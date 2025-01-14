import logging

from .sensorBase import SensorBase
from ..exceptions import SensorReadException


logger = logging.getLogger('indi_allsky')


class TempSensorSht4x(SensorBase):

    def update(self):

        try:
            temp_c = float(self.sht4x.temperature)
            rel_h = float(self.sht4x.relative_humidity)
        except RuntimeError as e:
            raise SensorReadException(str(e)) from e


        logger.info('SHT4x - temp: %0.1fc, humidity: %0.1f%%', temp_c, rel_h)


        try:
            dew_point_c = self.get_dew_point_c(temp_c, rel_h)
            frost_point_c = self.get_frost_point_c(temp_c, dew_point_c)
        except ValueError as e:
            logger.error('Dew Point calculation error - ValueError: %s', str(e))
            dew_point_c = 0.0
            frost_point_c = 0.0



        if self.config.get('TEMP_DISPLAY') == 'f':
            current_temp = self.c2f(temp_c)
            current_dp = self.c2f(dew_point_c)
            current_fp = self.c2f(frost_point_c)
        elif self.config.get('TEMP_DISPLAY') == 'k':
            current_temp = self.c2k(temp_c)
            current_dp = self.c2k(dew_point_c)
            current_fp = self.c2k(frost_point_c)
        else:
            current_temp = temp_c
            current_dp = dew_point_c
            current_fp = frost_point_c


        data = {
            'dew_point' : current_dp,
            'frost_point' : current_fp,
            'data' : (current_temp, rel_h),
        }

        return data


class TempSensorSht4x_I2C(TempSensorSht4x):

    def __init__(self, *args, **kwargs):
        super(TempSensorSht4x_I2C, self).__init__(*args, **kwargs)

        i2c_address_str = kwargs['i2c_address']

        import board
        import adafruit_sht4x

        i2c_address = int(i2c_address_str, 16)  # string in config

        logger.warning('Initializing SHT4x I2C temperature device @ %s', hex(i2c_address))
        i2c = board.I2C()
        self.sht4x = adafruit_sht4x.SHT4x(i2c, address=i2c_address)

        # this should be the default
        self.sht4x.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION

        # Can also set the mode to enable heater
        # self.sht4x.mode = adafruit_sht4x.Mode.LOWHEAT_100MS


        # NOHEAT_HIGHPRECISION   No heater, high precision
        # NOHEAT_MEDPRECISION    No heater, med precision
        # NOHEAT_LOWPRECISION    No heater, low precision
        # HIGHHEAT_1S            High heat, 1 second
        # HIGHHEAT_100MS         High heat, 0.1 second
        # MEDHEAT_1S             Med heat, 1 second
        # MEDHEAT_100MS          Med heat, 0.1 second
        # LOWHEAT_1S             Low heat, 1 second
        # LOWHEAT_100MS          Low heat, 0.1 second

