"""Where the values come from. Both sources share one small interface:

src.ensure() -> bool          try to (re)connect if needed; True when data can be read
src.read(names) -> dict|None  latest values for ``names``, or None while there is no data
src.events() -> list[str]     sim key events fired since the last call (for the event log)
src.close()
"""

import math
import time

from .apps import ALL_VARS

STALE_SECONDS = 15  # no packet for this long -> drop the connection and retry
RETRY_SECONDS = 5  # how often to try connecting while the sim is down


class SimSource:
    """Streaming feed of ALL_VARS via one SimConnect data definition, plus notifications for every discrete
    key event unless ``events`` names the ones to subscribe to (see sim_feed.py and sim_events.py)."""

    def __init__(self, names=ALL_VARS, events=None):
        from .sim_events import all_key_events
        from .sim_feed import SimFeed  # imports the SimConnect package, Windows-only

        self.feed = SimFeed(names, events=all_key_events() if events is None else events)
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

    def events(self):
        return self.feed.take_events()

    def close(self):
        self.feed.close()


DEMO_PERIOD = 92  # the scripted cockpit below repeats every this many seconds

# Cockpit state at the start of the demo loop, for the SimVars the event log watches.
DEMO_COCKPIT = {
    "FLAPS_HANDLE_INDEX": 0,
    "GEAR_HANDLE_POSITION": 1,
    "BRAKE_PARKING_POSITION": 1,
    "SPOILERS_HANDLE_POSITION": 0,
    "ELEVATOR_TRIM_PCT": 0,
    "GENERAL_ENG_THROTTLE_LEVER_POSITION:1": 0,
    "GENERAL_ENG_PROPELLER_LEVER_POSITION:1": 100,
    "GENERAL_ENG_MIXTURE_LEVER_POSITION:1": 100,
    "GENERAL_ENG_STARTER:1": 0,
    "GENERAL_ENG_COMBUSTION:1": 1,
    "GENERAL_ENG_FUEL_PUMP_SWITCH:1": 0,
    "FUEL_TANK_SELECTOR:1": 1,
    "LIGHT_LANDING": 0,
    "LIGHT_TAXI": 0,
    "LIGHT_STROBE": 0,
    "LIGHT_NAV": 1,
    "LIGHT_BEACON": 0,
    "LIGHT_PANEL": 0,
    "LIGHT_CABIN": 0,
    "LIGHT_LOGO": 0,
    "LIGHT_WING": 0,
    "LIGHT_RECOGNITION": 0,
    "PITOT_HEAT": 0,
    "ALTERNATE_STATIC_SOURCE_OPEN": 0,
    "GENERAL_ENG_ANTI_ICE_POSITION:1": 0,
    "STRUCTURAL_DEICE_SWITCH": 0,
    "PROP_DEICE_SWITCH:1": 0,
    "ELECTRICAL_MASTER_BATTERY": 0,
    "GENERAL_ENG_MASTER_ALTERNATOR:1": 0,
    "AVIONICS_MASTER_SWITCH": 1,
    "AUTOPILOT_MASTER": 0,
    "AUTOPILOT_HEADING_LOCK": 0,
    "AUTOPILOT_VERTICAL_HOLD": 0,
    "AUTOPILOT_APPROACH_HOLD": 0,
    "AUTOPILOT_BACKCOURSE_HOLD": 0,
    "AUTOPILOT_AIRSPEED_HOLD": 0,
    "AUTOPILOT_FLIGHT_LEVEL_CHANGE": 0,
    "AUTOPILOT_FLIGHT_DIRECTOR_ACTIVE": 0,
    "AUTOPILOT_YAW_DAMPER": 0,
    "NAV_STANDBY_FREQUENCY:1": 113.90,
    "KOHLSMAN_SETTING_MB": 1013.25,
    "SIM_ON_GROUND": 1,
}

# A departure and return, as (seconds into the loop, SimVar, new value). Each step makes one log line;
# by the end everything is back at DEMO_COCKPIT so the wrap-around is silent.
DEMO_TIMELINE = (
    (3, "ELECTRICAL_MASTER_BATTERY", 1),
    (5, "LIGHT_BEACON", 1),
    (8, "GENERAL_ENG_THROTTLE_LEVER_POSITION:1", 45),
    (12, "FLAPS_HANDLE_INDEX", 1),
    (14, "BRAKE_PARKING_POSITION", 0),
    (18, "LIGHT_STROBE", 1),
    (20, "GENERAL_ENG_THROTTLE_LEVER_POSITION:1", 100),
    (26, "SIM_ON_GROUND", 0),
    (30, "GEAR_HANDLE_POSITION", 0),
    (34, "FLAPS_HANDLE_INDEX", 0),
    (40, "AUTOPILOT_MASTER", 1),
    (43, "AUTOPILOT_HEADING_LOCK", 1),
    (46, "AUTOPILOT_HEADING_LOCK_DIR", 300),
    (50, "COM_STANDBY_FREQUENCY:1", 121.900),
    (54, "ELEVATOR_TRIM_PCT", 0.12),
    (60, "GEAR_HANDLE_POSITION", 1),
    (62, "FLAPS_HANDLE_INDEX", 2),
    (64, "AUTOPILOT_HEADING_LOCK_DIR", 270),
    (66, "AUTOPILOT_HEADING_LOCK", 0),
    (68, "AUTOPILOT_MASTER", 0),
    (70, "SIM_ON_GROUND", 1),
    (72, "GENERAL_ENG_THROTTLE_LEVER_POSITION:1", 0),
    (74, "ELEVATOR_TRIM_PCT", 0),
    (76, "FLAPS_HANDLE_INDEX", 0),
    (78, "BRAKE_PARKING_POSITION", 1),
    (80, "COM_STANDBY_FREQUENCY:1", 121.500),
    (84, "LIGHT_STROBE", 0),
    (88, "LIGHT_BEACON", 0),
    (90, "ELECTRICAL_MASTER_BATTERY", 0),
)

# Key events that change nothing (flaps already up, no AP for the panel button), so they show up as "EV ...".
DEMO_KEY_EVENTS = ((37, "FLAPS_DECR"), (56, "COM_STBY_RADIO_SWAP"))


def demo_events(t_from, t_to):
    """Names of the scripted key events firing in ``(t_from, t_to]``, in order, across loop wrap-arounds."""
    fired = []
    for at, name in DEMO_KEY_EVENTS:
        k = (t_from - at) // DEMO_PERIOD + 1  # first loop whose occurrence is after t_from
        while k * DEMO_PERIOD + at <= t_to:
            fired.append((k * DEMO_PERIOD + at, name))
            k += 1
    return [name for _, name in sorted(fired)]


def demo_values(t):
    """Plausible, slowly moving values for every SimVar the apps use, ``t`` seconds into the demo."""
    values = _demo_flight(t)
    values.update(DEMO_COCKPIT)
    loop_t = t % DEMO_PERIOD
    for at, name, value in DEMO_TIMELINE:
        if at <= loop_t:
            values[name] = value
    return values


def _demo_flight(t):
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
        self._events_t = 0.0

    def ensure(self):
        return True

    def read(self, names):
        vals = demo_values(self._clock() - self.t0)
        return {n: vals.get(n) for n in names}

    def events(self):
        t = self._clock() - self.t0
        names = demo_events(self._events_t, t)
        self._events_t = t
        return names

    def close(self):
        pass
