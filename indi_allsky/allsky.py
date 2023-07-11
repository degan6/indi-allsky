import platform
import sys
import os
import time
import io
import re
import psutil
from pathlib import Path
from datetime import datetime
from datetime import timedelta
#from pprint import pformat
import signal
import logging

import queue
from multiprocessing import Queue
from multiprocessing import Value

from .version import __version__
from .version import __config_level__

from .config import IndiAllSkyConfig

#from . import constants

from .capture import CaptureWorker
from .image import ImageWorker
from .video import VideoWorker
from .uploader import FileUploader

from .exceptions import TimeOutException

from .flask import create_app
from .flask import db
from .flask.miscDb import miscDb

from .flask.models import TaskQueueQueue
from .flask.models import TaskQueueState
from .flask.models import NotificationCategory

from .flask.models import IndiAllSkyDbCameraTable
from .flask.models import IndiAllSkyDbImageTable
from .flask.models import IndiAllSkyDbDarkFrameTable
from .flask.models import IndiAllSkyDbBadPixelMapTable
from .flask.models import IndiAllSkyDbVideoTable
from .flask.models import IndiAllSkyDbKeogramTable
from .flask.models import IndiAllSkyDbStarTrailsTable
from .flask.models import IndiAllSkyDbStarTrailsVideoTable
from .flask.models import IndiAllSkyDbTaskQueueTable

from sqlalchemy import or_
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.exc import IntegrityError


app = create_app()

logger = logging.getLogger('indi_allsky')


