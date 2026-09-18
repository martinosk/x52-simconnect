import pytest

from x52_simconnect.apps import ALL_VARS, build_apps
from x52_simconnect.bridge import Bridge, parse_args, run, waiting_screen
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
    assert lines == ["MODE 2 COMMS", "see spec 05", ""]
    lines = bridge.step(stick_mode=2, values=VALUES, now=2)
    assert lines == ["COMMS", "see spec 05", ""]
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


def written(fake_mfd):
    """The line index of every line write, in order."""
    return [args[0] for name, args in fake_mfd.calls if name == "set_line"]


def settled(fake_mfd, **options):
    """A bridge whose first screen is on the MFD already, with the fake's log emptied."""
    bridge = make_bridge(fake_mfd, **options)
    for now in (-3.0, -2.0, -1.0):
        bridge.step(stick_mode=1, values=VALUES, now=now)
    fake_mfd.calls.clear()
    return bridge


def test_firmware_buttons_hold_writes_then_redraw_all_three_lines_a_tick_apart(fake_mfd):
    bridge = settled(fake_mfd)
    bridge.step(presses=["START_STOP"], stick_mode=1, values=VALUES, now=0)
    bridge.step(stick_mode=1, values=VALUES, now=0.2)
    assert written(fake_mfd) == []  # the firmware is about to draw over it
    for now in (0.3, 0.55, 0.8, 1.05):
        bridge.step(stick_mode=1, values=VALUES, now=now)
    assert written(fake_mfd)[:3] == [0, 1, 2]


def test_other_buttons_cost_no_writes_and_a_mode_change_goes_out_a_line_per_tick(fake_mfd):
    bridge = settled(fake_mfd)
    bridge.step(presses=["FIRE"], stick_mode=1, values=VALUES, held=["FIRE"], now=0)
    bridge.step(stick_mode=1, values=VALUES, held=[], now=0.25)
    assert written(fake_mfd) == []
    for now in (1.0, 1.25, 1.5):
        bridge.step(stick_mode=2, values=VALUES, now=now)
    assert written(fake_mfd) == [0, 1, 2]
    assert bridge.display.on_screen == ["MODE 2 COMMS", "see spec 05", ""]


def test_live_values_go_out_one_line_per_write_interval(fake_mfd):
    bridge = settled(fake_mfd)
    for i in range(41):  # 10 s of a moving demo flight at 4 Hz
        bridge.step(stick_mode=1, values=demo_values(i * 0.25), now=i * 0.25)
    assert len(written(fake_mfd)) <= 11  # one a second, never two in a tick


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


def test_event_log_collects_in_the_background_and_shows_on_mode_3(fake_mfd):
    bridge = make_bridge(fake_mfd)
    bridge.step(stick_mode=1, values=demo_values(0), now=0)
    bridge.step(stick_mode=1, values=demo_values(3), now=3)  # BATTERY ON while showing the pages
    assert bridge.step(stick_mode=1, values=demo_values(5), now=5) == render(PAGES[0], demo_values(5))
    bridge.step(stick_mode=3, values=demo_values(5), now=6)  # banner
    assert bridge.step(stick_mode=3, values=demo_values(5), now=7) == ["BEACON ON", "BATTERY ON", ""]


def test_events_reach_the_log_and_show_as_ev_when_unexplained(fake_mfd):
    bridge = make_bridge(fake_mfd, forced_mode=3)
    bridge.step(values=VALUES, now=0)
    bridge.step(values=VALUES, now=1, events=["FLAPS_DECR"])
    assert bridge.step(values=VALUES, now=1.5)[0] == "EV FLAPS_DECR"


def test_held_reset_jumps_to_the_newest_entry(fake_mfd):
    bridge = make_bridge(fake_mfd, forced_mode=3)
    log = bridge.apps[3]
    bridge.step(values=VALUES, now=0)
    for i, text in enumerate("ABCDEF"):
        log.add(float(i), text)
    bridge.step(presses=["START_STOP"] * 3, values=VALUES, now=6)
    assert log.scroll == 3
    bridge.step(presses=["RESET"], values=VALUES, held=["RESET"], now=7)
    bridge.step(values=VALUES, held=["RESET"], now=7.5)
    assert log.scroll == 2
    lines = bridge.step(values=VALUES, held=["RESET"], now=8)
    assert log.scroll == 0
    assert lines[0] == "F"
    bridge.step(values=VALUES, held=[], now=8.25)  # released
    assert bridge._down == {}


def test_mode_change_forgets_held_buttons(fake_mfd):
    bridge = make_bridge(fake_mfd)
    bridge.step(presses=["RESET"], stick_mode=1, values=VALUES, held=["RESET"], now=0)
    bridge.step(stick_mode=3, values=VALUES, held=["RESET"], now=1)
    assert bridge._down == {}


def test_events_banner_mirrors_new_entries_in_mode_1(fake_mfd):
    bridge = make_bridge(fake_mfd, events_banner=True)
    bridge.step(stick_mode=1, values=demo_values(0), now=0)
    assert bridge.step(stick_mode=1, values=demo_values(3), now=3)[0] == "BATTERY ON"
    assert bridge.step(stick_mode=1, values=demo_values(3), now=4) == render(PAGES[0], demo_values(3))


def test_parse_args_events_banner():
    assert parse_args(["--events-banner"]).events_banner
    assert not parse_args([]).events_banner


def test_demo_source_scripts_key_events_across_the_loop():
    clock = iter([0.0, 36.0, 38.0, 92.0 + 40.0])
    src = DemoSource(clock=lambda: next(clock))
    assert src.events() == []
    assert src.events() == ["FLAPS_DECR"]
    assert src.events() == ["COM_STBY_RADIO_SWAP", "FLAPS_DECR"]  # 56 s of loop 1, then 37 s of loop 2


def test_refuses_to_start_beside_logitechs_driver(monkeypatch):
    monkeypatch.setattr("x52_simconnect.bridge.logitech_driver.installed", lambda: True)
    monkeypatch.setattr("x52_simconnect.bridge.X52Mfd", lambda: pytest.fail("the stick must not be opened"))
    with pytest.raises(SystemExit, match="Logitech"):
        run(parse_args(["--no-ui"]))
