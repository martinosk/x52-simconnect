"""The config file: defaults, validation with paths the UI can point at, TOML round trip, applying it to apps."""

import tomllib

import pytest

from x52_simconnect import config as cfg
from x52_simconnect import templates
from x52_simconnect.apps import ALL_VARS, build_apps, configure_apps, feed_vars
from x52_simconnect.display import Display
from x52_simconnect.event_rules import RULES
from x52_simconnect.formatting import LINE_LEN
from x52_simconnect.pages import PAGES, render
from x52_simconnect.sources import demo_values

THROTTLE = "GENERAL_ENG_THROTTLE_LEVER_POSITION:1"


def test_defaults_are_the_built_in_pages_and_everything_shown():
    config = cfg.Config()
    assert [p.title for p in config.pages] == [p.title for p in PAGES]
    built = config.build_pages()
    assert [render(p, demo_values(0)) for p in built] == [render(p, demo_values(0)) for p in PAGES]
    assert config.events == cfg.EventsConfig(frozenset(), True, frozenset())
    assert feed_vars(config) == ALL_VARS
    assert cfg.from_dict({}) == config


def test_toml_round_trip(tmp_path):
    pages = (cfg.PageConfig("JET", ('N1 {TURB_ENG_N1:1:5.1f} "%"', "back\\slash", "")),)
    config = cfg.Config(pages, cfg.EventsConfig(frozenset({THROTTLE}), False, frozenset({"BRAKES"})))
    text = cfg.dumps(config)
    assert tomllib.loads(text)["page"][0]["lines"][0] == 'N1 {TURB_ENG_N1:1:5.1f} "%"'
    assert cfg.loads(text) == config
    path = tmp_path / "nested" / "config.toml"
    cfg.save(path, config)
    assert cfg.load(path) == config
    assert cfg.load(tmp_path / "missing.toml") == cfg.Config()


def test_feed_vars_adds_what_new_pages_read():
    config = cfg.from_dict({"page": [{"title": "jet", "lines": ["N1 {TURB_ENG_N1:1:5.1f}"]}]})
    assert config.pages[0] == cfg.PageConfig("JET", ("N1 {TURB_ENG_N1:1:5.1f}", "", ""))
    assert feed_vars(config) == (*ALL_VARS, "TURB_ENG_N1:1")


def test_errors_carry_the_path_of_the_input():
    data = {
        "page": [
            {"title": "OK", "lines": ["IAS {AIRSPEED_INDICATED:3.0f}", "{BROKEN", "{NOT_A_SIMVAR}"]},
            {"title": "A TITLE THAT IS TOO LONG", "lines": ["x"] * 4},
            {"lines": []},
        ],
        "events": {"hidden": ["NO_SUCH_RULE"], "key_events": "yes", "ignored_key_events": ["brakes", "not ok!"]},
    }
    with pytest.raises(cfg.ConfigError) as e:
        cfg.from_dict(data, known={"AIRSPEED_INDICATED"}.__contains__)
    paths = {err["path"] for err in e.value.errors}
    assert paths == {
        "page.0.lines.1",
        "page.0.lines.2",
        "page.1.title",
        "page.1.lines",
        "page.2.title",
        "events.hidden",
        "events.key_events",
        "events.ignored",
    }


def test_no_pages_and_bad_toml_are_rejected():
    with pytest.raises(cfg.ConfigError, match="at least one page"):
        cfg.from_dict({"page": []})
    with pytest.raises(cfg.ConfigError, match="not valid TOML"):
        cfg.loads("[[page]\ntitle = ")


def test_every_catalogue_field_is_a_valid_template_that_fits():
    for _group, label, text in cfg.FIELDS:
        assert templates.check(text) is None, label
        assert len(templates.render(text, demo_values(12.5))) <= LINE_LEN
        assert len(templates.variables(text)) == 1


def test_rule_catalogue_lists_every_rule_with_a_label_and_group():
    catalogue = cfg.rule_catalogue()
    assert [r["var"] for r in catalogue] == [r.var for r in RULES]
    assert all(r["label"] and r["group"] and r["example"] for r in catalogue)
    assert len({r["label"] for r in catalogue}) == len(catalogue)
    examples = {r["var"]: r["example"] for r in catalogue}
    assert examples[THROTTLE] == "THR 75%" and examples["TRANSPONDER_CODE:1"] == "SQK 7000"


def test_configure_apps_swaps_pages_and_filters_the_log(fake_mfd):
    apps = build_apps(Display(fake_mfd), start=4)
    config = cfg.from_dict(
        {
            "page": [{"title": "ONE", "lines": ["IAS {AIRSPEED_INDICATED:3.0f}"]}, {"title": "TWO", "lines": ["two"]}],
            "events": {"hidden": [THROTTLE], "key_events": True, "ignored_key_events": ["BRAKES"]},
        }
    )
    configure_apps(apps, config)
    assert apps[1].page == 1  # was on page 5 of 5; stays on the last one there is
    assert apps[1].render(demo_values(0)) == ["two", "", ""]

    log = apps[3]
    values = demo_values(0)
    log.observe(values, 0.0)
    values[THROTTLE] = 100
    log.observe(values, 1.0, events=["THROTTLE_FULL"])
    log.observe(values, 2.0)
    log.observe(values, 3.0, events=["BRAKES", "FLAPS_DECR"])
    log.observe(values, 4.0)
    assert [text for _, text in log.history] == ["EV FLAPS_DECR"]  # no THR line, and no EV THROTTLE_FULL for it


def test_key_events_can_be_switched_off(fake_mfd):
    apps = build_apps(Display(fake_mfd))
    configure_apps(apps, cfg.from_dict({"events": {"key_events": False}}))
    apps[3].observe(demo_values(0), 0.0, events=["FLAPS_DECR"])
    apps[3].observe(demo_values(0), 1.0)
    assert not apps[3].history
