"""What the user can change without touching Python (spec 06): the mode 1 pages and what the mode 3 event log
shows. One TOML file, written by the config UI (``config_server.py``) or by hand:

    [[page]]
    title = "FLIGHT"
    lines = ["IAS {AIRSPEED_INDICATED:3.0f} GS {GROUND_VELOCITY:3.0f}", "...", "..."]

    [events]
    hidden = ["GENERAL_ENG_THROTTLE_LEVER_POSITION:1"]   # rules (by SimVar) that log nothing
    key_events = true                                       # show key events no rule explains, as EV NAME
    ignored_key_events = ["BRAKES"]                         # except these

Everything is optional; what is missing keeps its default. Pure apart from ``load`` and ``save``:
``from_dict`` validates and raises ``ConfigError`` with every problem it found, each with a path the UI can
put next to the right input (``page.2.lines.1``).
"""

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import templates
from .event_rules import RULES, VARS
from .pages import DEFAULT_PAGES, template_page

MAX_PAGES = 20
TITLE_LEN = 9  # "P10/12 " leaves nine of the sixteen columns for the title in the page banner
_EVENT_NAME = re.compile(r"[A-Z0-9_]+")


class ConfigError(ValueError):
    """``errors`` is a list of ``{"path": "page.0.lines.2", "message": "..."}``."""

    def __init__(self, errors):
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors))
        self.errors = errors


@dataclass(frozen=True)
class PageConfig:
    title: str
    lines: tuple[str, str, str]


@dataclass(frozen=True)
class EventsConfig:
    hidden: frozenset[str] = frozenset()
    key_events: bool = True
    ignored_key_events: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Config:
    pages: tuple[PageConfig, ...] = tuple(PageConfig(title, tuple(lines)) for title, lines in DEFAULT_PAGES)
    events: EventsConfig = field(default_factory=EventsConfig)

    def build_pages(self):
        return tuple(template_page(p.title, p.lines) for p in self.pages)

    @property
    def vars(self):
        """The SimVars the pages read, for the feed."""
        return tuple(dict.fromkeys(v for p in self.pages for line in p.lines for v in templates.variables(line)))


# ---------------------------------------------------------------------------------------------- dict <-> Config
def to_dict(config):
    return {
        "page": [{"title": p.title, "lines": list(p.lines)} for p in config.pages],
        "events": {
            "hidden": [v for v in VARS if v in config.events.hidden],  # rule order, so the file diffs well
            "key_events": config.events.key_events,
            "ignored_key_events": sorted(config.events.ignored_key_events),
        },
    }


def _strings(value, path, errors):
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    errors.append({"path": path, "message": "expected a list of strings"})
    return []


def from_dict(data, known=None):
    """A validated ``Config``. ``known(simvar_name) -> bool`` checks names when the SimVar table is around."""
    errors = []
    if not isinstance(data, dict):
        raise ConfigError([{"path": "", "message": "expected a table"}])
    default = Config()

    pages = default.pages
    if "page" in data:
        raw_pages = data["page"] if isinstance(data["page"], list) else []
        if not raw_pages:
            errors.append({"path": "page", "message": "there must be at least one page"})
        if len(raw_pages) > MAX_PAGES:
            errors.append({"path": "page", "message": f"at most {MAX_PAGES} pages"})
        pages = tuple(_page(raw, f"page.{i}", errors, known) for i, raw in enumerate(raw_pages[:MAX_PAGES]))

    events = default.events
    raw = data.get("events", {})
    if not isinstance(raw, dict):
        errors.append({"path": "events", "message": "expected a table"})
    else:
        hidden = _strings(raw.get("hidden", []), "events.hidden", errors)
        for name in hidden:
            if name not in VARS:
                errors.append({"path": "events.hidden", "message": f"no event rule watches {name}"})
        ignored = [
            name.strip().upper() for name in _strings(raw.get("ignored_key_events", []), "events.ignored", errors)
        ]
        for name in ignored:
            if not _EVENT_NAME.fullmatch(name):
                errors.append({"path": "events.ignored", "message": f"{name!r} is not a key event name"})
        key_events = raw.get("key_events", True)
        if not isinstance(key_events, bool):
            errors.append({"path": "events.key_events", "message": "expected true or false"})
        events = EventsConfig(frozenset(hidden), key_events is not False, frozenset(ignored))

    if errors:
        raise ConfigError(errors)
    return Config(pages, events)


def _page(raw, path, errors, known):
    raw = raw if isinstance(raw, dict) else {}
    title = raw.get("title", "")
    if not isinstance(title, str) or not title.strip():
        errors.append({"path": f"{path}.title", "message": "a page needs a title"})
        title = "?"
    title = title.strip().upper()
    if len(title) > TITLE_LEN or not all(32 <= ord(c) < 127 for c in title):
        errors.append({"path": f"{path}.title", "message": f"at most {TITLE_LEN} plain ASCII characters"})
    lines = raw.get("lines", [])
    if not isinstance(lines, list) or len(lines) > 3 or not all(isinstance(line, str) for line in lines):
        errors.append({"path": f"{path}.lines", "message": "expected up to three lines of text"})
        lines = []
    lines = (list(lines) + ["", "", ""])[:3]
    for i, line in enumerate(lines):
        problem = templates.check(line, known)
        if problem:
            errors.append({"path": f"{path}.lines.{i}", "message": problem})
            lines[i] = ""
    return PageConfig(title, tuple(lines))


