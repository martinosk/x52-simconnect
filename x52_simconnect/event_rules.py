"""Turns the streaming feed into event-log lines: "FLAPS 2", "GEAR DOWN", "AP HDG ON", "THR 75%".

Pure. ``RuleEngine.update(values, now)`` compares this tick's SimVar values with what it saw before and
returns the lines to log. Rules are data (``RULES``): which SimVar, how its raw value becomes a comparable
state, how a state is worded, and a policy that says when a new state counts:

    change()                log the moment the state differs from the last logged one
    settled(seconds)        log once the state has stopped changing for ``seconds`` (trim, heading bug)
    step(size, debounce)    quantise to ``size`` first, then settle for ``debounce`` (throttle in 5 % steps)

The first value seen for a SimVar is a baseline, not an event, and so is the first value after the sim
stopped sending (``None``). Bool SimVars go through ``formatting.flag`` (``> 0.5``), never a truth test.

``KEY_EVENTS`` lists the discrete key events worth subscribing to as a second source (``sim_events.py``);
the log shows them as ``EV FLAPS_INCR`` only when no rule explains them (``apps.EventLogApp``).
"""

from collections.abc import Callable
from dataclasses import dataclass

from .formatting import bcd, flag, freq, num

SETTLE_SECONDS = 0.5  # default for settled() and step(): how long a value must hold still to be logged


# ---------------------------------------------------------------------------------------------- policies
@dataclass(frozen=True)
class Policy:
    settle: float = 0.0  # seconds the new state must hold before it is logged
    size: float = 0.0  # quantisation step, 0 = none

    def quantise(self, state):
        if not self.size or not isinstance(state, int | float) or isinstance(state, bool):
            return state
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


def rint(v):
    return int(round(num(v)))


def khz(v):
    """MHz rounded to the kHz, so 118.7499999 and 118.75 are the same station."""
    return round(num(v), 3)


def toggle(label):
    return lambda on: f"{label} {'ON' if on else 'OFF'}"


def either(when_true, when_false):
    return lambda on: when_true if on else when_false


def _alt_sel(feet):
    return f"ALT SEL {feet}" if feet < 10000 else f"ALT SEL{feet}"  # 12 chars either way


RULES = (
    Rule("FLAPS_HANDLE_INDEX", rint, lambda n: f"FLAPS {n}", change()),
    Rule("GEAR_HANDLE_POSITION", flag, either("GEAR DOWN", "GEAR UP"), change()),
    Rule("BRAKE_PARKING_POSITION", flag, toggle("PARK BRK"), change()),
    Rule("SPOILERS_HANDLE_POSITION", num, lambda s: f"SPOILER {s * 100:.0f}%", step(0.1, 0)),
    Rule("ELEVATOR_TRIM_PCT", num, lambda s: f"TRIM {s * 100:+.0f}%", step(0.01)),
    Rule("GENERAL_ENG_THROTTLE_LEVER_POSITION:1", num, lambda s: f"THR {s:.0f}%", step(5)),
    Rule("LIGHT_LANDING", flag, toggle("LDG LTS"), change()),
    Rule("LIGHT_TAXI", flag, toggle("TAXI LTS"), change()),
    Rule("LIGHT_STROBE", flag, toggle("STROBES"), change()),
    Rule("LIGHT_NAV", flag, toggle("NAV LTS"), change()),
    Rule("LIGHT_BEACON", flag, toggle("BEACON"), change()),
    Rule("PITOT_HEAT", flag, toggle("PITOT HT"), change()),
    Rule("ELECTRICAL_MASTER_BATTERY", flag, toggle("BATTERY"), change()),
    Rule("GENERAL_ENG_MASTER_ALTERNATOR:1", flag, toggle("ALTERN"), change()),
    Rule("AUTOPILOT_MASTER", flag, toggle("AP"), change()),
    Rule("AUTOPILOT_HEADING_LOCK", flag, toggle("AP HDG"), change()),
    Rule("AUTOPILOT_ALTITUDE_LOCK", flag, toggle("AP ALT"), change()),
    Rule("AUTOPILOT_NAV1_LOCK", flag, toggle("AP NAV"), change()),
    Rule("AUTOPILOT_VERTICAL_HOLD", flag, toggle("AP VS"), change()),
    Rule("AUTOPILOT_HEADING_LOCK_DIR", rint, lambda d: f"HDG BUG {d % 360:03d}", settled()),
    Rule("AUTOPILOT_ALTITUDE_LOCK_VAR", rint, _alt_sel, settled()),
    Rule("COM_ACTIVE_FREQUENCY:1", khz, lambda f: f"COM1 {freq(f)}", change()),
    Rule("COM_STANDBY_FREQUENCY:1", khz, lambda f: f"STBY {freq(f)}", change()),
    Rule("TRANSPONDER_CODE:1", rint, lambda c: f"SQK {bcd(c)}", change()),
    Rule("SIM_ON_GROUND", flag, either("TOUCHDOWN", "AIRBORNE"), change()),
)

VARS = tuple(dict.fromkeys(r.var for r in RULES))

# Discrete key events to be notified about. Never AXIS_* or *_SET events: those fire every frame.
KEY_EVENTS = (
    "FLAPS_INCR", "FLAPS_DECR", "FLAPS_UP", "FLAPS_DOWN",
    "GEAR_TOGGLE", "GEAR_UP", "GEAR_DOWN",
    "PARKING_BRAKES", "SPOILERS_TOGGLE",
    "ELEV_TRIM_UP", "ELEV_TRIM_DN",
    "AP_MASTER", "AP_HDG_HOLD", "AP_ALT_HOLD", "AP_NAV1_HOLD", "AP_VS_HOLD",
    "AP_PANEL_HEADING_HOLD", "AP_PANEL_ALTITUDE_HOLD",
    "LANDING_LIGHTS_TOGGLE", "TOGGLE_TAXI_LIGHTS", "STROBES_TOGGLE", "TOGGLE_NAV_LIGHTS", "TOGGLE_BEACON_LIGHTS",
    "PITOT_HEAT_TOGGLE", "TOGGLE_MASTER_BATTERY", "TOGGLE_MASTER_ALTERNATOR",
    "COM_STBY_RADIO_SWAP",
)  # fmt: skip


# ---------------------------------------------------------------------------------------------- engine
class _Tracker:
    """Per-rule memory: the state last logged, and the newer state waiting to settle."""

    __slots__ = ("logged", "pending", "since")

    def __init__(self):
        self.logged = None  # None: no baseline yet
        self.pending = None
        self.since = 0.0


class RuleEngine:
    def __init__(self, rules=RULES):
        self.rules = rules
        self._trackers = {rule: _Tracker() for rule in rules}

    @property
    def settling(self):
        """True while some rule has seen a change it has not logged yet (the value is still moving)."""
        return any(t.logged is not None and t.pending != t.logged for t in self._trackers.values())

    def update(self, values, now):
        """Feed this tick's values (a dict, or None while there is no sim data). Returns the new lines."""
        lines = []
        for rule in self.rules:
            tracker = self._trackers[rule]
            raw = values.get(rule.var) if values else None
            if raw is None:
                tracker.logged = tracker.pending = None  # no data: whatever comes next is a new baseline
                continue
            state = rule.policy.quantise(rule.key(raw))
            if tracker.logged is None:
                tracker.logged = tracker.pending = state
                continue
            if state != tracker.pending:
                tracker.pending, tracker.since = state, now
            if tracker.pending != tracker.logged and now - tracker.since >= rule.policy.settle:
                tracker.logged = tracker.pending
                lines.append(rule.text(tracker.logged))
        return lines
