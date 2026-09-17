"""The MFD pages: which SimVars each needs and how it renders them into three 16-character lines.
Pure: give ``render`` a dict of values (None allowed) and get three strings back."""

from collections.abc import Callable
from dataclasses import dataclass

from .formatting import bcd, clip, flag, freq, hdg3, latlon, num, onoff, signed


@dataclass(frozen=True)
class Page:
    title: str
    vars: tuple[str, ...]
    render: Callable[[dict], list[str]]


def _flight(v):
    return [
        f"IAS {num(v['AIRSPEED_INDICATED']):3.0f} GS {num(v['GROUND_VELOCITY']):3.0f}",
        f"ALT {num(v['INDICATED_ALTITUDE']):5.0f} V{signed(v['VERTICAL_SPEED'], 5)}",
        f"HDG {hdg3(v['PLANE_HEADING_DEGREES_MAGNETIC'])}  TRK {hdg3(v['GPS_GROUND_MAGNETIC_TRACK'])}",
    ]


def _radio(v):
    return [
        f"COM1 {freq(v['COM_ACTIVE_FREQUENCY:1'])}",
        f"STBY {freq(v['COM_STANDBY_FREQUENCY:1'])}",
        f"NAV1 {freq(v['NAV_ACTIVE_FREQUENCY:1'])[:6]} {bcd(v['TRANSPONDER_CODE:1'])}",
    ]


def _autopilot(v):
    ap = "ON " if flag(v["AUTOPILOT_MASTER"]) else "OFF"
    return [
        f"AP {ap} {onoff(v['AUTOPILOT_HEADING_LOCK'], 'HDG')} {int(num(v['AUTOPILOT_HEADING_LOCK_DIR'])):03d}",
        f"{onoff(v['AUTOPILOT_ALTITUDE_LOCK'], 'ALT')} {num(v['AUTOPILOT_ALTITUDE_LOCK_VAR']):5.0f} "
        f"V{signed(v['AUTOPILOT_VERTICAL_HOLD_VAR'], 5)}",
        f"{onoff(v['AUTOPILOT_NAV1_LOCK'], 'NAV')} SPD {num(v['AUTOPILOT_AIRSPEED_HOLD_VAR']):3.0f}",
    ]


def _engine(v):
    return [
        f"RPM {num(v['GENERAL_ENG_RPM:1']):5.0f}",
        f"FUEL {num(v['FUEL_TOTAL_QUANTITY']):6.1f} GAL",
        f"OAT {signed(v['AMBIENT_TEMPERATURE'], 3)}C TAS {num(v['AIRSPEED_TRUE']):3.0f}",
    ]


def _position(v):
    lat, lon = latlon(v["PLANE_LATITUDE"], v["PLANE_LONGITUDE"])
    return [lat, lon, f"AGL {num(v['PLANE_ALT_ABOVE_GROUND']):5.0f} FT"]


PAGES = (
    Page(
        "FLIGHT",
        (
            "AIRSPEED_INDICATED",
            "GROUND_VELOCITY",
            "INDICATED_ALTITUDE",
            "VERTICAL_SPEED",
            "PLANE_HEADING_DEGREES_MAGNETIC",
            "GPS_GROUND_MAGNETIC_TRACK",
        ),
        _flight,
    ),
    Page(
        "RADIO",
        ("COM_ACTIVE_FREQUENCY:1", "COM_STANDBY_FREQUENCY:1", "NAV_ACTIVE_FREQUENCY:1", "TRANSPONDER_CODE:1"),
        _radio,
    ),
    Page(
        "AUTOPILOT",
        (
            "AUTOPILOT_MASTER",
            "AUTOPILOT_HEADING_LOCK",
            "AUTOPILOT_HEADING_LOCK_DIR",
            "AUTOPILOT_ALTITUDE_LOCK",
            "AUTOPILOT_ALTITUDE_LOCK_VAR",
            "AUTOPILOT_VERTICAL_HOLD_VAR",
            "AUTOPILOT_NAV1_LOCK",
            "AUTOPILOT_AIRSPEED_HOLD_VAR",
        ),
        _autopilot,
    ),
    Page(
        "ENGINE",
        ("GENERAL_ENG_RPM:1", "FUEL_TOTAL_QUANTITY", "AMBIENT_TEMPERATURE", "AIRSPEED_TRUE"),
        _engine,
    ),
    Page(
        "POSITION",
        ("PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALT_ABOVE_GROUND"),
        _position,
    ),
)

# Streamed alongside the page vars so the firmware clock and brightness can follow sim time.
CLOCK_VARS = ("ZULU_TIME", "LOCAL_TIME", "ZULU_DAY_OF_MONTH", "ZULU_MONTH_OF_YEAR", "ZULU_YEAR", "TIME_OF_DAY")

# Everything the feed streams, in a stable order and without duplicates.
ALL_VARS = tuple(dict.fromkeys([n for p in PAGES for n in p.vars] + list(CLOCK_VARS)))


def render(page, values):
    """Three clipped lines for ``page``. A formatting bug shows on the display instead of blanking it."""
    try:
        lines = page.render(values)
    except Exception as e:  # noqa: BLE001 - never let a formatting bug take the display down
        lines = [page.title, "format error", str(e)]
    return [clip(line) for line in lines]
