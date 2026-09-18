"""Turns the streaming feed into event-log lines: "FLAPS 2", "GEAR DOWN", "AP HDG ON", "THR 75%".

Pure. ``RuleEngine.update(values, now)`` compares this tick's SimVar values with what it saw before and
returns the lines to log. Rules are data (``RULES``): which SimVar, how its raw value becomes a comparable
state, how a state is worded, and a policy that says when a new state counts:

    change()                log the moment the state differs from the last logged one
    settled(seconds)        log once the state has stopped changing for ``seconds`` (trim, heading bug)
    step(size, debounce)    quantise to ``size`` first, then settle for ``debounce`` (throttle in 5 % steps)

The first value seen for a SimVar is a baseline, not an event, and so is the first value after the sim
stopped sending (``None``). ``BURST_LINES`` or more lines in one tick collapse into one ``12 CHANGES`` line:
restarting a flight swaps the whole cockpit state at once, with no ``None`` in between (seen live).
Bool SimVars go through ``formatting.flag`` (``> 0.5``), never a truth test.

Key events are the second source, and the catch-all for whatever has no rule: the feed subscribes to every
key event Python-SimConnect knows that passes ``loggable`` (``sim_events.all_key_events``), and the log shows
one as ``EV FLAPS_INCR`` only when no rule explains it (``apps.EventLogApp``).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from .formatting import bcd, flag, freq, num

SETTLE_SECONDS = 0.5  # default for settled() and step(): how long a value must hold still to be logged
HYSTERESIS = 0.75  # step(): a value leaves its step only once it is this fraction of a step away from it
BURST_LINES = 4  # this many lines in one tick is a flight restart or respawn, not a hand on a switch


# ---------------------------------------------------------------------------------------------- policies
@dataclass(frozen=True)
class Policy:
    settle: float = 0.0  # seconds the new state must hold before it is logged
    size: float = 0.0  # quantisation step, 0 = none

    def quantise(self, state, prev=None):
        """``state`` rounded to the step size. With ``prev`` (the step it was on) it stays there until it is
        well clear of it, so a lever resting on a boundary (82.4 %, 82.6 %) does not flap between two steps."""
        if not self.size or not isinstance(state, int | float) or isinstance(state, bool):
            return state
        if isinstance(prev, int | float) and not isinstance(prev, bool) and abs(state - prev) < self.size * HYSTERESIS:
            return prev
        return round(state / self.size) * self.size + 0.0  # "+ 0.0" turns -0.0 into 0.0


def change():
    return Policy()


def settled(seconds=SETTLE_SECONDS):
    return Policy(settle=seconds)


def step(size, debounce=SETTLE_SECONDS):
    return Policy(settle=debounce, size=size)


# ---------------------------------------------------------------------------------------------- rules
@dataclass(frozen=True)
class Rule:
    var: str
    key: Callable  # raw SimVar value -> comparable state (bool, int, float)
    text: Callable  # state -> log line, at most 12 characters (3 for the age column, 1 space)
    policy: Policy
    label: str = ""  # what the config UI calls it
    group: str = ""  # and the heading it is listed under


def rint(v):
    return int(round(num(v)))


def khz(v):
    """MHz rounded to the kHz, so 118.7499999 and 118.75 are the same station."""
    return round(num(v), 3)


def toggle(label):
    return lambda on: f"{label} {'ON' if on else 'OFF'}"


def either(when_true, when_false):
    return lambda on: when_true if on else when_false


TANKS = {0: "OFF", 1: "ALL", 2: "LEFT", 3: "RIGHT"}  # FUEL_TANK_SELECTOR enum; the rest show as a number


def _alt_sel(feet):
    return f"ALT SEL {feet}" if feet < 10000 else f"ALT SEL{feet}"  # 12 chars either way


def _group(name, *rules):
    return tuple(replace(rule, group=name) for rule in rules)


RULES = (
    *_group(
        "Flight controls",
        Rule("FLAPS_HANDLE_INDEX", rint, lambda n: f"FLAPS {n}", change(), "Flaps"),
        Rule("GEAR_HANDLE_POSITION", flag, either("GEAR DOWN", "GEAR UP"), change(), "Gear"),
        Rule("BRAKE_PARKING_POSITION", flag, toggle("PARK BRK"), change(), "Parking brake"),
        Rule("SPOILERS_HANDLE_POSITION", num, lambda s: f"SPOILER {s * 100:.0f}%", step(0.1, 0), "Spoilers"),
        Rule("ELEVATOR_TRIM_PCT", num, lambda s: f"TRIM {s * 100:+.0f}%", step(0.01), "Elevator trim"),
        Rule("SIM_ON_GROUND", flag, either("TOUCHDOWN", "AIRBORNE"), change(), "Airborne and touchdown"),
    ),
    *_group(
        "Engine and fuel",
        Rule("GENERAL_ENG_THROTTLE_LEVER_POSITION:1", num, lambda s: f"THR {s:.0f}%", step(5), "Throttle"),
        Rule("GENERAL_ENG_PROPELLER_LEVER_POSITION:1", num, lambda s: f"PROP {s:.0f}%", step(5), "Propeller"),
        Rule("GENERAL_ENG_MIXTURE_LEVER_POSITION:1", num, lambda s: f"MIX {s:.0f}%", step(5), "Mixture"),
        Rule("GENERAL_ENG_STARTER:1", flag, toggle("STARTER"), change(), "Starter"),
        Rule("GENERAL_ENG_COMBUSTION:1", flag, either("ENG RUNNING", "ENG STOPPED"), change(), "Engine running"),
        Rule("GENERAL_ENG_FUEL_PUMP_SWITCH:1", flag, toggle("FUEL PMP"), change(), "Fuel pump"),
        Rule("FUEL_TANK_SELECTOR:1", rint, lambda n: f"TANK {TANKS.get(n, n)}", change(), "Fuel tank selector"),
    ),
    *_group(
        "Lights",
        Rule("LIGHT_LANDING", flag, toggle("LDG LTS"), change(), "Landing lights"),
        Rule("LIGHT_TAXI", flag, toggle("TAXI LTS"), change(), "Taxi lights"),
        Rule("LIGHT_STROBE", flag, toggle("STROBES"), change(), "Strobes"),
        Rule("LIGHT_NAV", flag, toggle("NAV LTS"), change(), "Navigation lights"),
        Rule("LIGHT_BEACON", flag, toggle("BEACON"), change(), "Beacon"),
        Rule("LIGHT_PANEL", flag, toggle("PANEL LT"), change(), "Panel lights"),
        Rule("LIGHT_CABIN", flag, toggle("CABIN LT"), change(), "Cabin lights"),
        Rule("LIGHT_LOGO", flag, toggle("LOGO LTS"), change(), "Logo lights"),
        Rule("LIGHT_WING", flag, toggle("WING LTS"), change(), "Wing lights"),
        Rule("LIGHT_RECOGNITION", flag, toggle("RECOG LT"), change(), "Recognition lights"),
    ),
    *_group(
        "Systems",
        Rule("PITOT_HEAT", flag, toggle("PITOT HT"), change(), "Pitot heat"),
        Rule("ALTERNATE_STATIC_SOURCE_OPEN", flag, either("ALT STATIC", "NORM STATIC"), change(), "Alternate static"),
        Rule("GENERAL_ENG_ANTI_ICE_POSITION:1", flag, toggle("ANTI ICE"), change(), "Engine anti-ice"),
        Rule("STRUCTURAL_DEICE_SWITCH", flag, toggle("DEICE"), change(), "Structural de-ice"),
        Rule("PROP_DEICE_SWITCH:1", flag, toggle("PROP ICE"), change(), "Propeller de-ice"),
        Rule("ELECTRICAL_MASTER_BATTERY", flag, toggle("BATTERY"), change(), "Master battery"),
        Rule("GENERAL_ENG_MASTER_ALTERNATOR:1", flag, toggle("ALTERN"), change(), "Alternator"),
        Rule("AVIONICS_MASTER_SWITCH", flag, toggle("AVIONICS"), change(), "Avionics master"),
    ),
    *_group(
        "Autopilot",
        Rule("AUTOPILOT_MASTER", flag, toggle("AP"), change(), "Autopilot master"),
        Rule("AUTOPILOT_HEADING_LOCK", flag, toggle("AP HDG"), change(), "Heading hold"),
        Rule("AUTOPILOT_ALTITUDE_LOCK", flag, toggle("AP ALT"), change(), "Altitude hold"),
        Rule("AUTOPILOT_NAV1_LOCK", flag, toggle("AP NAV"), change(), "NAV hold"),
        Rule("AUTOPILOT_VERTICAL_HOLD", flag, toggle("AP VS"), change(), "Vertical speed hold"),
        Rule("AUTOPILOT_APPROACH_HOLD", flag, toggle("AP APR"), change(), "Approach hold"),
        Rule("AUTOPILOT_BACKCOURSE_HOLD", flag, toggle("AP BC"), change(), "Back course hold"),
        Rule("AUTOPILOT_AIRSPEED_HOLD", flag, toggle("AP IAS"), change(), "Airspeed hold"),
        Rule("AUTOPILOT_FLIGHT_LEVEL_CHANGE", flag, toggle("AP FLC"), change(), "Flight level change"),
        Rule("AUTOPILOT_FLIGHT_DIRECTOR_ACTIVE", flag, toggle("FD"), change(), "Flight director"),
        Rule("AUTOPILOT_YAW_DAMPER", flag, toggle("YD"), change(), "Yaw damper"),
        Rule("AUTOPILOT_HEADING_LOCK_DIR", rint, lambda d: f"HDG BUG {d % 360:03d}", settled(), "Heading bug"),
        Rule("AUTOPILOT_ALTITUDE_LOCK_VAR", rint, _alt_sel, settled(), "Selected altitude"),
    ),
    *_group(
        "Radios and altimeter",
        Rule("COM_ACTIVE_FREQUENCY:1", khz, lambda f: f"COM1 {freq(f)}", change(), "COM1 active"),
        Rule("COM_STANDBY_FREQUENCY:1", khz, lambda f: f"STBY {freq(f)}", change(), "COM1 standby"),
        Rule("NAV_ACTIVE_FREQUENCY:1", khz, lambda f: f"NAV1 {freq(f)}", change(), "NAV1 active"),
        Rule("NAV_STANDBY_FREQUENCY:1", khz, lambda f: f"NSBY {freq(f)}", change(), "NAV1 standby"),
        Rule("TRANSPONDER_CODE:1", rint, lambda c: f"SQK {bcd(c)}", change(), "Squawk"),
        Rule("KOHLSMAN_SETTING_MB", rint, lambda mb: f"QNH {mb}", settled(), "Altimeter setting"),
    ),
)

VARS = tuple(dict.fromkeys(r.var for r in RULES))

# Key events worth a notification. Never AXIS_* or *_SET events: those fire every frame from bound axes.
# The groups are Python-SimConnect's (EventList.py); these are not cockpit actions, and a hat panning the
# view would fill the log.
SKIPPED_EVENT_GROUPS = ("Slew_System", "View_System", "Mission_Keys", "ATC", "Multiplayer")
# In Python-SimConnect's table, but MSFS 2024 answers NAME_UNRECOGNIZED (seen live, 2026-09).
REJECTED_EVENTS = frozenset(
    {
        "KEY_PRESSURIZATION_PRESSURE_ALT_INC",
        "KEY_PRESSURIZATION_PRESSURE_ALT_DEC",
        "PRESSURIZATION_PRESSURE_DUMP_SWTICH",
    }
)


def loggable(name):
    """True for a discrete key event the log should be told about."""
    if not re.fullmatch(r"[A-Z0-9_]+", name):  # the table has placeholder rows named "Not supported"
        return False
    continuous = name.startswith("AXIS_") or name.endswith("_SET") or "_SET_" in name
    sim_control = name.startswith("PAUSE_")  # every pause sends PAUSE_TOGGLE and PAUSE_OFF/ON: two lines of noise
    return not continuous and not sim_control and name not in REJECTED_EVENTS


# ---------------------------------------------------------------------------------------------- engine
class _Tracker:
    """Per-rule memory: the state last logged, and the newer state waiting to settle."""

    __slots__ = ("logged", "pending", "since")

    def __init__(self):
        self.logged = None  # None: no baseline yet
        self.pending = None
        self.since = 0.0


class RuleEngine:
    def __init__(self, rules=RULES, hidden=()):
        self.rules = rules
        self.hidden = frozenset(hidden)  # SimVars whose rules still run (and explain key events) but log nothing
        self.fired = 0  # rules that logged a change in the last update, hidden ones included
        self._trackers = {rule: _Tracker() for rule in rules}

    @property
    def settling(self):
        """True while some rule has seen a change it has not logged yet (the value is still moving)."""
        return any(t.logged is not None and t.pending != t.logged for t in self._trackers.values())

    def update(self, values, now):
        """Feed this tick's values (a dict, or None while there is no sim data). Returns the new lines."""
        lines = []
        self.fired = 0
        for rule in self.rules:
            tracker = self._trackers[rule]
            raw = values.get(rule.var) if values else None
            if raw is None:
                tracker.logged = tracker.pending = None  # no data: whatever comes next is a new baseline
                continue
            state = rule.policy.quantise(rule.key(raw), tracker.pending)
            if tracker.logged is None:
                tracker.logged = tracker.pending = state
                continue
            if state != tracker.pending:
                tracker.pending, tracker.since = state, now
            if tracker.pending != tracker.logged and now - tracker.since >= rule.policy.settle:
                tracker.logged = tracker.pending
                self.fired += 1
                if rule.var not in self.hidden:
                    lines.append(rule.text(tracker.logged))
        if self.fired >= BURST_LINES:
            for tracker in self._trackers.values():
                tracker.logged = tracker.pending  # and whatever is still settling belongs to the same jump
            return [f"{self.fired} CHANGES"]
        return lines
