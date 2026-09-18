"""The template mini-language the configurable pages are written in (spec 03, used by spec 06).

    IAS {AIRSPEED_INDICATED:3.0f} GS {GROUND_VELOCITY:3.0f}
    HDG {PLANE_HEADING_DEGREES_MAGNETIC|hdg}  V{VERTICAL_SPEED|signed:5}
    RPM {GENERAL_ENG_RPM:1:5.0f}

A field is ``{VAR}``, ``{VAR:format}`` (a Python format spec; ``d`` and friends get the value as an int) or
``{VAR|filter}`` / ``{VAR|filter:argument}`` (``FILTERS``, thin wrappers over ``formatting.py``). An indexed
SimVar keeps its index in front of the format: ``{GENERAL_ENG_RPM:1:5.0f}``; a bare number after the name is
therefore always an index. ``{{`` and ``}}`` are literal braces. A field without format or filter is a whole
number.

Pure. ``parse`` raises ``TemplateError`` for anything it cannot make sense of; ``render`` never raises for
values: ``None`` and garbage render as 0, like everywhere else in ``formatting.py``.
"""

import re
from dataclasses import dataclass

from .formatting import LINE_LEN, bcd, clip, flag, freq, hdg3, hms, latlon, num, onoff, signed


class TemplateError(ValueError):
    """A template that cannot be parsed, names an unknown filter or has a format Python rejects."""


def _either(v, arg):
    when_on, _, when_off = arg.partition(",")
    width = max(len(when_on), len(when_off))
    return (when_on if flag(v) else when_off).ljust(width)


def _freq(v, arg):
    return freq(v)[: 4 + int(arg)] if arg else freq(v)


# name -> (function(value, argument), what it does, whether the argument is required)
FILTERS = {
    "hdg": (lambda v, arg: hdg3(v), "radians to a three-digit heading, 007", False),
    "signed": (lambda v, arg: signed(v, int(arg or 5)), "whole number with its sign, signed:5 is the width", False),
    "freq": (_freq, "MHz as 118.750; freq:2 keeps two decimals", False),
    "bcd": (lambda v, arg: bcd(v), "transponder code, 7000", False),
    "onoff": (lambda v, arg: onoff(v, arg), "onoff:HDG shows HDG when set, --- when not", True),
    "either": (_either, "either:ON,OFF shows one of two words", True),
    "lat": (lambda v, arg: latlon(v, 0)[0], "latitude as N55 37.08", False),
    "lon": (lambda v, arg: latlon(0, v)[1], "longitude as E012 39.05", False),
    "hms": (lambda v, arg: hms(v), "seconds since midnight as 12:34:56", False),
}

_FIELD = re.compile(
    r"""^\s*(?P<var>[A-Z][A-Z0-9_]*(?::\d+)?)      # SIMVAR_NAME or SIMVAR_NAME:1
        (?:
            :(?P<spec>[^|]*)                        # :5.0f
          | \|\s*(?P<filter>[a-z]+)(?::(?P<arg>.*))?   # |signed:5
        )?\s*$""",
    re.VERBOSE,
)
_INT_TYPES = "dbcoxXn"


@dataclass(frozen=True)
class Field:
    var: str
    spec: str = ""
    filter: str = ""
    arg: str = ""

    def format(self, value):
        if self.filter:
            return FILTERS[self.filter][0](value, self.arg)
        spec = self.spec or ".0f"
        if spec[-1] in _INT_TYPES:
            return format(int(num(value)), spec)
        return format(num(value), spec)


def _field(text):
    m = _FIELD.match(text)
    if not m:
        raise TemplateError(f"cannot read {{{text}}}: expected {{SIMVAR}}, {{SIMVAR:5.0f}} or {{SIMVAR|filter}}")
    field = Field(m["var"], m["spec"] or "", m["filter"] or "", m["arg"] or "")
    if field.filter:
        if field.filter not in FILTERS:
            raise TemplateError(f"unknown filter {field.filter!r}; there is {', '.join(FILTERS)}")
        if FILTERS[field.filter][2] and not field.arg:
            raise TemplateError(f"filter {field.filter!r} needs an argument, like {field.filter}:HDG")
    try:
        field.format(0.0)
    except (ValueError, TypeError) as e:
        raise TemplateError(f"{{{text}}}: {e}") from e
    return field


def parse(template):
    """``template`` as a tuple of literal strings and ``Field``s."""
    parts, literal, i = [], "", 0
    while i < len(template):
        ch = template[i]
        if template[i : i + 2] in ("{{", "}}"):
            literal += ch
            i += 2
        elif ch == "{":
            end = template.find("}", i)
            if end < 0:
                raise TemplateError("a { is never closed; write {{ for a literal brace")
            if literal:
                parts.append(literal)
                literal = ""
            parts.append(_field(template[i + 1 : end]))
            i = end + 1
        elif ch == "}":
            raise TemplateError("a } without its {; write }} for a literal brace")
        else:
            literal += ch
            i += 1
    if literal:
        parts.append(literal)
    return tuple(parts)


def variables(template):
    """The SimVars ``template`` reads, in order of appearance, without duplicates."""
    return tuple(dict.fromkeys(p.var for p in parse(template) if isinstance(p, Field)))


def render_parts(parts, values):
    return "".join(p if isinstance(p, str) else p.format(values.get(p.var)) for p in parts)


def render(template, values):
    """``template`` filled in from ``values`` (a dict, ``None`` values allowed), clipped to the display."""
    return clip(render_parts(parse(template), values or {}))


def check(template, known=None):
    """Why ``template`` cannot be used, or ``None``. ``known(name)`` says whether a SimVar exists; without it
    names are not checked. A line that is already too long with every value at zero is rejected: real values
    are rarely shorter."""
    try:
        parts = parse(template)
    except TemplateError as e:
        return str(e)
    if not all(32 <= ord(c) < 127 for p in parts if isinstance(p, str) for c in p):
        return "the MFD only shows plain ASCII characters"
    if known:
        unknown = [p.var for p in parts if isinstance(p, Field) and not known(p.var)]
        if unknown:
            return f"unknown SimVar {unknown[0]}"
    length = len(render_parts(parts, {}))
    if length > LINE_LEN:
        return f"{length} characters, the MFD shows {LINE_LEN}"
    return None
