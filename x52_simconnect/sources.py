"""Where the values come from. Both sources share one small interface:

src.ensure() -> bool          try to (re)connect if needed; True when data can be read
src.read(names) -> dict|None  latest values for ``names``, or None while there is no data
src.close()
"""

import math
import time

from .pages import ALL_VARS

STALE_SECONDS = 15  # no packet for this long -> drop the connection and retry
RETRY_SECONDS = 5  # how often to try connecting while the sim is down


class SimSource:
    """Streaming feed of ALL_VARS via one SimConnect data definition (see sim_feed.py)."""

    def __init__(self, names=ALL_VARS):
        from .sim_feed import SimFeed  # imports the SimConnect package, Windows-only

        self.feed = SimFeed(names)
        self._next_try = 0

    @property
    def connected(self):
        return self.feed.connected

    def ensure(self):
        if self.feed.connected:
            return True
        if time.time() < self._next_try:
            return False
        self._next_try = time.time() + RETRY_SECONDS
        try:
            self.feed.connect()
            print(f"connected to sim, streaming {len(self.feed.names)} vars")
        except Exception as e:  # noqa: BLE001 - any failure just means "try again later"
            print(f"sim not available ({e.__class__.__name__}: {e}); retrying")
        return self.feed.connected

    def read(self, names):
        age = self.feed.age()
        if age == float("inf"):
            return None
        if age > STALE_SECONDS:
            if self.feed.connected:
                print(f"no data from sim for {age:.0f} s; reconnecting")
                self.feed.close()
            return None
        return self.feed.get(names)

    def close(self):
        self.feed.close()


def demo_values(t):
    """Plausible, slowly moving values for every SimVar the pages use, ``t`` seconds into the demo."""
    return {
        "AIRSPEED_INDICATED": 118 + 10 * math.sin(t / 5),
        "GROUND_VELOCITY": 124 + 10 * math.sin(t / 5),
        "INDICATED_ALTITUDE": 3500 + 400 * math.sin(t / 9),
        "VERTICAL_SPEED": 500 * math.cos(t / 9),
        "PLANE_HEADING_DEGREES_MAGNETIC": math.radians((270 + t * 2) % 360),
        "GPS_GROUND_MAGNETIC_TRACK": math.radians((268 + t * 2) % 360),
        "COM_ACTIVE_FREQUENCY:1": 118.750,
        "COM_STANDBY_FREQUENCY:1": 121.500,
        "NAV_ACTIVE_FREQUENCY:1": 110.50,
        "TRANSPONDER_CODE:1": 0x7000,  # BCO16
        "AUTOPILOT_MASTER": 1,
        "AUTOPILOT_HEADING_LOCK": 1,
        "AUTOPILOT_HEADING_LOCK_DIR": 270,
        "AUTOPILOT_ALTITUDE_LOCK": 1,
        "AUTOPILOT_ALTITUDE_LOCK_VAR": 5000,
        "AUTOPILOT_VERTICAL_HOLD_VAR": 700,
        "AUTOPILOT_NAV1_LOCK": 0,
        "AUTOPILOT_AIRSPEED_HOLD_VAR": 200,
        "GENERAL_ENG_RPM:1": 2350 + 20 * math.sin(t),
        "FUEL_TOTAL_QUANTITY": 42.5 - t / 600,
        "AMBIENT_TEMPERATURE": 12.4,
        "AIRSPEED_TRUE": 130,
        "PLANE_LATITUDE": 55.6180 + t / 20000,
        "PLANE_LONGITUDE": 12.6508,
        "PLANE_ALT_ABOVE_GROUND": 3455,
        "ZULU_TIME": (12 * 3600 + t * 60) % 86400,
        "LOCAL_TIME": (14 * 3600 + t * 60) % 86400,
        "ZULU_DAY_OF_MONTH": 17,
        "ZULU_MONTH_OF_YEAR": 9,
        "ZULU_YEAR": 2026,
        "TIME_OF_DAY": int(t / 10) % 4,
    }


class DemoSource:
    """Fake flight data for running without the sim."""

    connected = True

    def __init__(self, clock=time.time):
        self._clock = clock
        self.t0 = clock()

    def ensure(self):
        return True

    def read(self, names):
        vals = demo_values(self._clock() - self.t0)
        return {n: vals.get(n) for n in names}

    def close(self):
        pass
