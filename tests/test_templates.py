"""The template mini-language of the configurable pages: every filter, the index/format split, the errors."""

import math

import pytest

from x52_simconnect import templates as t
from x52_simconnect.formatting import LINE_LEN


def test_plain_text_and_literal_braces():
    assert t.render("MSFS 2024", {}) == "MSFS 2024"
    assert t.render("{{N1}} {X:2.0f}", {"X": 7}) == "{N1}  7"
    assert t.variables("no fields here") == ()


def test_field_without_format_is_a_whole_number():
    assert t.render("ALT {INDICATED_ALTITUDE}", {"INDICATED_ALTITUDE": 3499.6}) == "ALT 3500"


def test_index_comes_before_the_format():
    values = {"GENERAL_ENG_RPM:1": 2350.4, "GENERAL_ENG_RPM:2": 1200}
    assert t.render("{GENERAL_ENG_RPM:1:5.0f}|{GENERAL_ENG_RPM:2:5.0f}", values) == " 2350| 1200"
    assert t.variables("{GENERAL_ENG_RPM:1:5.0f} {GENERAL_ENG_RPM:1}") == ("GENERAL_ENG_RPM:1",)
    assert t.variables("{FLAPS_HANDLE_INDEX:1d}") == ("FLAPS_HANDLE_INDEX",)  # "1d" is a format, not an index
    assert t.variables("{COM_ACTIVE_FREQUENCY:2|freq}") == ("COM_ACTIVE_FREQUENCY:2",)


def test_integer_formats_get_an_int_and_percent_scales():
    assert t.render("{AUTOPILOT_HEADING_LOCK_DIR:03d}", {"AUTOPILOT_HEADING_LOCK_DIR": 7.9}) == "007"
    assert t.render("{ELEVATOR_TRIM_PCT:+4.0%}", {"ELEVATOR_TRIM_PCT": -0.12}) == "-12%"


@pytest.mark.parametrize(
    ("template", "value", "expected"),
    [
        ("{V|hdg}", math.radians(7), "007"),
        ("{V|signed:5}", -1234.5, "-1234"),
        ("{V|signed}", 500, " +500"),
        ("{V|freq}", 118.75, "118.750"),
        ("{V|freq:2}", 110.5, "110.50"),
        ("{V|bcd}", 0x7000, "7000"),
        ("{V|onoff:HDG}", 1, "HDG"),
        ("{V|onoff:HDG}", 1.4e-311, "---"),  # a denormal "false", seen live
        ("{V|either:ON,OFF}", 1, "ON "),
        ("{V|either:ON,OFF}", 0, "OFF"),
        ("{V|lat}", 55.618, "N55 37.08"),
        ("{V|lon}", -12.6508, "W012 39.05"),
        ("{V|hms}", 45296, "12:34:56"),
    ],
)
def test_filters(template, value, expected):
    assert t.render(template, {"V": value}) == expected


def test_missing_values_and_garbage_render_like_zero():
    for values in ({}, {"V": None}, {"V": "garbage"}):
        assert t.render("IAS {V:3.0f} {V|onoff:AP} {V|hdg}", values) == "IAS   0 -- 000"
    assert t.render("{V|hms}", {}) == "--:--:--"


def test_render_clips_to_the_display():
    assert len(t.render("{V:20.0f}", {"V": 1})) == LINE_LEN


@pytest.mark.parametrize(
    ("template", "problem"),
    [
        ("IAS {AIRSPEED", "never closed"),
        ("IAS }", "without its {"),
        ("{lowercase}", "cannot read"),
        ("{V|nosuch}", "unknown filter"),
        ("{V|onoff}", "needs an argument"),
        ("{V:5.0q}", "V:5.0q"),
        ("{V|signed:x}", "V|signed:x"),
        ("ALTITUDE IN FEET {V:5.0f}", "characters, the MFD shows 16"),
        ("TEMP {V:3.0f}°C", "plain ASCII"),
    ],
)
def test_check_says_what_is_wrong(template, problem):
    assert problem in t.check(template)
    if "characters" not in problem and "ASCII" not in problem:
        with pytest.raises(t.TemplateError):
            t.parse(template)


def test_check_with_a_simvar_table():
    known = {"INDICATED_ALTITUDE"}.__contains__
    assert t.check("ALT {INDICATED_ALTITUDE:5.0f}", known) is None
    assert t.check("ALT {INDICATED_ALTITUDE:5.0f}") is None
    assert t.check("ALT {INDICATED_ALTITUD:5.0f}", known) == "unknown SimVar INDICATED_ALTITUD"
    assert t.check("", known) is None
