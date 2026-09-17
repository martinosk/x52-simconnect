import pytest

from x52_simconnect.bridge import Pager, parse_args, waiting_screen
from x52_simconnect.pages import ALL_VARS, PAGES
from x52_simconnect.sources import DemoSource


def test_pager_wraps_both_ways_and_reports_changes():
    p = Pager(len(PAGES), start=0, banner_seconds=1)
    assert p.prev(now=10) is True
    assert p.page == len(PAGES) - 1
    assert p.next(now=11) is True
    assert p.page == 0
    assert p.goto(0, now=12) is False  # already there: no banner
    assert p.banner(now=12) is None


def test_pager_banner_expires():
    p = Pager(len(PAGES), banner_seconds=0.8)
    p.next(now=100.0)
    assert p.banner(now=100.5) == f"P2/{len(PAGES)} {PAGES[1].title}"
    assert p.banner(now=100.8) is None


def test_pager_start_is_wrapped():
    assert Pager(5, start=7).page == 2
    assert Pager(5, start=-1).page == 4


def test_waiting_screen_shape():
    lines = waiting_screen()
    assert len(lines) == 3
    assert all(len(line) <= 16 for line in lines)


def test_parse_args_defaults_and_uppercases_buttons():
    args = parse_args(["--next", "mouse_scroll_up", "--prev", "mouse_scroll_dn"])
    assert (args.next, args.prev, args.home) == ("MOUSE_SCROLL_UP", "MOUSE_SCROLL_DN", "")
    assert args.page == 1 and args.cycle == 0 and not args.demo


def test_parse_args_rejects_unknown_button():
    with pytest.raises(SystemExit):
        parse_args(["--home", "NOPE"])


def test_demo_source_serves_requested_names_and_moves():
    clock = iter([0.0, 0.0, 30.0])
    src = DemoSource(clock=lambda: next(clock))
    assert src.ensure()
    first = src.read(ALL_VARS)
    later = src.read(["AIRSPEED_INDICATED", "UNKNOWN_VAR"])
    assert set(first) == set(ALL_VARS)
    assert all(v is not None for v in first.values())
    assert later["UNKNOWN_VAR"] is None
    assert later["AIRSPEED_INDICATED"] != first["AIRSPEED_INDICATED"]
    src.close()
