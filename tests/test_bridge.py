import pytest

from x52_simconnect.apps import ALL_VARS, build_apps
from x52_simconnect.bridge import Bridge, parse_args, waiting_screen
from x52_simconnect.display import Display
from x52_simconnect.pages import PAGES, render
from x52_simconnect.sources import DemoSource, demo_values

VALUES = demo_values(0)


def make_bridge(fake_mfd, forced_mode=None, **pages_options):
    display = Display(fake_mfd, banner_seconds=0.8, redraw_delay=0.3, clock=lambda: 0.0)
    return Bridge(display, build_apps(display, **pages_options), forced_mode=forced_mode)


def page_lines(index):
    return render(PAGES[index], VALUES)


def test_waiting_screen_shape():
    lines = waiting_screen()
    assert len(lines) == 3
    assert all(len(line) <= 16 for line in lines)


def test_no_values_shows_waiting_screen(fake_mfd):
    bridge = make_bridge(fake_mfd)
    assert bridge.step(values=None, now=0)[1] == "waiting for sim"


def test_startup_is_mode_1_page_1_without_a_stick_report(fake_mfd):
    bridge = make_bridge(fake_mfd)
    assert bridge.step(stick_mode=None, values=VALUES, now=0) == page_lines(0)
    assert bridge.display.mode == 1


def test_selector_switches_app_with_banner_then_shows_the_app(fake_mfd):
    bridge = make_bridge(fake_mfd)
    bridge.step(stick_mode=1, values=VALUES, now=0)
    lines = bridge.step(stick_mode=2, values=VALUES, now=1)
    assert lines == ["MODE 2 COMMS", "see spec 05", "Z 12:00:00"]
    lines = bridge.step(stick_mode=2, values=VALUES, now=2)
    assert lines == ["COMMS", "see spec 05", "Z 12:00:00"]
    lines = bridge.step(stick_mode=3, values=VALUES, now=3)
    assert lines[0] == "MODE 3 EVENTS"
    assert bridge.step(stick_mode=3, values=VALUES, now=4)[0] == "EVENTS"


def test_each_app_keeps_its_state_across_mode_switches(fake_mfd):
    bridge = make_bridge(fake_mfd)
    bridge.step(presses=["START_STOP"], stick_mode=1, values=VALUES, now=0)  # to RADIO
    assert bridge.step(stick_mode=1, values=VALUES, now=1) == page_lines(1)
    bridge.step(stick_mode=3, values=VALUES, now=2)
    bridge.step(presses=["START_STOP", "RESET"], stick_mode=3, values=VALUES, now=3)  # not paging
    bridge.step(stick_mode=1, values=VALUES, now=4)  # banner MODE 1 PAGES
    assert bridge.step(stick_mode=1, values=VALUES, now=5) == page_lines(1)


def test_scripted_sequence_of_modes_and_presses(fake_mfd):
    bridge = make_bridge(fake_mfd)
    script = [
        # (presses, selector, expected line 1)
        ([], None, page_lines(0)[0]),
        (["START_STOP"], 1, f"P2/{len(PAGES)} RADIO"),
        ([], 1, page_lines(1)[0]),
        (["RESET"], 1, f"P1/{len(PAGES)} FLIGHT"),
        ([], 2, "MODE 2 COMMS"),
        ([], 2, "COMMS"),
        (["START_STOP"], 2, "COMMS"),
        ([], 1, "MODE 1 PAGES"),
        ([], 1, page_lines(0)[0]),
    ]
    for i, (presses, selector, expected) in enumerate(script):
        assert bridge.step(presses, selector, VALUES, now=float(i))[0] == expected, f"step {i}"


def test_forced_mode_ignores_the_selector(fake_mfd):
    bridge = make_bridge(fake_mfd, forced_mode=3)
    assert bridge.step(stick_mode=None, values=VALUES, now=0)[0] == "MODE 3 EVENTS"
    assert bridge.step(stick_mode=1, values=VALUES, now=1)[0] == "EVENTS"
    assert bridge.step(stick_mode=2, values=VALUES, now=2)[0] == "EVENTS"
    assert bridge.display.mode == 3


def test_firmware_buttons_force_a_redraw_after_the_delay(fake_mfd):
    bridge = make_bridge(fake_mfd)
    bridge.step(presses=["START_STOP"], stick_mode=1, values=VALUES, now=0)
    bridge.step(stick_mode=1, values=VALUES, now=0.2)
    bridge.step(stick_mode=1, values=VALUES, now=0.3)
    forces = [args[1] for name, args in fake_mfd.calls if name == "set_lines"]
    assert forces == [False, False, True]


def test_cycle_advances_pages_while_in_mode_1(fake_mfd):
    bridge = make_bridge(fake_mfd, cycle=5)
    bridge.step(stick_mode=1, values=VALUES, now=0)
    assert bridge.step(stick_mode=1, values=VALUES, now=4) == page_lines(0)
    assert bridge.step(stick_mode=1, values=VALUES, now=5)[0] == f"P2/{len(PAGES)} RADIO"
    assert bridge.step(stick_mode=1, values=VALUES, now=6) == page_lines(1)


def test_parse_args_defaults_and_uppercases_buttons():
    args = parse_args(["--next", "mouse_scroll_up", "--prev", "mouse_scroll_dn"])
    assert (args.next, args.prev, args.home) == ("MOUSE_SCROLL_UP", "MOUSE_SCROLL_DN", "")
    assert args.page == 1 and args.cycle == 0 and args.mode is None and not args.demo


def test_parse_args_mode():
    assert parse_args(["--mode", "2"]).mode == 2
    with pytest.raises(SystemExit):
        parse_args(["--mode", "4"])


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
