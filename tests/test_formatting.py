import math

import pytest

from x52_simconnect import formatting as f


@pytest.mark.parametrize("value, expected", [(1, 1.0), ("2.5", 2.5), (None, 0.0), ("x", 0.0), (object(), 0.0)])
def test_num_tolerates_garbage(value, expected):
    assert f.num(value) == expected


def test_num_custom_default():
    assert f.num(None, 7) == 7


def test_deg_wraps_and_rounds():
    assert f.deg(0) == 0
    assert f.deg(math.pi) == 180
    assert f.deg(2 * math.pi) == 0
    assert f.deg(-math.pi / 2) == 270
    assert f.deg(None) == 0


def test_hdg3_is_zero_padded():
    assert f.hdg3(math.radians(7)) == "007"
    assert f.hdg3(math.radians(359.6)) == "000"


def test_signed_has_explicit_sign_and_width():
    assert f.signed(500, 5) == " +500"
    assert f.signed(-12.4, 3) == "-12"
    assert f.signed(None, 4) == "  +0"


def test_freq():
    assert f.freq(118.75) == "118.750"
    assert f.freq(None) == "000.000"


def test_hms_wraps_and_tolerates_none():
    assert f.hms(0) == "00:00:00"
    assert f.hms(12 * 3600 + 34 * 60 + 56.7) == "12:34:56"
    assert f.hms(86400 + 61) == "00:01:01"
    assert f.hms(None) == "--:--:--"


def test_bcd_transponder():
    assert f.bcd(0x7000) == "7000"
    assert f.bcd(0x1200) == "1200"
    assert f.bcd(None) == "0000"


def test_latlon_hemispheres_and_minutes():
    assert f.latlon(55.618, 12.6508) == ("N55 37.08", "E012 39.05")
    assert f.latlon(-33.8688, -151.2093) == ("S33 52.13", "W151 12.56")
    assert f.latlon(None, None) == ("N00 00.00", "E000 00.00")


@pytest.mark.parametrize("value, expected", [(1.0, True), (0.0, False), (1.4e-311, False), (None, False), (0.51, True)])
def test_flag_thresholds_denormals(value, expected):
    assert f.flag(value) is expected


def test_onoff_keeps_width():
    assert f.onoff(1, "HDG") == "HDG"
    assert f.onoff(0, "HDG") == "---"


def test_clip():
    assert f.clip("x" * 20) == "x" * 16
    assert f.clip(123) == "123"


AGES = [(0, " 0s"), (3, " 3s"), (41.9, "41s"), (59, "59s"), (60, " 1m"), (12 * 60 + 5, "12m"), (3600, " 1h")]
AGES += [(400 * 3600, "99h"), (-5, " 0s"), (None, " 0s")]


@pytest.mark.parametrize("seconds, expected", AGES)
def test_age_is_a_three_character_column(seconds, expected):
    assert f.age(seconds) == expected
    assert len(f.age(seconds)) == 3
