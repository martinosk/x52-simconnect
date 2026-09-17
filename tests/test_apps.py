import pytest

from x52_simconnect.apps import ALL_VARS, APP_CLASSES, EventLogApp, PagesApp, build_apps
from x52_simconnect.display import Display
from x52_simconnect.formatting import LINE_LEN
from x52_simconnect.pages import CLOCK_VARS, PAGES
from x52_simconnect.sources import demo_values


class BannerSpy:
    def __init__(self):
        self.banners = []

    def banner(self, text):
        self.banners.append(text)


def test_all_vars_is_unique_and_covers_every_app_and_the_clock():
    assert len(ALL_VARS) == len(set(ALL_VARS))
    for cls in APP_CLASSES.values():
        assert set(cls.vars) <= set(ALL_VARS)
    assert set(CLOCK_VARS) <= set(ALL_VARS)


def test_demo_values_cover_every_var():
    assert set(ALL_VARS) <= set(demo_values(0))


def test_build_apps_one_per_selector_position(fake_mfd):
    apps = build_apps(Display(fake_mfd))
    assert sorted(apps) == [1, 2, 3]
    assert [apps[m].name for m in (1, 2, 3)] == ["PAGES", "COMMS", "EVENTS"]


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_every_app_renders_with_demo_and_with_no_data(fake_mfd, mode):
    app = build_apps(Display(fake_mfd))[mode]
    for values in (demo_values(12.5), dict.fromkeys(ALL_VARS)):
        lines = app.render(values)
        assert len(lines) == 3
        assert all(isinstance(line, str) and len(line) <= LINE_LEN for line in lines)


def test_placeholder_and_empty_log_show_name_and_sim_time(fake_mfd):
    apps = build_apps(Display(fake_mfd))
    assert apps[2].render(demo_values(0)) == ["COMMS", "see spec 05", "Z 12:00:00"]
    assert apps[3].render(dict.fromkeys(ALL_VARS)) == ["EVENTS", "no events yet", "Z --:--:--"]


def test_pages_app_wraps_both_ways_and_reports_changes():
    spy = BannerSpy()
    app = PagesApp(spy, start=0)
    assert app.prev() is True
    assert app.page == len(PAGES) - 1
    assert app.next() is True
    assert app.page == 0
    assert app.goto(0) is False  # already there: no banner
    assert spy.banners == [f"P{len(PAGES)}/{len(PAGES)} {PAGES[-1].title}", f"P1/{len(PAGES)} FLIGHT"]


def test_pages_app_start_is_wrapped():
    assert PagesApp(BannerSpy(), start=7).page == 7 % len(PAGES)
    assert PagesApp(BannerSpy(), start=-1).page == len(PAGES) - 1


def test_pages_app_button_mapping_and_home():
    app = PagesApp(BannerSpy(), next="MOUSE_SCROLL_UP", prev="MOUSE_SCROLL_DN", home="FUNCTION")
    app.on_button("MOUSE_SCROLL_UP")
    app.on_button("MOUSE_SCROLL_UP")
    assert app.page == 2
    app.on_button("MOUSE_SCROLL_DN")
    assert app.page == 1
    app.on_button("START_STOP")  # not mapped any more
    assert app.page == 1
    app.on_button("FUNCTION")
    assert app.page == 0


def test_pages_app_unmapped_home_is_not_a_button():
    app = PagesApp(BannerSpy())
    assert "" not in app.buttons


def test_pages_app_cycle():
    app = PagesApp(BannerSpy(), cycle=5)
    app.tick(0)
    app.tick(4.9)
    assert app.page == 0
    app.tick(5)
    assert app.page == 1
    app.tick(9)
    assert app.page == 1
    app.tick(10)
    assert app.page == 2


# ------------------------------------------------------------------------------------------ event log
class DisplaySpy(BannerSpy):
    mode = 1


def event_log(**kw):
    app = EventLogApp(DisplaySpy(), **kw)
    app.observe(demo_values(0), 0.0)  # baseline
    return app


def fill(app, texts, start=1.0, step=1.0):
    for i, text in enumerate(texts):
        app.add(start + i * step, text)


def test_event_log_starts_empty():
    app = event_log()
    assert app.render(demo_values(0)) == ["EVENTS", "no events yet", "Z 12:00:00"]


def test_event_log_lines_from_the_feed_newest_first_with_age():
    app = event_log()
    app.observe(demo_values(3), 3.0)  # BATTERY ON
    app.observe(demo_values(5), 5.0)  # BEACON ON
    app.observe(demo_values(5), 8.0)
    assert app.render(demo_values(8)) == [" 3s BEACON ON", " 5s BATTERY ON", ""]
    app.observe(demo_values(5), 125.0)
    assert app.render(demo_values(125)) == [" 2m BEACON ON", " 2m BATTERY ON", ""]


def test_event_log_keeps_collecting_while_not_showing_and_activation_shows_newest():
    app = event_log()
    fill(app, ["A", "B", "C", "D", "E"])
    app.on_button("START_STOP")
    app.on_button("START_STOP")
    app.on_activate()
    app.observe(None, 10.0)
    assert app.render({}) == [" 5s E", " 6s D", " 7s C"]
    assert app.showing