# ---------------------------------------------------------------------------------------------- TOML
def dumps(config):
    """``config`` as TOML. JSON strings and lists of strings are valid TOML, so ``json.dumps`` does the quoting."""
    data = to_dict(config)
    out = ["# x52-simconnect configuration. Edit here or in the config UI; the bridge picks up changes.", ""]
    for page in data["page"]:
        out += ["[[page]]", f"title = {json.dumps(page['title'])}", "lines = ["]
        out += [f"    {json.dumps(line)}," for line in page["lines"]]
        out += ["]", ""]
    events = data["events"]
    out += ["[events]", "hidden = ["]
    out += [f"    {json.dumps(name)}," for name in events["hidden"]]
    out += ["]", f"key_events = {'true' if events['key_events'] else 'false'}"]
    out += [f"ignored_key_events = {json.dumps(events['ignored_key_events'])}", ""]
    return "\n".join(out)


def loads(text, known=None):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([{"path": "", "message": f"not valid TOML: {e}"}]) from e
    return from_dict(data, known)


def default_path():
    """Per-user, outside the repo: ``%APPDATA%/x52-simconnect/config.toml`` (``~/.config`` elsewhere)."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "x52-simconnect" / "config.toml"


def load(path, known=None):
    """The config in ``path``; the defaults when there is no such file. Raises ``ConfigError`` for a bad one."""
    path = Path(path)
    if not path.exists():
        return Config()
    return loads(path.read_text(encoding="utf-8"), known)


def save(path, config):
    """Write ``config`` to ``path`` through a temporary file, so a crash never leaves half a config."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps(config), encoding="utf-8", newline="\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------------------------- for the UI
