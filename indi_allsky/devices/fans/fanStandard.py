#import time
import logging

from .fanBase import FanBase


logger = logging.getLogger('indi_allsky')


class FanStandard(FanBase):

    def __init__(self, *args, **kwargs):
        super(FanStandard, self).__init__(*args, **kwargs)

        pin_1_name = kwargs['pin_1_name']

        import board
        import digitalio

        pin1 = getattr(board, pin_1_name)

        self.pin = digitalio.DigitalInOut(pin1)
        self.pin.direction = digitalio.Direction.OUTPUT

        self._state = None


    @property
    def state(self):
        return self._state


    @state.setter
    def state(self, new_state):
        # any positive value is ON
        new_state_b = bool(new_state)

        if new_state_b:
            logger.warning('Set fan state: 100%')
            self.pin.value = 1
            self._state = 100
        else:
            logger.warning('Set fan state: 0%')
            self.pin.value = 0
            self._state = 0


    def disable(self):
        self.state = 0

