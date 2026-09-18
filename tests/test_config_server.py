"""The config UI's server and store, over real HTTP on an ephemeral port. No stick, no sim."""

import json
import urllib.error
import urllib.request

import pytest

from x52_simconnect import config as cfg
from x52_simconnect.config_server import ConfigServer, ConfigStore

KNOWN = {"AIRSPEED_INDICATED", "INDICATED_ALTITUDE"}
NEW = {
    "page": [{"title": "mine", "lines": ["IAS {AIRSPEED_INDICATED:3.0f}", "", ""]}],
    "events": {"hidden": ["LIGHT_NAV"], "key_events": False, "ignored_key_events": ["BRAKES"]},
}


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path):
    units = {"AIRSPEED_INDICATED": "Knots", "INDICATED_ALTITUDE": "Feet"}
    return ConfigStore(tmp_path / "config.toml", known=KNOWN.__contains__, units=units.get, clock=Clock())


@pytest.fixture
def server(store):
    srv = ConfigServer(store, port=0).start()
    yield srv
    srv.stop()


def call(server, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if headers is None and body is not None else headers or {}
    req = urllib.request.Request(server.url.rstrip("/") + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_the_page_and_the_state(server, store):
    status, body = call(server, "/")
    assert status == 200 and b"X52 MFD setup" in body
    status, body = call(server, "/api/state")
    state = json.loads(body)
    assert state["config"] == cfg.to_dict(cfg.Config()) == state["defaults"]
    assert state["path"] == str(store.path)
    assert {r["group"] for r in state["rules"]} >= {"Lights", "Autopilot"}
    assert state["fields"][0] == {
        "group": "Flight",
        "label": "Indicated airspeed, kt",
        "template": "{AIRSPEED_INDICATED:3.0f}",
        "unit": "Knots",
    }


def test_save_writes_the_file_and_queues_the_config_once(server, store):
    assert store.take() is None
    status, body = call(server, "/api/config", NEW)
    assert status == 200
    assert json.loads(body)["config"]["page"][0]["title"] == "MINE"
    saved = cfg.load(store.path)
    assert saved.events == cfg.EventsConfig(frozenset({"LIGHT_NAV"}), False, frozenset({"BRAKES"}))
    assert store.take() == saved
    assert store.take() is None


def test_a_bad_config_is_refused_and_nothing_changes(server, store):
    bad = {"page": [{"title": "X", "lines": ["{NOT_A_SIMVAR}", "{BROKEN", ""]}]}
    status, body = call(server, "/api/config", bad)
    assert status == 400
    assert [e["path"] for e in json.loads(body)["errors"]] == ["page.0.lines.0", "page.0.lines.1"]
    assert not store.path.exists()
    assert store.take() is None


def test_preview_renders_with_live_values_and_reports_problems(server, store):
    store.live = {"values": {"AIRSPEED_INDICATED": 97.4, "INDICATED_ALTITUDE": None}}
    pages = [{"lines": ["IAS {AIRSPEED_INDICATED:3.0f}", "{BROKEN", "{NOT_A_SIMVAR}"]}]
    status, body = call(server, "/api/preview", {"pages": pages})
    lines = json.loads(body)["pages"][0]["lines"]
    assert status == 200
    assert lines[0] == {"text": "IAS  97", "error": None, "units": {"AIRSPEED_INDICATED": "Knots"}}
    assert lines[1]["text"] == "" and "never closed" in lines[1]["error"]
    assert lines[2]["error"] == "unknown SimVar NOT_A_SIMVAR"


def test_live_leaves_out_the_raw_values(server, store):
    store.live = {"mfd": ["a", "b", "c"], "mode": 3, "values": {"AIRSPEED_INDICATED": 1.0}}
    assert json.loads(call(server, "/api/live")[1]) == {"mfd": ["a", "b", "c"], "mode": 3}


def test_only_local_json_requests_are_served(server):
    assert call(server, "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert call(server, "/api/config", NEW, headers={"Content-Type": "text/plain"})[0] == 415
    assert call(server, "/api/nope")[0] == 404


def test_a_hand_edited_file_is_picked_up_and_a_broken_one_ignored(store):
    clock = store._clock
    assert store.take() is None
    store.path.write_text(cfg.dumps(cfg.from_dict(NEW)), encoding="utf-8")
    clock.now += 1
    assert store.take() is None  # not looked at yet
    clock.now += 2
    assert store.take() == cfg.from_dict(NEW)
    store.path.write_text("[[page]\nbroken", encoding="utf-8")
    clock.now += 3
    assert store.take() is None
    assert "not valid TOML" in store.file_error
    assert store.config == cfg.from_dict(NEW)  # the working config stays


def test_a_broken_file_at_start_means_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[page]]\ntitle = "X"\nlines = ["{BROKEN"]\n', encoding="utf-8")
    store = ConfigStore(path)
    assert store.config == cfg.Config()
    assert "page.0.lines.0" in store.file_error