# Ready-made fields the page editor offers: (group, what it shows, the template text it inserts). Anything in
# Python-SimConnect's table works in a template; these are the ones with the unit and the width sorted out.
FIELDS = (
    ("Flight", "Indicated airspeed, kt", "{AIRSPEED_INDICATED:3.0f}"),
    ("Flight", "True airspeed, kt", "{AIRSPEED_TRUE:3.0f}"),
    ("Flight", "Ground speed, kt", "{GROUND_VELOCITY:3.0f}"),
    ("Flight", "Mach", "{AIRSPEED_MACH:4.2f}"),
    ("Flight", "Indicated altitude, ft", "{INDICATED_ALTITUDE:5.0f}"),
    ("Flight", "Height above ground, ft", "{PLANE_ALT_ABOVE_GROUND:5.0f}"),
    ("Flight", "Vertical speed, ft/min", "{VERTICAL_SPEED|signed:5}"),
    ("Flight", "Heading, magnetic", "{PLANE_HEADING_DEGREES_MAGNETIC|hdg}"),
    ("Flight", "Ground track, magnetic", "{GPS_GROUND_MAGNETIC_TRACK|hdg}"),
    ("Flight", "G force", "{G_FORCE:+4.1f}"),
    ("Flight", "Latitude", "{PLANE_LATITUDE|lat}"),
    ("Flight", "Longitude", "{PLANE_LONGITUDE|lon}"),
    ("Controls", "Flaps handle position", "{FLAPS_HANDLE_INDEX:1d}"),
    ("Controls", "Gear handle", "{GEAR_HANDLE_POSITION|either:DOWN,UP}"),
    ("Controls", "Parking brake", "{BRAKE_PARKING_POSITION|onoff:PARK}"),
    ("Controls", "Elevator trim, %", "{ELEVATOR_TRIM_PCT:+4.0%}"),
    ("Controls", "Spoilers, %", "{SPOILERS_HANDLE_POSITION:4.0%}"),
    ("Engine and fuel", "Engine 1 RPM", "{GENERAL_ENG_RPM:1:5.0f}"),
    ("Engine and fuel", "Engine 1 throttle, %", "{GENERAL_ENG_THROTTLE_LEVER_POSITION:1:3.0f}"),
    ("Engine and fuel", "Engine 1 mixture, %", "{GENERAL_ENG_MIXTURE_LEVER_POSITION:1:3.0f}"),
    ("Engine and fuel", "Engine 1 propeller lever, %", "{GENERAL_ENG_PROPELLER_LEVER_POSITION:1:3.0f}"),
    ("Engine and fuel", "Engine 1 N1, %", "{TURB_ENG_N1:1:5.1f}"),
    ("Engine and fuel", "Engine 2 N1, %", "{TURB_ENG_N1:2:5.1f}"),
    ("Engine and fuel", "Engine 1 fuel flow, gal/h", "{ENG_FUEL_FLOW_GPH:1:5.1f}"),
    ("Engine and fuel", "Fuel on board, gal", "{FUEL_TOTAL_QUANTITY:6.1f}"),
    ("Engine and fuel", "Fuel on board, lb", "{FUEL_TOTAL_QUANTITY_WEIGHT:6.0f}"),
    ("Engine and fuel", "Main bus voltage", "{ELECTRICAL_MAIN_BUS_VOLTAGE:4.1f}"),
    ("Autopilot", "Autopilot master", "{AUTOPILOT_MASTER|either:ON,OFF}"),
    ("Autopilot", "Heading hold", "{AUTOPILOT_HEADING_LOCK|onoff:HDG}"),
    ("Autopilot", "Heading bug", "{AUTOPILOT_HEADING_LOCK_DIR:03d}"),
    ("Autopilot", "Altitude hold", "{AUTOPILOT_ALTITUDE_LOCK|onoff:ALT}"),
    ("Autopilot", "Selected altitude, ft", "{AUTOPILOT_ALTITUDE_LOCK_VAR:5.0f}"),
    ("Autopilot", "Selected vertical speed", "{AUTOPILOT_VERTICAL_HOLD_VAR|signed:5}"),
    ("Autopilot", "Selected airspeed, kt", "{AUTOPILOT_AIRSPEED_HOLD_VAR:3.0f}"),
    ("Autopilot", "NAV hold", "{AUTOPILOT_NAV1_LOCK|onoff:NAV}"),
    ("Autopilot", "Approach hold", "{AUTOPILOT_APPROACH_HOLD|onoff:APR}"),
    ("Autopilot", "Flight director", "{AUTOPILOT_FLIGHT_DIRECTOR_ACTIVE|onoff:FD}"),
    ("Autopilot", "Yaw damper", "{AUTOPILOT_YAW_DAMPER|onoff:YD}"),
    ("Radios", "COM1 active", "{COM_ACTIVE_FREQUENCY:1|freq}"),
    ("Radios", "COM1 standby", "{COM_STANDBY_FREQUENCY:1|freq}"),
    ("Radios", "COM2 active", "{COM_ACTIVE_FREQUENCY:2|freq}"),
    ("Radios", "NAV1 active", "{NAV_ACTIVE_FREQUENCY:1|freq:2}"),
    ("Radios", "NAV1 standby", "{NAV_STANDBY_FREQUENCY:1|freq:2}"),
    ("Radios", "NAV1 course (OBS)", "{NAV_OBS:1:03d}"),
    ("Radios", "NAV1 DME, NM", "{NAV_DME:1:5.1f}"),
    ("Radios", "Squawk", "{TRANSPONDER_CODE:1|bcd}"),
    ("Radios", "Altimeter setting, hPa", "{KOHLSMAN_SETTING_MB:4.0f}"),
    ("Radios", "Altimeter setting, inHg", "{KOHLSMAN_SETTING_HG:5.2f}"),
    ("Weather and time", "Outside air temperature, C", "{AMBIENT_TEMPERATURE|signed:3}"),
    ("Weather and time", "Wind direction", "{AMBIENT_WIND_DIRECTION:03d}"),
    ("Weather and time", "Wind speed, kt", "{AMBIENT_WIND_VELOCITY:2.0f}"),
    ("Weather and time", "Zulu time", "{ZULU_TIME|hms}"),
    ("Weather and time", "Local time", "{LOCAL_TIME|hms}"),
)


# Raw SimVar values that make a believable example line for a rule; every other rule is a switch, shown "on".
EXAMPLE_VALUES = {
    "FLAPS_HANDLE_INDEX": 2,
    "SPOILERS_HANDLE_POSITION": 0.5,
    "ELEVATOR_TRIM_PCT": 0.12,
    "GENERAL_ENG_THROTTLE_LEVER_POSITION:1": 75,
    "GENERAL_ENG_PROPELLER_LEVER_POSITION:1": 90,
    "GENERAL_ENG_MIXTURE_LEVER_POSITION:1": 80,
    "FUEL_TANK_SELECTOR:1": 2,
    "AUTOPILOT_HEADING_LOCK_DIR": 270,
    "AUTOPILOT_ALTITUDE_LOCK_VAR": 5000,
    "COM_ACTIVE_FREQUENCY:1": 118.75,
    "COM_STANDBY_FREQUENCY:1": 121.5,
    "NAV_ACTIVE_FREQUENCY:1": 110.5,
    "NAV_STANDBY_FREQUENCY:1": 113.9,
    "TRANSPONDER_CODE:1": 0x7000,
    "KOHLSMAN_SETTING_MB": 1013.25,
}


def rule_catalogue():
    """The event rules as the UI lists them: grouped, each with a label and an example of its line."""
    out = []
    for rule in RULES:
        try:
            example = rule.text(rule.policy.quantise(rule.key(EXAMPLE_VALUES.get(rule.var, 1))))
        except Exception:  # noqa: BLE001 - an example is decoration; never let it break the catalogue
            example = ""
        out.append({"var": rule.var, "label": rule.label or rule.var, "group": rule.group, "example": example})
    return out
