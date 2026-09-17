"""SimFeed packet handling and stale-data logic, without a sim. Skipped where the SimConnect
package cannot be imported (it needs ctypes.wintypes, i.e. Windows)."""

import ctypes
import time

import pytest

SimConnectEnum = pytest.importorskip("SimConnect.Enum")

from x52_simconnect import sources  # noqa: E402
from x52_simconnect.sim_feed import SimFeed  # noqa: E402

NAMES = ["INDICATED_ALTITUDE", "AIRSPEED_INDICATED", "ZULU_TIME"]


def packet(values):
    obj = SimConnectEnum.SIMCONNECT_RECV_SIMOBJECT_DATA()
    obj.dwDefineCount = len(values)
    arr = (ctypes.c_double * len(values))(*values)
    ctypes.memmove(obj.dwData, arr, ctypes.sizeof(arr))
    return obj


def test_feed_starts_empty_and_disconnected():
    feed = SimFeed(NAMES + ["ZULU_TIME"])  # duplicates are dropped
    assert feed.names == NAMES
    assert not feed.connected
    assert feed.age() == float("inf")
    assert feed.get(["ZULU_TIME", "OTHER"]) == {"ZULU_TIME": None, "OTHER": None}


def test_on_data_unpacks_doubles_in_definition_order():
    feed = SimFeed(NAMES)
    feed._on_data(packet([3500.0, 118.5, 45000.0]))
    assert feed.values == {"INDICATED_ALTITUDE": 3500.0, "AIRSPEED_INDICATED": 118.5, "ZULU_TIME": 45000.0}
    assert feed.packets == 1
    assert feed.age() < 1


def test_on_data_ignores_packets_of_the_wrong_size():
    feed = SimFeed(NAMES)
    feed._on_data(packet([1.0, 2.0]))
    assert feed.values == dict.fromkeys(NAMES)
    assert feed.packets == 0


def test_close_without_connection_resets_values():
    feed = SimFeed(NAMES)
    feed._on_data(packet([1.0, 2.0, 3.0]))
    feed.close()
    assert feed.values == dict.fromkeys(NAMES)
    assert feed.age() == float("inf")


class FakeFeed:
    def __init__(self, names, events=()):
        self.names = list(names)
        self.event_names = list(events)
        self.connected = False
        self.closed = 0
        self._last = None

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False
        self.closed += 1

    def age(self):
        return float("inf") if self._last is None else time.time() - self._last

    def get(self, names):
        return dict.fromkeys(names, 1.0)

    def take_events(self):
        return ["FLAPS_INCR"] if self.connected else []


@pytest.fixture
def sim_source(monkeypatch):
    import x52_simconnect.sim_feed as sim_feed

    monkeypatch.setattr(sim_feed, "SimFeed", FakeFeed)
    return sources.SimSource(NAMES)


def test_sim_source_returns_none_until_first_packet(sim_source):
    assert sim_source.ensure()
    assert sim_source.read(NAMES) is None
    sim_source.feed._last = time.time()
    assert sim_source.read(NAMES) == dict.fromkeys(NAMES, 1.0)


def test_sim_source_passes_events_through(sim_source):
    assert sim_source.events() == []
    sim_source.ensure()
    assert sim_source.events() == ["FLAPS_INCR"]
    assert sim_source.feed.event_names == list(sources.ALL_EVENTS)


def test_sim_source_reconnects_after_stale_data(sim_source):
    sim_source.ensure()
    sim_source.feed._last = time.time() - sources.STALE_SECONDS - 1
    assert sim_source.read(NAMES) is None
    assert sim_source.feed.closed == 1
    assert not sim_source.connected


def test_sim_source_rate_limits_connection_attempts(monkeypatch, sim_source):
    attempts = []

    def failing_connect():
        attempts.append(1)
        raise ConnectionError("sim down")

    sim_source.feed.connect = failing_connect
    assert not sim_source.ensure()
    assert not sim_source.ensure()  # within RETRY_SECONDS: no second attempt
    assert len(attempts) == 1
