"""The MFD pages: which SimVars each needs and how it renders them into three 16-character lines.
Pure: give ``render`` a dict of values (None allowed) and get three strings back.

A page is a title and three templates (``templates.py``). ``DEFAULT_PAGES`` are the built-in ones; the config
file and the config UI replace them with the user's (``config.py``, spec 06)."""

from collections.abc import Callable
from dataclasses import dataclass

from . import templates
from .formatting import clip


@dataclass(frozen=True)
class Page:
    title: str
    vars: tuple[str, ...]
    render: Callable[[dict], list[str]]


def template_page(title, lines):
    """A ``Page`` from three templates. Raises ``templates.TemplateError`` for one that does not parse."""
    parsed = [templates.parse(line) for line in lines]
    names = tuple(dict.fromkeys(p.var for parts in parsed for p in parts if isinstance(p, templates.Field)))
    return Page(title, names, lambda values: [templates.render_parts(parts, values) for parts in parsed])


# (title, three templates). Rendered output is what the hand-written Python pages gave before them.
DEFAULT_PAGES = (
    (
        "FLIGHT",
        (
            "IAS {AIRSPEED_INDICATED:3.0f} GS {GROUND_VELOCITY:3.0f}",
            "ALT {INDICATED_ALTITUDE:5.0f} V{VERTICAL_SPEED|signed:5}",
            "HDG {PLANE_HEADING_DEGREES_MAGNETIC|hdg}  TRK {GPS_GROUND_MAGNETIC_TRACK|hdg}",
        ),
    ),
    (
        "RADIO",
        (
            "COM1 {COM_ACTIVE_FREQUENCY:1|freq}",
            "STBY {COM_STANDBY_FREQUENCY:1|freq}",
            "NAV1 {NAV_ACTIVE_FREQUENCY:1|freq:2} {TRANSPONDER_CODE:1|bcd}",
        ),
    ),
    (
        "AUTOPILOT",
        (
            "AP {AUTOPILOT_MASTER|either:ON,OFF} {AUTOPILOT_HEADING_LOCK|onoff:HDG} {AUTOPILOT_HEADING_LOCK_DIR:03d}",
            "{AUTOPILOT_ALTITUDE_LOCK|onoff:ALT} {AUTOPILOT_ALTITUDE_LOCK_VAR:5.0f} "
            "V{AUTOPILOT_VERTICAL_HOLD_VAR|signed:5}",
            "{AUTOPILOT_NAV1_LOCK|onoff:NAV} SPD {AUTOPILOT_AIRSPEED_HOLD_VAR:3.0f}",
        ),
    ),
    (
        "ENGINE",
        (
            "RPM {GENERAL_ENG_RPM:1:5.0f}",
            "FUEL {FUEL_TOTAL_QUANTITY:6.1f} GAL",
            "OAT {AMBIENT_TEMPERATURE|signed:3}C TAS {AIRSPEED_TRUE:3.0f}",
        ),
    ),
    (
        "POSITION",
        ("{PLANE_LATITUDE|lat}", "{PLANE_LONGITUDE|lon}", "AGL {PLANE_ALT_ABOVE_GROUND:5.0f} FT"),
    ),
)

PAGES = tuple(template_page(title, lines) for title, lines in DEFAULT_PAGES)

# Streamed alongside the app vars so the firmware clock and brightness can follow sim time.
# The full list the feed streams is ``apps.ALL_VARS``.
CLOCK_VARS = ("ZULU_TIME", "LOCAL_TIME", "ZULU_DAY_OF_MONTH", "ZULU_MONTH_OF_YEAR", "ZULU_YEAR", "TIME_OF_DAY")


def render(page, values):
    """Three clipped lines for ``page``. A formatting bug shows on the display instead of blanking it."""
    try:
        lines = page.render(values)
    except Exception as e:  # noqa: BLE001 - never let a formatting bug take the display down
        lines = [page.title, "format error", str(e)]
    return [clip(line) for line in lines]