def test_event_log_scrolls_older_and_newer_within_bounds():
    app = event_log()
    fill(app, ["A", "B", "C", "D", "E"])
    app.observe(None, 10.0)
    app.on_button("RESET")  # already at the newest
    assert app.scroll == 0
    for _ in range(5):
        app.on_button("START_STOP")
    assert app.scroll == 2  # the oldest entry is on line 3, no further
    assert app.render({}) == [" 7s C", " 8s B", " 9s A"]
    app.on_button("RESET")
    assert app.render({}) == [" 6s D", " 7s C", " 8s B"]


def test_new_entries_while_scrolled_keep_the_view_and_show_a_marker():
    app = event_log()
    fill(app, ["A", "B", "C", "D", "E"])
    app.on_button("START_STOP")
    app.on_button("START_STOP")
    app.observe(None, 10.0)
    assert app.render({}) == [" 7s C", " 8s B", " 9s A"]
    app.add(10.0, "F")
    app.add(10.0, "G")
    assert app.render({}) == ["+2 NEW C", " 8s B", " 9s A"]  # same entries on screen
    app.on_button("RESET")
    assert app.render({}) == ["+2 NEW D", " 7s C", " 8s B"]
    app.on_button("RESET")
    app.on_button("RESET")
    assert app.render({}) == ["+1 NEW F", " 5s E", " 6s D"]  # one newer entry still above the view
    app.on_button("RESET")
    assert app.render({}) == [" 0s G", " 0s F", " 5s E"]
    assert app.unseen == 0


def test_reset_held_jumps_to_the_newest_once():
    app = event_log()
    fill(app, ["A", "B", "C", "D", "E", "F"])
    app.on_button("START_STOP")
    app.on_button("START_STOP")
    app.on_button("START_STOP")
    assert app.scroll == 3
    app.on_button("RESET")  # the press itself scrolls one newer ...
    app.on_hold("RESET", 0.5)
    assert app.scroll == 2
    app.on_hold("RESET", 1.0)  # ... holding jumps to the top
    assert app.scroll == 0
    app.on_button("START_STOP")
    app.on_hold("RESET", 1.5)  # still the same hold: no second jump
    assert app.scroll == 1
    app.on_hold("START_STOP", 5)  # only the newer button jumps
    assert app.scroll == 1


def test_history_is_capped():
    app = event_log()
    fill(app, [str(i) for i in range(150)])
    assert len(app.history) == EventLogApp.HISTORY
    assert app.history[0][1] == "149"
    for _ in range(200):
        app.on_button("START_STOP")
    assert app.scroll == EventLogApp.HISTORY - 3
    app.add(200.0, "new")  # full and scrolled to the end: the view cannot move further
    assert app.scroll == EventLogApp.HISTORY - 3


def test_key_event_explained_by_a_rule_is_not_logged_twice():
    app = event_log()
    values = demo_values(0)
    app.observe(values, 1.0, events=["FLAPS_INCR"])
    values["FLAPS_HANDLE_INDEX"] = 1
    app.observe(values, 1.25)
    app.observe(values, 2.0)
    assert [text for _, text in app.history] == ["FLAPS 1"]


def test_key_event_that_changes_nothing_is_logged_after_the_grace_period():
    app = event_log()
    values = demo_values(0)
    app.observe(values, 1.0, events=["FLAPS_DECR"])
    app.observe(values, 1.25)
    assert not app.history
    app.observe(values, 1.3)
    assert [text for _, text in app.history] == ["EV FLAPS_DECR"]


def test_repeated_key_event_is_one_line_and_a_moving_value_swallows_them():
    app = event_log()
    values = demo_values(0)
    for i in range(8):  # hat held: ELEV_TRIM_UP every tick, trim at its stop
        app.observe(values, 1.0 + 0.25 * i, events=["ELEV_TRIM_UP"])
    app.observe(values, 4.0)
    assert [text for _, text in app.history] == ["EV ELEV_TRIM_UP"]
    for i in range(8):  # now the trim moves: the settled TRIM line explains the events
        values["ELEVATOR_TRIM_PCT"] = 0.01 * i
        app.observe(values, 10.0 + 0.25 * i, events=["ELEV_TRIM_UP"])
    app.observe(values, 13.0)
    assert [text for _, text in app.history] == ["TRIM +7%", "EV ELEV_TRIM_UP"]


def test_mirror_banners_new_entries_only_while_another_app_shows():
    app = event_log(mirror=True)
    app.add(1.0, "GEAR UP")
    app.on_activate()
    app.add(2.0, "GEAR DOWN")
    app.on_deactivate()
    app.add(3.0, "FLAPS 1")
    assert app.display.banners == ["GEAR UP", "FLAPS 1"]
    assert not event_log().display.banners


def test_event_log_lines_fit_the_display():
    app = event_log()
    app.observe(dict.fromkeys(ALL_VARS), 1.0, events=["TOGGLE_MASTER_ALTERNATOR"])
    app.observe(dict.fromkeys(ALL_VARS), 2.0)
    lines = app.render(dict.fromkeys(ALL_VARS))
    assert lines[0] == " 0s EV TOGGLE_MA"
    assert all(len(line) <= LINE_LEN for line in lines)
