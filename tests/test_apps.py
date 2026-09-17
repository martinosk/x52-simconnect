import pytest

from x52_simconnect.apps import ALL_VARS, APP_CLASSES, PagesApp, build_apps
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


def test_placeholders_show_name_and_sim_time(fake_mfd):
    apps = build_apps(Display(fake_mfd))
    assert apps[2].render(demo_values(0)) == ["COMMS", "see spec 05", "Z 12:00:00"]
    assert apps[3].render(dict.fromkeys(ALL_VARS)) == ["EVENTS", "see spec 02", "Z --:--:--"]


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
