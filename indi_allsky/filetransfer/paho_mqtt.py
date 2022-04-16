from .generic import GenericFileTransfer
#from .exceptions import AuthenticationFailure
#from .exceptions import ConnectionFailure
#from .exceptions import TransferFailure

from pathlib import Path
import paho.mqtt.publish as publish
#import ssl
import io
import time
import logging

logger = logging.getLogger('indi_allsky')


class paho_mqtt(GenericFileTransfer):
    def __init__(self, *args, **kwargs):
        super(paho_mqtt, self).__init__(*args, **kwargs)

        self._port = 1883

        self.mq_hostname = None
        self.mq_auth = None
        self.mq_tls = None


    def connect(self, *args, **kwargs):
        super(paho_mqtt, self).connect(*args, **kwargs)

        hostname = kwargs['hostname']
        username = kwargs['username']
        password = kwargs.get('password') if kwargs.get('password') else None
        tls = kwargs.get('tls')


        self.mq_hostname = hostname

        if tls:
            self.mq_tls = {
                'ca_certs'    : '/etc/ssl/certs/ca-certificates.crt',
                #'cert_reqs'   : ssl.CERT_NONE,
                #'tls_version' : ssl.PROTOCOL_TLS_CLIENT,
                'insecure'    : True,
            }

        if username:
            self.mq_auth = {
                'username' : username,
                'password' : password,
            }


        #except paramiko.ssh_exception.AuthenticationException as e:
        #    raise AuthenticationFailure(str(e)) from e
        #except paramiko.ssh_exception.NoValidConnectionsError as e:
        #    raise ConnectionFailure(str(e)) from e
        #except socket.gaierror as e:
        #    raise ConnectionFailure(str(e)) from e
        #except socket.timeout as e:
        #    raise ConnectionFailure(str(e)) from e

        return


    def close(self):
        super(paho_mqtt, self).close()


    def put(self, *args, **kwargs):
        super(paho_mqtt, self).put(*args, **kwargs)

        local_file = kwargs['local_file']
        base_topic = kwargs['base_topic']
        mq_data = kwargs['mq_data']

        local_file_p = Path(local_file)


        message_list = list()

        # publish image
        with io.open(local_file_p, 'rb') as f_localfile:
            image_data = f_localfile.read()
            message_list.append({
                'topic'    : '/'.join((base_topic, 'latest')),
                'payload'  : bytearray(image_data),
                'qos'      : 0,
                'retain'   : True,
            })


        message_list.append({
            'topic'    : '/'.join((base_topic, 'sqm')),
            'payload'  : mq_data['sqm'],
            'qos'      : 0,
            'retain'   : True,
        })
        message_list.append({
            'topic'    : '/'.join((base_topic, 'stars')),
            'payload'  : mq_data['stars'],
            'qos'      : 0,
            'retain'   : True,
        })


        start = time.time()

        publish.multiple(
            message_list,
            hostname=self.mq_hostname,
            port=self._port,
            client_id='',
            keepalive=60,
            auth=self.mq_auth,
            tls=self.mq_tls,
        )

        upload_elapsed_s = time.time() - start
        local_file_size = local_file_p.stat().st_size
        logger.info('File transferred in %0.4f s (%0.2f kB/s)', upload_elapsed_s, local_file_size / upload_elapsed_s / 1024)


