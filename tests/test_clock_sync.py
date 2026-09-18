from datetime import datetime

import pytest

from x52_simconnect.clock_sync import BRIGHTNESS, ClockSync, offset_minutes

NOON = datetime(2026, 9, 17, 12, 0)


def values(**overrides):
    base = {
        "ZULU_TIME": 10 * 3600 + 30 * 60,  # 10:30Z
        "LOCAL_TIME": 12 * 3600 + 30 * 60,  # 12:30 local (UTC+2)
        "ZULU_DAY_OF_MONTH": 17,
        "ZULU_MONTH_OF_YEAR": 9,
        "ZULU_YEAR": 2026,
        "TIME_OF_DAY": 1,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "a, b, expected",
    [(120, 0, 120), (0, 120, -120), (23 * 60, 0, -60), (0, 23 * 60, 60), (720, 0, -720), (719, 0, 719)],
)
def test_offset_minutes_wraps_to_half_day(a, b, expected):
    assert offset_minutes(a, b) == expected


def test_update_sets_clocks_date_and_brightness(fake_mfd):
    ClockSync(fake_mfd, now=lambda: NOON).update(values())
    assert fake_mfd.calls == [
        ("set_clock", (10, 30)),
        ("set_clock_offset", (2, 120)),
        ("set_clock_offset", (3, 90)),  # wall clock 12:00 vs 10:30Z
        ("set_date", (17, 9, 2026)),
        ("set_brightness", (BRIGHTNESS[1],)),
        ("set_led_brightness", (BRIGHTNESS[1],)),
    ]


def test_without_sim_time_only_the_display_is_lit(fake_mfd):
    sync = ClockSync(fake_mfd)
    sync.update(None)
    assert fake_mfd.calls == [("set_brightness", (128,)), ("set_led_brightness", (128,))]
    fake_mfd.calls.clear()
    sync.update({"ZULU_TIME": None, "TIME_OF_DAY": 3})
    assert fake_mfd.calls == [("set_brightness", (128,)), ("set_led_brightness", (128,))]


def test_without_sim_time_and_brightness_disabled_nothing_is_touched(fake_mfd):
    ClockSync(fake_mfd, brightness=False).update(None)
    assert fake_mfd.calls == []


def test_clock_and_brightness_can_be_disabled(fake_mfd):
    ClockSync(fake_mfd, clock=False, now=lambda: NOON).update(values(TIME_OF_DAY=3))
    assert fake_mfd.calls == [("set_brightness", (BRIGHTNESS[3],)), ("set_led_brightness", (BRIGHTNESS[3],))]

    fake_mfd.calls.clear()
    ClockSync(fake_mfd, brightness=False, now=lambda: NOON).update(values())
    assert [name for name, _ in fake_mfd.calls] == ["set_clock", "set_clock_offset", "set_clock_offset", "set_date"]


def test_unknown_time_of_day_uses_default(fake_mfd):
    ClockSync(fake_mfd, clock=False).update(values(TIME_OF_DAY=None))
    assert fake_mfd.calls[0] == ("set_brightness", (BRIGHTNESS[1],))
    fake_mfd.calls.clear()
    ClockSync(fake_mfd, clock=False).update(values(TIME_OF_DAY=99))
    assert fake_mfd.calls[0] == ("set_brightness", (128,))