class IndiAllSky(object):

    periodic_tasks_offset = 900  # 15 minutes
    cleanup_tasks_offset = 43200  # 12 hours


    def __init__(self):
        self.name = 'Main'

        with app.app_context():
            try:
                self._config_obj = IndiAllSkyConfig()
                #logger.info('Loaded config id: %d', self._config_obj.config_id)
            except NoResultFound:
                logger.error('No config file found, please import a config')
                sys.exit(1)

            self.config = self._config_obj.config


        self._miscDb = miscDb(self.config)


        if __config_level__ != self._config_obj.config_level:
            logger.error('indi-allsky version does not match config, please rerun setup.sh')

            with app.app_context():
                self._miscDb.addNotification(
                    NotificationCategory.STATE,
                    'config_version',
                    'WARNING: indi-allsky version does not match config, please rerun setup.sh',
                    expire=timedelta(hours=2),
                )

            sys.exit(1)


        with app.app_context():
            self._miscDb.setState('CONFIG_ID', self._config_obj.config_id)

        self._pid_file = Path('/var/lib/indi-allsky/indi-allsky.pid')


        self.periodic_tasks_time = time.time() + self.periodic_tasks_offset
        self.cleanup_tasks_time = time.time()  # run immediately


        self.latitude_v = Value('f', float(self.config['LOCATION_LATITUDE']))
        self.longitude_v = Value('f', float(self.config['LOCATION_LONGITUDE']))

        self.ra_v = Value('f', 0.0)
        self.dec_v = Value('f', 0.0)

        self.exposure_v = Value('f', -1.0)
        self.gain_v = Value('i', -1)  # value set in CCD config
        self.bin_v = Value('i', 1)  # set 1 for sane default
        self.sensortemp_v = Value('f', 0)
        self.night_v = Value('i', -1)  # bogus initial value
        self.moonmode_v = Value('i', -1)  # bogus initial value


        self.capture_q = Queue()
        self.capture_error_q = Queue()
        self.capture_worker = None
        self.capture_worker_idx = 0

        self.image_q = Queue()
        self.image_error_q = Queue()
        self.image_worker = None
        self.image_worker_idx = 0

        self.video_q = Queue()
        self.video_error_q = Queue()
        self.video_worker = None
        self.video_worker_idx = 0

        self.upload_q = Queue()
        self.upload_worker_list = []
        self.upload_worker_idx = 0

        for x in range(self.config.get('UPLOAD_WORKERS', 1)):
            self.upload_worker_list.append({
                'worker'  : None,
                'error_q' : Queue(),
            })


        if self.config['IMAGE_FOLDER']:
            self.image_dir = Path(self.config['IMAGE_FOLDER']).absolute()
        else:
            self.image_dir = Path(__file__).parent.parent.joinpath('html', 'images').absolute()


        self.generate_timelapse_flag = False   # This is updated once images have been generated


        self._reload = False
        self._shutdown = False
        self._terminate = False

        signal.signal(signal.SIGALRM, self.sigalarm_handler_main)
        signal.signal(signal.SIGHUP, self.sighup_handler_main)
        signal.signal(signal.SIGTERM, self.sigterm_handler_main)
        signal.signal(signal.SIGINT, self.sigint_handler_main)



    @property
    def pid_file(self):
        return self._pid_file

    @pid_file.setter
    def pid_file(self, new_pid_file):
        self._pid_file = Path(new_pid_file)


    def sighup_handler_main(self, signum, frame):
        logger.warning('Caught HUP signal')

        self._reload = True


    def sigterm_handler_main(self, signum, frame):
        logger.warning('Caught TERM signal, shutting down')

        # set flag for program to stop processes
        self._shutdown = True
        self._terminate = True


    def sigint_handler_main(self, signum, frame):
        logger.warning('Caught INT signal, shutting down')

        # set flag for program to stop processes
        self._shutdown = True


    def sigalarm_handler_main(self, signum, frame):
        raise TimeOutException()


    def write_pid(self):
        pid = os.getpid()

        try:
            with io.open(str(self.pid_file), 'w') as pid_f:
                pid_f.write('{0:d}'.format(pid))
        except PermissionError as e:
            logger.error('Unable to write pid file: %s', str(e))
            sys.exit(1)


        self.pid_file.chmod(0o644)

        self._miscDb.setState('PID', pid)
        self._miscDb.setState('PID_FILE', self.pid_file)


    def _startup(self):
        logger.info('indi-allsky release: %s', str(__version__))
        logger.info('indi-allsky config level: %s', str(__config_level__))

        logger.info('Python version: %s', platform.python_version())
        logger.info('Platform: %s', platform.machine())

        logger.info('System CPUs: %d', psutil.cpu_count())

        memory_info = psutil.virtual_memory()
        memory_total_mb = int(memory_info[0] / 1024.0 / 1024.0)

        logger.info('System memory: %d MB', memory_total_mb)

        uptime_s = time.time() - psutil.boot_time()
        logger.info('System uptime: %ds', uptime_s)

        #logger.info('Temp dir: %s', tempfile.gettempdir())


    def _startCaptureWorker(self):
        if self.capture_worker:
            if self.capture_worker.is_alive():
                return

            try:
                capture_error, capture_traceback = self.capture_error_q.get_nowait()
                for line in capture_traceback.split('\n'):
                    logger.error('Capture worker exception: %s', line)
            except queue.Empty:
                pass


        self.capture_worker_idx += 1

        logger.info('Starting Capture%03d worker', self.capture_worker_idx)
        self.capture_worker = CaptureWorker(
            self.capture_worker_idx,
            self.config,
            self.capture_error_q,
            self.capture_q,
            self.image_q,
            self.video_q,
            self.upload_q,
            self.latitude_v,
            self.longitude_v,
            self.ra_v,
            self.dec_v,
            self.exposure_v,
            self.gain_v,
            self.bin_v,
            self.sensortemp_v,
            self.night_v,
            self.moonmode_v,
        )
        self.capture_worker.start()


    def _stopCaptureWorker(self, terminate=False):
        if not self.capture_worker:
            return

        if not self.capture_worker.is_alive():
            return

        if terminate:
            logger.info('Terminating Capture worker')
            self.capture_worker.terminate()

        logger.info('Stopping Capture worker')

        self.capture_q.put({'stop' : True})
        self.capture_worker.join()


    def _reloadCaptureWorker(self):
        if not self.capture_worker:
            return

        if not self.capture_worker.is_alive():
            return

        self.capture_q.put({'reload' : True})


    def _startImageWorker(self):
        if self.image_worker:
            if self.image_worker.is_alive():
                return

            try:
                image_error, image_traceback = self.image_error_q.get_nowait()
                for line in image_traceback.split('\n'):
                    logger.error('Image worker exception: %s', line)
            except queue.Empty:
                pass


        self.image_worker_idx += 1

        logger.info('Starting Image%03d worker', self.image_worker_idx)
        self.image_worker = ImageWorker(
            self.image_worker_idx,
            self.config,
            self.image_error_q,
            self.image_q,
            self.upload_q,
            self.latitude_v,
            self.longitude_v,
            self.ra_v,
            self.dec_v,
            self.exposure_v,
            self.gain_v,
            self.bin_v,
            self.sensortemp_v,
            self.night_v,
            self.moonmode_v,
        )
        self.image_worker.start()


        if self.image_worker_idx % 10 == 0:
            # notify if worker is restarted more than 10 times
            with app.app_context():
                self._miscDb.addNotification(
                    NotificationCategory.WORKER,
                    'ImageWorker',
                    'WARNING: Image worker was restarted more than 10 times',
                    expire=timedelta(hours=2),
                )


    def _stopImageWorker(self, terminate=False):
        if not self.image_worker:
            return

        if not self.image_worker.is_alive():
            return

        if terminate:
            logger.info('Terminating Image worker')
            self.image_worker.terminate()

        logger.info('Stopping Image worker')

        self.image_q.put({'stop' : True})
        self.image_worker.join()


    def _startVideoWorker(self):
        if self.video_worker:
            if self.video_worker.is_alive():
                return


            try:
                video_error, video_traceback = self.video_error_q.get_nowait()
                for line in video_traceback.split('\n'):
                    logger.error('Video worker exception: %s', line)
            except queue.Empty:
                pass


        self.video_worker_idx += 1

        logger.info('Starting Video%03d worker', self.video_worker_idx)
        self.video_worker = VideoWorker(
            self.video_worker_idx,
            self.config,
            self.video_error_q,
            self.video_q,
            self.upload_q,
            self.latitude_v,
            self.longitude_v,
            self.bin_v,
        )
        self.video_worker.start()


        if self.video_worker_idx % 10 == 0:
            # notify if worker is restarted more than 10 times
            with app.app_context():
                self._miscDb.addNotification(
                    NotificationCategory.WORKER,
                    'VideoWorker',
                    'WARNING: VideoWorker was restarted more than 10 times',
                    expire=timedelta(hours=2),
                )


    def _stopVideoWorker(self, terminate=False):
        if not self.video_worker:
            return

        if not self.video_worker.is_alive():
            return

        if terminate:
            logger.info('Terminating Video worker')
            self.video_worker.terminate()

        logger.info('Stopping Video worker')

        self.video_q.put({'stop' : True})
        self.video_worker.join()


    def _startFileUploadWorkers(self):
        for upload_worker_dict in self.upload_worker_list:
            self._fileUploadWorkerStart(upload_worker_dict)


    def _fileUploadWorkerStart(self, uw_dict):
        if uw_dict['worker']:
            if uw_dict['worker'].is_alive():
                return


            try:
                upload_error, upload_traceback = uw_dict['error_q'].get_nowait()
                for line in upload_traceback.split('\n'):
                    logger.error('Upload worker exception: %s', line)
            except queue.Empty:
                pass


        self.upload_worker_idx += 1

        logger.info('Starting Upload%03d worker', self.upload_worker_idx)
        uw_dict['worker'] = FileUploader(
            self.upload_worker_idx,
            self.config,
            uw_dict['error_q'],
            self.upload_q,
        )

        uw_dict['worker'].start()


        if self.upload_worker_idx % 10 == 0:
            # notify if worker is restarted more than 10 times
            with app.app_context():
                self._miscDb.addNotification(
                    NotificationCategory.WORKER,
                    'FileUploader',
                    'WARNING: Upload worker was restarted more than 10 times',
                    expire=timedelta(hours=2),
                )


    def _stopFileUploadWorkers(self, terminate=False):
        active_worker_list = list()
        for upload_worker_dict in self.upload_worker_list:
            if not upload_worker_dict['worker']:
                continue

            if not upload_worker_dict['worker'].is_alive():
                continue

            active_worker_list.append(upload_worker_dict)

            # need to put the stops in the queue before waiting on workers to join
            self.upload_q.put({'stop' : True})


        for upload_worker_dict in active_worker_list:
            self._fileUploadWorkerStop(upload_worker_dict)


    def _fileUploadWorkerStop(self, uw_dict):
        logger.info('Stopping Upload worker')

        uw_dict['worker'].join()


    def run(self):
        with app.app_context():
            self.write_pid()

            self._expireOrphanedTasks()

            self._startup()




        while True:
            if self._shutdown:
                logger.warning('Shutting down')
                self._stopImageWorker(terminate=self._terminate)
                self._stopVideoWorker(terminate=self._terminate)
                self._stopFileUploadWorkers(terminate=self._terminate)
                self._stopCaptureWorker(terminate=self._terminate)  # do this last


                with app.app_context():
                    self._miscDb.addNotification(
                        NotificationCategory.STATE,
                        'indi-allsky',
                        'indi-allsky was shutdown',
                        expire=timedelta(hours=1),
                    )


                sys.exit()


            if self._reload:
                logger.warning('Restarting processes')
                self._reload = False
                self._stopImageWorker()
                self._stopVideoWorker()
                self._stopFileUploadWorkers()
                self._stopCaptureWorker()
                # processes will start at the next loop


            # do *NOT* start workers inside of a flask context
            # doing so will cause TLS/SSL problems connecting to databases

            # restart worker if it has failed
            self._startCaptureWorker()
            self._startImageWorker()
            self._startVideoWorker()
            self._startFileUploadWorkers()


            # Queue externally defined tasks
            with app.app_context():
                self._queueManualTasks()
                self._periodic_tasks()


            time.sleep(15)


    def _systemHealthCheck(self, task_state=TaskQueueState.QUEUED):
        # This will delete old images from the filesystem and DB
        jobdata = {
            'action'       : 'systemHealthCheck',
            'img_folder'   : str(self.image_dir),  # not needed
            'timespec'     : None,  # Not needed
            'night'        : None,  # Not needed
            'camera_id'    : None,  # Not needed
        }

        task = IndiAllSkyDbTaskQueueTable(
            queue=TaskQueueQueue.VIDEO,
            state=task_state,
            data=jobdata,
        )
        db.session.add(task)
        db.session.commit()

        self.video_q.put({'task_id' : task.id})


    def dbImportImages(self):
        with app.app_context():
            self._dbImportImages()


    def _dbImportImages(self):
        try:
            IndiAllSkyDbCameraTable.query\
                .limit(1)\
                .one()

            logger.error('Imports may only be performed before the first camera is connected')
            sys.exit(1)

        except NoResultFound:
            camera = self._miscDb.addCamera('Import camera', None)
            camera_id = camera.id


        file_list_darks = list()
        self._getFolderFilesByExt(self.image_dir.joinpath('darks'), file_list_darks, extension_list=['fit', 'fits'])


        ### Dark frames
        file_list_darkframes = filter(lambda p: 'dark' in p.name, file_list_darks)

        #/var/www/html/allsky/images/darks/dark_ccd1_8bit_6s_gain250_bin1_10c_20210826_020202.fit
        re_darkframe = re.compile(r'\/dark_ccd(?P<ccd_id_str>\d+)_(?P<bitdepth_str>\d+)bit_(?P<exposure_str>\d+)s_gain(?P<gain_str>\d+)_bin(?P<binmode_str>\d+)_(?P<ccdtemp_str>\-?\d+)c_(?P<createDate_str>[0-9_]+)\.[a-z]+$')

        darkframe_entries = list()
        for f in file_list_darkframes:
            #logger.info('Raw frame: %s', f)

            m = re.search(re_darkframe, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue


            #logger.info('CCD ID string: %s', m.group('ccd_id_str'))
            #logger.info('Exposure string: %s', m.group('exposure_str'))
            #logger.info('Bitdepth string: %s', m.group('bitdepth_str'))
            #logger.info('Gain string: %s', m.group('gain_str'))
            #logger.info('Binmode string: %s', m.group('binmode_str'))
            #logger.info('Ccd temp string: %s', m.group('ccdtemp_str'))

            ccd_id = int(m.group('ccd_id_str'))
            exposure = int(m.group('exposure_str'))
            bitdepth = int(m.group('bitdepth_str'))
            gain = int(m.group('gain_str'))
            binmode = int(m.group('binmode_str'))
            ccdtemp = float(m.group('ccdtemp_str'))


            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)

            darkframe_dict = {
                'filename'   : str(f),
                'createDate' : d_createDate,
                'bitdepth'   : bitdepth,
                'exposure'   : exposure,
                'gain'       : gain,
                'binmode'    : binmode,
                'camera_id'  : ccd_id,
                'temp'       : ccdtemp,
            }

            darkframe_entries.append(darkframe_dict)


        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbDarkFrameTable, darkframe_entries)
            db.session.commit()

            logger.warning('*** Dark frames inserted: %d ***', len(darkframe_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()


        file_list_videos = list()
        self._getFolderFilesByExt(self.image_dir, file_list_videos, extension_list=['mp4', 'webm'])


        ### Bad pixel maps
        file_list_bpm = filter(lambda p: 'bpm' in p.name, file_list_darks)

        #/var/www/html/allsky/images/darks/bpm_ccd1_8bit_6s_gain250_bin1_10c_20210826_020202.fit
        re_bpm = re.compile(r'\/bpm_ccd(?P<ccd_id_str>\d+)_(?P<bitdepth_str>\d+)bit_(?P<exposure_str>\d+)s_gain(?P<gain_str>\d+)_bin(?P<binmode_str>\d+)_(?P<ccdtemp_str>\-?\d+)c_(?P<createDate_str>[0-9_]+)\.[a-z]+$')

        bpm_entries = list()
        for f in file_list_bpm:
            #logger.info('Raw frame: %s', f)

            m = re.search(re_bpm, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue


            #logger.info('CCD ID string: %s', m.group('ccd_id_str'))
            #logger.info('Exposure string: %s', m.group('exposure_str'))
            #logger.info('Bitdepth string: %s', m.group('bitdepth_str'))
            #logger.info('Gain string: %s', m.group('gain_str'))
            #logger.info('Binmode string: %s', m.group('binmode_str'))
            #logger.info('Ccd temp string: %s', m.group('ccdtemp_str'))

            ccd_id = int(m.group('ccd_id_str'))
            exposure = int(m.group('exposure_str'))
            bitdepth = int(m.group('bitdepth_str'))
            gain = int(m.group('gain_str'))
            binmode = int(m.group('binmode_str'))
            ccdtemp = float(m.group('ccdtemp_str'))


            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)

            bpm_dict = {
                'filename'   : str(f),
                'createDate' : d_createDate,
                'bitdepth'   : bitdepth,
                'exposure'   : exposure,
                'gain'       : gain,
                'binmode'    : binmode,
                'camera_id'  : ccd_id,
                'temp'       : ccdtemp,
            }

            bpm_entries.append(bpm_dict)


        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbBadPixelMapTable, bpm_entries)
            db.session.commit()

            logger.warning('*** Bad pixel maps inserted: %d ***', len(bpm_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()



        ### Timelapse
        timelapse_videos_tl = filter(lambda p: 'timelapse' in p.name, file_list_videos)
        timelapse_videos = filter(lambda p: 'startrail' not in p.name, timelapse_videos_tl)  # exclude star trail timelapses

        #/var/www/html/allsky/images/20210915/allsky-timelapse_ccd1_20210915_night.mp4
        re_video = re.compile(r'(?P<dayDate_str>\d{8})\/.+timelapse_ccd(?P<ccd_id_str>\d+)_\d{8}_(?P<timeofday_str>[a-z]+)\.[a-z0-9]+$')

        video_entries = list()
        for f in timelapse_videos:
            #logger.info('Timelapse: %s', f)

            m = re.search(re_video, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue

            #logger.info('dayDate string: %s', m.group('dayDate_str'))
            #logger.info('Time of day string: %s', m.group('timeofday_str'))

            d_dayDate = datetime.strptime(m.group('dayDate_str'), '%Y%m%d').date()
            #logger.info('dayDate: %s', str(d_dayDate))

            if m.group('timeofday_str') == 'night':
                night = True
            else:
                night = False

            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)

            video_dict = {
                'filename'   : str(f),
                'createDate' : d_createDate,
                'dayDate'    : d_dayDate,
                'night'      : night,
                'uploaded'   : False,
                'camera_id'  : camera_id,
            }

            video_entries.append(video_dict)


        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbVideoTable, video_entries)
            db.session.commit()

            logger.warning('*** Timelapse videos inserted: %d ***', len(video_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()



        ### find all imaegs
        file_list_images = list()
        self._getFolderFilesByExt(self.image_dir, file_list_images, extension_list=['jpg', 'jpeg', 'png', 'tif', 'tiff', 'webp'])


        ### Keograms
        file_list_keograms = filter(lambda p: 'keogram' in p.name, file_list_images)

        #/var/www/html/allsky/images/20210915/allsky-keogram_ccd1_20210915_night.jpg
        re_keogram = re.compile(r'(?P<dayDate_str>\d{8})\/.+keogram_ccd(?P<ccd_id_str>\d+)_\d{8}_(?P<timeofday_str>[a-z]+)\.[a-z]+$')

        keogram_entries = list()
        for f in file_list_keograms:
            #logger.info('Keogram: %s', f)

            m = re.search(re_keogram, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue

            #logger.info('dayDate string: %s', m.group('dayDate_str'))
            #logger.info('Time of day string: %s', m.group('timeofday_str'))

            d_dayDate = datetime.strptime(m.group('dayDate_str'), '%Y%m%d').date()
            #logger.info('dayDate: %s', str(d_dayDate))

            if m.group('timeofday_str') == 'night':
                night = True
            else:
                night = False

            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)

            keogram_dict = {
                'filename'   : str(f),
                'createDate' : d_createDate,
                'dayDate'    : d_dayDate,
                'night'      : night,
                'uploaded'   : False,
                'camera_id'  : camera_id,
            }

            keogram_entries.append(keogram_dict)


        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbKeogramTable, keogram_entries)
            db.session.commit()

            logger.warning('*** Keograms inserted: %d ***', len(keogram_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()


        ### Star trails
        file_list_startrail = filter(lambda p: 'startrail' in p.name, file_list_images)

        #/var/www/html/allsky/images/20210915/allsky-startrail_ccd1_20210915_night.jpg
        re_startrail = re.compile(r'(?P<dayDate_str>\d{8})\/.+startrail_ccd(?P<ccd_id_str>\d+)_\d{8}_(?P<timeofday_str>[a-z]+)\.[a-z]+$')

        startrail_entries = list()
        for f in file_list_startrail:
            #logger.info('Star trail: %s', f)

            m = re.search(re_startrail, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue

            #logger.info('dayDate string: %s', m.group('dayDate_str'))
            #logger.info('Time of day string: %s', m.group('timeofday_str'))

            d_dayDate = datetime.strptime(m.group('dayDate_str'), '%Y%m%d').date()
            #logger.info('dayDate: %s', str(d_dayDate))

            if m.group('timeofday_str') == 'night':
                night = True
            else:
                night = False

            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)

            startrail_dict = {
                'filename'   : str(f),
                'createDate' : d_createDate,
                'dayDate'    : d_dayDate,
                'night'      : night,
                'uploaded'   : False,
                'camera_id'  : camera_id,
            }

            startrail_entries.append(startrail_dict)


        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbStarTrailsTable, startrail_entries)
            db.session.commit()

            logger.warning('*** Star trails inserted: %d ***', len(startrail_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()


        ### Star trail Videos
        file_list_startrail_video_tl = filter(lambda p: 'timelapse' in p.name, file_list_videos)
        file_list_startrail_video = filter(lambda p: 'startrail' in p.name, file_list_startrail_video_tl)

        #/var/www/html/allsky/images/20210915/allsky-startrail_timelapse_ccd1_20210915_night.mp4
        re_startrail_video = re.compile(r'(?P<dayDate_str>\d{8})\/.+startrail_timelapse_ccd(?P<ccd_id_str>\d+)_\d{8}_(?P<timeofday_str>[a-z]+)\.[a-z0-9]+$')

        startrail_video_entries = list()
        for f in file_list_startrail_video:
            #logger.info('Star trail timelapse: %s', f)

            m = re.search(re_startrail_video, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue

            #logger.info('dayDate string: %s', m.group('dayDate_str'))
            #logger.info('Time of day string: %s', m.group('timeofday_str'))

            d_dayDate = datetime.strptime(m.group('dayDate_str'), '%Y%m%d').date()
            #logger.info('dayDate: %s', str(d_dayDate))

            if m.group('timeofday_str') == 'night':
                night = True
            else:
                night = False

            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)

            startrail_video_dict = {
                'filename'   : str(f),
                'createDate' : d_createDate,
                'dayDate'    : d_dayDate,
                'night'      : night,
                'uploaded'   : False,
                'camera_id'  : camera_id,
            }

            startrail_video_entries.append(startrail_video_dict)


        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbStarTrailsVideoTable, startrail_video_entries)
            db.session.commit()

            logger.warning('*** Star trail timelapses inserted: %d ***', len(startrail_video_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()


        ### Images
        # Exclude keograms and star trails
        file_list_images_nok = filter(lambda p: 'keogram' not in p.name, file_list_images)
        file_list_images_nok_nost = filter(lambda p: 'startrail' not in p.name, file_list_images_nok)

        #/var/www/html/allsky/images/20210825/night/26_02/ccd1_20210826_020202.jpg
        re_image = re.compile(r'(?P<dayDate_str>\d{8})\/(?P<timeofday_str>[a-z]+)\/\d{2}_\d{2}\/ccd(?P<ccd_id_str>\d+)_(?P<createDate_str>[0-9_]+)\.[a-z]+$')

        image_entries = list()
        for f in file_list_images_nok_nost:
            #logger.info('Image: %s', f)

            m = re.search(re_image, str(f))
            if not m:
                logger.error('Regex did not match file: %s', f)
                continue

            #logger.info('dayDate string: %s', m.group('dayDate_str'))
            #logger.info('Time of day string: %s', m.group('timeofday_str'))
            #logger.info('createDate string: %s', m.group('createDate_str'))

            d_dayDate = datetime.strptime(m.group('dayDate_str'), '%Y%m%d').date()
            #logger.info('dayDate: %s', str(d_dayDate))

            if m.group('timeofday_str') == 'night':
                night = True
            else:
                night = False

            #d_createDate = datetime.strptime(m.group('createDate_str'), '%Y%m%d_%H%M%S')
            d_createDate = datetime.fromtimestamp(f.stat().st_mtime)
            #logger.info('createDate: %s', str(d_createDate))


            image_dict = {
                'filename'   : str(f),
                'camera_id'  : camera_id,
                'createDate' : d_createDate,
                'dayDate'    : d_dayDate,
                'exposure'   : 0.0,
                'gain'       : -1,
                'binmode'    : 1,
                'night'      : night,
                'adu'        : 0.0,
                'stable'     : True,
                'moonmode'   : False,
                'adu_roi'    : False,
                'uploaded'   : False,
            }


            image_entries.append(image_dict)

        try:
            db.session.bulk_insert_mappings(IndiAllSkyDbImageTable, image_entries)
            db.session.commit()

            logger.warning('*** Images inserted: %d ***', len(image_entries))
        except IntegrityError as e:
            logger.warning('Integrity error: %s', str(e))
            db.session.rollback()


    def _getFolderFilesByExt(self, folder, file_list, extension_list=None):
        if not extension_list:
            extension_list = [self.config['IMAGE_FILE_TYPE']]

        #logger.info('Searching for image files in %s', folder)

        dot_extension_list = ['.{0:s}'.format(e) for e in extension_list]

        for item in Path(folder).iterdir():
            if item.is_file() and item.suffix in dot_extension_list:
                file_list.append(item)
            elif item.is_dir():
                self.getFolderFilesByExt(item, file_list, extension_list=extension_list)  # recursion


    def _expireOrphanedTasks(self):
        orphaned_statuses = (
            TaskQueueState.MANUAL,
            TaskQueueState.QUEUED,
            TaskQueueState.RUNNING,
        )

        old_task_list = IndiAllSkyDbTaskQueueTable.query\
            .filter(IndiAllSkyDbTaskQueueTable.state.in_(orphaned_statuses))

        for task in old_task_list:
            logger.warning('Expiring orphaned task %d', task.id)
            task.state = TaskQueueState.EXPIRED

        db.session.commit()


    def _flushOldTasks(self):
        now_minus_3d = datetime.now() - timedelta(days=3)

        flush_old_tasks = IndiAllSkyDbTaskQueueTable.query\
            .filter(IndiAllSkyDbTaskQueueTable.createDate < now_minus_3d)

        logger.warning('Found %d expired tasks to delete', flush_old_tasks.count())
        flush_old_tasks.delete()
        db.session.commit()


    def _queueManualTasks(self):
        logger.info('Checking for manually submitted tasks')
        manual_tasks = IndiAllSkyDbTaskQueueTable.query\
            .filter(IndiAllSkyDbTaskQueueTable.state == TaskQueueState.MANUAL)\
            .filter(
                or_(
                    IndiAllSkyDbTaskQueueTable.queue == TaskQueueQueue.MAIN,
                    IndiAllSkyDbTaskQueueTable.queue == TaskQueueQueue.VIDEO,
                )
            )\
            .order_by(IndiAllSkyDbTaskQueueTable.createDate.asc())


        reload_received = False

        for task in manual_tasks:
            if task.queue == TaskQueueQueue.VIDEO:
                logger.info('Queuing manual task %d', task.id)
                task.setQueued()
                self.video_q.put({'task_id' : task.id})

            elif task.queue == TaskQueueQueue.MAIN:
                logger.info('Picked up MAIN task')

                action = task.data['action']

                if action == 'reload':
                    if reload_received:
                        logger.warning('Skipping duplicate reload signal')
                        task.setExpired()
                        continue

                    reload_received = True
                    os.kill(os.getpid(), signal.SIGHUP)

                    task.setSuccess('Reloaded indi-allsky process')

                elif action == 'settime':
                    self.update_time_offset = task.data['time_offset']
                    logger.info('Set time offset: %ds', int(self.update_time_offset))

                    self.capture_q.put({
                        'settime' : int(self.update_time_offset),
                    })

                    task.setSuccess('Set time queued')

                else:
                    logger.error('Unknown action: %s', action)
                    task.setFailed()

            else:
                logger.error('Unmanaged queue %s', task.queue.name)
                task.setFailed()


    def _periodic_tasks(self):

        # Tasks that need to be run periodically
        now = time.time()

        if self.periodic_tasks_time > now:
            return

        # set next reconfigure time
        self.periodic_tasks_time = now + self.periodic_tasks_offset

        logger.warning('Periodic tasks triggered')


        # cleanup data
        if self.cleanup_tasks_time < now:
            self.cleanup_tasks_time = now + self.cleanup_tasks_offset

            self._flushOldTasks()
            self._systemHealthCheck()



