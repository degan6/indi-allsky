import time
import traceback
import logging

from threading import Thread
#import queue
import threading

from .devices import dew_heaters
from .devices import fans
from .devices import sensors as indi_allsky_sensors
from .devices.exceptions import SensorReadException

logger = logging.getLogger('indi_allsky')


class SensorWorker(Thread):
    def __init__(
        self,
        idx,
        config,
        error_q,
        sensors_user_av,
        night_v,
    ):
        super(SensorWorker, self).__init__()

        self.name = 'Sensor-{0:d}'.format(idx)

        self.config = config
        self.error_q = error_q

        self.sensors_user_av = sensors_user_av
        self.night_v = night_v
        self.night = False

        self.dew_heater = None
        self.fan = None
        self.sensors = [None, None, None]

        self.next_run = time.time()  # run immediately
        self.next_run_offset = 59

        # dew heater
        self.dh_temp_user_slot = self.config.get('DEW_HEATER', {}).get('TEMP_USER_VAR_SLOT', 10)

        self.dh_level_default = self.config.get('DEW_HEATER', {}).get('LEVEL_DEF', 100)
        self.dh_level_low = self.config.get('DEW_HEATER', {}).get('LEVEL_LOW', 33)
        self.dh_level_med = self.config.get('DEW_HEATER', {}).get('LEVEL_MED', 66)
        self.dh_level_high = self.config.get('DEW_HEATER', {}).get('LEVEL_HIGH', 100)

        self.dh_thold_diff_low = self.config.get('DEW_HEATER', {}).get('THOLD_DIFF_LOW', 15)
        self.dh_thold_diff_med = self.config.get('DEW_HEATER', {}).get('THOLD_DIFF_MED', 10)
        self.dh_thold_diff_high = self.config.get('DEW_HEATER', {}).get('THOLD_DIFF_HIGH', 5)

        # fan
        self.fan_target = self.config.get('FAN', {}).get('TARGET', 30.0)
        self.fan_temp_user_slot = self.config.get('FAN', {}).get('TEMP_USER_VAR_SLOT', 10)

        self.fan_level_default = self.config.get('FAN', {}).get('LEVEL_DEF', 100)
        self.fan_level_low = self.config.get('FAN', {}).get('LEVEL_LOW', 33)
        self.fan_level_med = self.config.get('FAN', {}).get('LEVEL_MED', 66)
        self.fan_level_high = self.config.get('FAN', {}).get('LEVEL_HIGH', 100)

        self.fan_thold_diff_low = self.config.get('FAN', {}).get('THOLD_DIFF_LOW', 0)
        self.fan_thold_diff_med = self.config.get('FAN', {}).get('THOLD_DIFF_MED', 5)
        self.fan_thold_diff_high = self.config.get('FAN', {}).get('THOLD_DIFF_HIGH', 10)

        self._stopper = threading.Event()


    def stop(self):
        self._stopper.set()


    def stopped(self):
        return self._stopper.isSet()


    def run(self):
        # setup signal handling after detaching from the main process
        #signal.signal(signal.SIGHUP, self.sighup_handler_worker)
        #signal.signal(signal.SIGTERM, self.sigterm_handler_worker)
        #signal.signal(signal.SIGINT, self.sigint_handler_worker)
        #signal.signal(signal.SIGALRM, self.sigalarm_handler_worker)


        ### use this as a method to log uncaught exceptions
        try:
            self.saferun()
        except Exception as e:
            tb = traceback.format_exc()
            self.error_q.put((str(e), tb))
            raise e


    def saferun(self):
        #raise Exception('Test exception handling in worker')


        self.init_dew_heater()
        self.init_fan()
        self.init_sensors()


        while True:
            time.sleep(3)

            if self.stopped():
                logger.warning('Goodbye')
                return


            now = time.time()
            if not now >= self.next_run:
                continue


            # set next run
            self.next_run = now + self.next_run_offset

            #############################
            ### do interesting stuff here
            #############################


            if self.night != bool(self.night_v.value):
                self.night = bool(self.night_v.value)
                self.night_day_change()


            if self.sensors_user_av[2]:
                logger.info('Dew Point: %0.1f, Frost Point: %0.1f', self.sensors_user_av[2], self.sensors_user_av[3])


            # update sensor readings
            for sensor in self.sensors:
                try:
                    sensor_data = sensor.update()

                    with self.sensors_user_av.get_lock():
                        if sensor_data.get('dew_point'):
                            self.sensors_user_av[2] = float(sensor_data['dew_point'])

                        if sensor_data.get('frost_point'):
                            self.sensors_user_av[3] = float(sensor_data['frost_point'])

                        for i, v in enumerate(sensor_data['data']):
                            self.sensors_user_av[sensor.slot + i] = float(v)
                except SensorReadException as e:
                    logger.error('SensorReadException: {0:s}'.format(str(e)))


            # dew heater threshold processing
            if not self.night and self.config.get('DEW_HEATER', {}).get('ENABLE_DAY'):
                ### day
                if self.config.get('DEW_HEATER', {}).get('THOLD_ENABLE'):
                    self.check_dew_heater_thresholds()
            else:
                ### night
                if self.config.get('DEW_HEATER', {}).get('THOLD_ENABLE'):
                    self.check_dew_heater_thresholds()


            # fan threshold processing
            if self.night and self.config.get('FAN', {}).get('ENABLE_NIGHT'):
                ### night
                if self.config.get('FAN', {}).get('THOLD_ENABLE'):
                    self.check_fan_thresholds()
            else:
                ### day
                if self.config.get('FAN', {}).get('THOLD_ENABLE'):
                    self.check_fan_thresholds()


    def night_day_change(self):
        logger.warning('Day/Night change')

        # changing modes here
        if self.night:
            ### night

            # dew heater
            if not self.dew_heater.state:
                self.set_dew_heater(self.dh_level_default)


            # fan
            if self.config.get('FAN', {}).get('ENABLE_NIGHT'):
                if not self.fan.state:
                    self.set_fan(self.fan_level_default)
            else:
                self.set_fan(0)

        else:
            ### day

            # dew heater
            if self.config.get('DEW_HEATER', {}).get('ENABLE_DAY'):
                if not self.dew_heater.state:
                    self.set_dew_heater(self.dh_level_default)
            else:
                self.set_dew_heater(0)


            # fan
            if not self.fan.state:
                self.set_fan(self.fan_level_default)


    def init_dew_heater(self):
        dew_heater_classname = self.config.get('DEW_HEATER', {}).get('CLASSNAME')
        if dew_heater_classname:
            dh_class = getattr(dew_heaters, dew_heater_classname)

            dh_pin_1 = self.config.get('DEW_HEATER', {}).get('PIN_1', 'notdefined')

            self.dew_heater = dh_class(self.config, pin_1_name=dh_pin_1)

            if self.night_v.value:
                ### night
                self.set_dew_heater(self.dh_level_default)
            else:
                ### day
                if self.config.get('DEW_HEATER', {}).get('ENABLE_DAY'):
                    self.set_dew_heater(self.dh_level_default)
                else:
                    self.set_dew_heater(0)


        else:
            self.dew_heater = dew_heaters.dew_heater_simulator(self.config)



    def set_dew_heater(self, new_state):
        if self.dew_heater.state != new_state:
            self.dew_heater.state = new_state

            with self.sensors_user_av.get_lock():
                self.sensors_user_av[1] = float(self.dew_heater.state)


    def init_fan(self):
        fan_classname = self.config.get('FAN', {}).get('CLASSNAME')
        if fan_classname:
            fan_class = getattr(fans, fan_classname)

            fan_pin_1 = self.config.get('FAN', {}).get('PIN_1', 'notdefined')

            self.fan = fan_class(self.config, pin_1_name=fan_pin_1)

            if not self.night_v.value:
                ### day
                self.set_fan(self.fan_level_default)
            else:
                ### night
                if self.config.get('FAN', {}).get('ENABLE_NIGHT'):
                    self.set_fan(self.fan_level_default)
                else:
                    self.set_fan(0)

        else:
            self.fan = fans.fan_simulator(self.config)


    def set_fan(self, new_state):
        if self.fan.state != new_state:
            self.fan.state = new_state

            with self.sensors_user_av.get_lock():
                self.sensors_user_av[4] = float(self.fan.state)


    def init_sensors(self):
        ### Sensor A
        a_sensor_classname = self.config.get('TEMP_SENSOR', {}).get('A_CLASSNAME')
        if a_sensor_classname:
            a_sensor = getattr(indi_allsky_sensors, a_sensor_classname)

            a_sensor_i2c_address = self.config.get('TEMP_SENSOR', {}).get('A_I2C_ADDRESS', '0x77')
            a_sensor_pin_1_name = self.config.get('TEMP_SENSOR', {}).get('A_PIN_1', 'notdefined')

            self.sensors[0] = a_sensor(self.config, pin_1_name=a_sensor_pin_1_name, i2c_address=a_sensor_i2c_address)
        else:
            self.sensors[0] = indi_allsky_sensors.sensor_simulator(self.config)

        self.sensors[0].slot = self.config.get('TEMP_SENSOR', {}).get('A_USER_VAR_SLOT', 10)


        ### Sensor B
        b_sensor_classname = self.config.get('TEMP_SENSOR', {}).get('B_CLASSNAME')
        if b_sensor_classname:
            b_sensor = getattr(indi_allsky_sensors, b_sensor_classname)

            b_sensor_i2c_address = self.config.get('TEMP_SENSOR', {}).get('B_I2C_ADDRESS', '0x76')
            b_sensor_pin_1_name = self.config.get('TEMP_SENSOR', {}).get('B_PIN_1', 'notdefined')

            self.sensors[1] = b_sensor(self.config, pin_1_name=b_sensor_pin_1_name, i2c_address=b_sensor_i2c_address)
        else:
            self.sensors[1] = indi_allsky_sensors.sensor_simulator(self.config)

        self.sensors[1].slot = self.config.get('TEMP_SENSOR', {}).get('B_USER_VAR_SLOT', 15)


        ### Sensor C
        c_sensor_classname = self.config.get('TEMP_SENSOR', {}).get('C_CLASSNAME')
        if c_sensor_classname:
            c_sensor = getattr(indi_allsky_sensors, c_sensor_classname)

            c_sensor_i2c_address = self.config.get('TEMP_SENSOR', {}).get('C_I2C_ADDRESS', '0x40')
            c_sensor_pin_1_name = self.config.get('TEMP_SENSOR', {}).get('C_PIN_1', 'notdefined')

            self.sensors[2] = c_sensor(self.config, pin_1_name=c_sensor_pin_1_name, i2c_address=c_sensor_i2c_address)
        else:
            self.sensors[2] = indi_allsky_sensors.sensor_simulator(self.config)

        self.sensors[2].slot = self.config.get('TEMP_SENSOR', {}).get('C_USER_VAR_SLOT', 15)


    def check_dew_heater_thresholds(self):
        manual_target = self.config.get('DEW_HEATER', {}).get('MANUAL_TARGET', 0.0)
        if manual_target:
            target_val = manual_target
        else:
            target_val = self.sensors_user_av[2]  # dew point


        if not target_val:
            logger.warning('Dew heater target dew point is 0, possible misconfiguration')


        current_temp = self.sensors_user_av[self.dh_temp_user_slot]  # dew point


        temp_diff = current_temp - target_val
        logger.info('Dew Heater threshold delta: %0.1f', temp_diff)

        if temp_diff <= self.dh_thold_diff_high:
            # set dew heater to high
            self.set_dew_heater(self.dh_level_high)
        elif temp_diff <= self.dh_thold_diff_med:
            # set dew heater to medium
            self.set_dew_heater(self.dh_level_med)
        elif temp_diff <= self.dh_thold_diff_low:
            # set dew heater to low
            self.set_dew_heater(self.dh_level_low)
        else:
            self.set_dew_heater(self.dh_level_default)
            #self.set_dew_heater(0)


    def check_fan_thresholds(self):
        current_temp = self.sensors_user_av[self.fan_temp_user_slot]


        temp_diff = current_temp - self.fan_target
        logger.info('Fan threshold delta: %0.1f', temp_diff)

        if temp_diff > self.fan_thold_diff_high:
            # set fan to high
            self.set_fan(self.fan_level_high)
        elif temp_diff > self.fan_thold_diff_med:
            # set fan to medium
            self.set_fan(self.fan_level_med)
        elif temp_diff > self.fan_thold_diff_low:
            # set fan to low
            self.set_fan(self.fan_level_low)
        else:
            self.set_fan(self.fan_level_default)
            #self.set_fan(0)

