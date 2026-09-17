import pytest

from x52_simconnect.formatting import LINE_LEN
from x52_simconnect.pages import ALL_VARS, CLOCK_VARS, PAGES, Page, render
from x52_simconnect.sources import demo_values

TITLES = [p.title for p in PAGES]


def test_all_vars_is_unique_and_complete():
    assert len(ALL_VARS) == len(set(ALL_VARS))
    for page in PAGES:
        assert set(page.vars) <= set(ALL_VARS)
    assert set(CLOCK_VARS) <= set(ALL_VARS)


def test_demo_values_cover_every_var():
    assert set(ALL_VARS) <= set(demo_values(0))


@pytest.mark.parametrize("page", PAGES, ids=TITLES)
def test_render_with_demo_data_fits_the_display(page):
    lines = render(page, demo_values(12.5))
    assert len(lines) == 3
    assert all(isinstance(line, str) and len(line) <= LINE_LEN for line in lines)
    assert "format error" not in lines


@pytest.mark.parametrize("page", PAGES, ids=TITLES)
def test_render_with_all_none_does_not_crash(page):
    lines = render(page, dict.fromkeys(ALL_VARS))
    assert len(lines) == 3
    assert all(len(line) <= LINE_LEN for line in lines)
    assert "format error" not in lines


def test_known_renderings():
    v = demo_values(0)
    assert render(PAGES[0], v) == ["IAS 118 GS 124", "ALT  3500 V +500", "HDG 270  TRK 268"]
    assert render(PAGES[1], v) == ["COM1 118.750", "STBY 121.500", "NAV1 110.50 7000"]
    assert render(PAGES[2], v) == ["AP ON  HDG 270", "ALT  5000 V +700", "--- SPD 200"]
    assert render(PAGES[4], v) == ["N55 37.08", "E012 39.05", "AGL  3455 FT"]


def test_render_survives_a_broken_page():
    def boom(_values):
        raise KeyError("MISSING_VAR")

    lines = render(Page("BROKEN", (), boom), {})
    assert lines[0] == "BROKEN"
    assert lines[1] == "format error"
    assert len(lines[2]) <= LINE_LEN
