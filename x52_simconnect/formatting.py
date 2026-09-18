"""SimVar value -> MFD text helpers. Pure functions; every one tolerates None and garbage input,
because values are None while a flight loads and bool SimVars can arrive as denormal floats."""

import math

LINE_LEN = 16


def num(v, default=0.0):
    """float(v), or ``default`` for None and anything else float() rejects."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def deg(rad):
    """Radians -> whole degrees in [0, 360)."""
    return int(round(math.degrees(num(rad)))) % 360


def hdg3(rad):
    """Radians -> three-digit heading, ``"007"``."""
    return f"{deg(rad):03d}"


def signed(v, width):
    """Rounded integer with an explicit sign, right-aligned to ``width``."""
    return f"{int(round(num(v))):+{width}d}"


def freq(v):
    """MHz -> ``"118.750"``."""
    return f"{num(v):07.3f}"


def bcd(v):
    """BCO16-encoded transponder code -> its four digits (``0x7000`` -> ``"7000"``)."""
    return f"{int(num(v)):04x}"


def latlon(lat, lon):
    """Decimal degrees -> ``("N55 37.08", "E012 39.05")``."""
    lat, lon = num(lat), num(lon)
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    lat, lon = abs(lat), abs(lon)
    return (f"{ns}{int(lat):02d} {(lat % 1) * 60:05.2f}", f"{ew}{int(lon):03d} {(lon % 1) * 60:05.2f}")


def flag(v):
    """Bool SimVars can come back as denormal garbage (~1e-311) instead of 0. Threshold, never truth-test."""
    return num(v) > 0.5


def onoff(v, label):
    """``label`` when the flag is set, otherwise dashes of the same width."""
    return label if flag(v) else "-" * len(label)


def hms(seconds):
    """Seconds since midnight (``ZULU_TIME``, ``LOCAL_TIME``) -> ``"12:34:56"``; ``"--:--:--"`` without data."""
    if seconds is None:
        return "--:--:--"
    s = int(num(seconds)) % 86400
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def clip(text):
    """Cut a line to the MFD width."""
    return str(text)[:LINE_LEN]
