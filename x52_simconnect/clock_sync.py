"""Drive the X52 firmware clock/date under the text from sim time, and MFD/LED brightness from
time of day. Clock 1 = sim Zulu, clock 2 = sim local, clock 3 = real wall-clock time (as offsets)."""

from datetime import datetime

from .formatting import num

# TIME_OF_DAY enum -> brightness 0..128: dawn, day, dusk, night
BRIGHTNESS = {0: 80, 1: 128, 2: 80, 3: 32}
DEFAULT_BRIGHTNESS = 128


def offset_minutes(minutes_a, minutes_b):
    """a - b in minutes, wrapped into [-720, 720)."""
    return (int(round(minutes_a - minutes_b)) + 720) % 1440 - 720


class ClockSync:
    def __init__(self, mfd, clock=True, brightness=True, now=datetime.now):
        self.mfd = mfd
        self.clock = clock
        self.brightness = brightness
        self._now = now

    def _set_brightness(self, level):
        self.mfd.set_brightness(level)
        self.mfd.set_led_brightness(level)

    def update(self, values):
        """Apply one set of feed values. While there is no sim time yet, only make sure the display is lit:
        the stick powers up with the MFD backlight off."""
        if values is None or values.get("ZULU_TIME") is None:
            if self.brightness:
                self._set_brightness(DEFAULT_BRIGHTNESS)
            return
        if self.clock:
            zulu = num(values["ZULU_TIME"]) / 60  # minutes since midnight
            local = num(values["LOCAL_TIME"]) / 60
            self.mfd.set_clock(int(zulu // 60) % 24, int(zulu % 60))
            self.mfd.set_clock_offset(2, offset_minutes(local, zulu))
            now = self._now()
            self.mfd.set_clock_offset(3, offset_minutes(now.hour * 60 + now.minute, zulu))
            self.mfd.set_date(
                int(num(values["ZULU_DAY_OF_MONTH"], 1)),
                int(num(values["ZULU_MONTH_OF_YEAR"], 1)),
                int(num(values["ZULU_YEAR"], 2000)),
            )
        if self.brightness:
            self._set_brightness(BRIGHTNESS.get(int(num(values["TIME_OF_DAY"], 1)), DEFAULT_BRIGHTNESS))
