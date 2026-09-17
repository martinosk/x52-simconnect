"""Key-event subscription, dispatch and sending against a fake SimConnect DLL. Skipped where the SimConnect
package cannot be imported (it needs ctypes.wintypes, i.e. Windows)."""

import ctypes

import pytest

SimConnectEnum = pytest.importorskip("SimConnect.Enum")

from x52_simconnect.sim_events import FIRST_ID, GROUP_ID, SimEvents  # noqa: E402
from x52_simconnect.sim_feed import _HookedSimConnect  # noqa: E402


class FakeDll:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            return 0x80004005 if name in self.fail else 0

        return call


class FakeSm:
    def __init__(self, fail=()):
        self.dll = FakeDll(fail)
        self.hSimConnect = "handle"
        self.event_handlers = {}

    @staticmethod
    def IsHR(hr, value):  # noqa: N802 - Python-SimConnect's spelling
        return hr == value


def recv_event(event_id, data=0):
    evt = SimConnectEnum.SIMCONNECT_RECV_EVENT()
    evt.dwID = SimConnectEnum.SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EVENT
    evt.uGroupID = GROUP_ID
    evt.uEventID = event_id
    evt.dwData = data
    return evt


def test_subscribe_maps_groups_and_prioritises():
    sm = FakeSm()
    ev = SimEvents(sm)
    ev.subscribe(["FLAPS_INCR", "GEAR_TOGGLE"])
    assert ev.ids == {"FLAPS_INCR": FIRST_ID, "GEAR_TOGGLE": FIRST_ID + 1}
    names = [name for name, _ in sm.dll.calls]
    assert names == [
        "MapClientEventToSimEvent",
        "AddClientEventToNotificationGroup",
        "MapClientEventToSimEvent",
        "AddClientEventToNotificationGroup",
        "SetNotificationGroupPriority",
    ]
    assert sm.dll.calls[0][1] == ("handle", FIRST_ID, b"FLAPS_INCR")
    assert sm.dll.calls[1][1] == ("handle", GROUP_ID, FIRST_ID, False)  # not maskable: the sim still acts on it
    assert set(sm.event_handlers) == {FIRST_ID, FIRST_ID + 1}


def test_subscribe_reports_failures():
    ev = SimEvents(FakeSm(fail={"MapClientEventToSimEvent"}))
    with pytest.raises(RuntimeError, match="map FLAPS_INCR"):
        ev.subscribe(["FLAPS_INCR"])


def test_events_are_queued_from_the_dispatch_thread_and_taken_in_order():
    clock = iter([10.0, 11.0])
    ev = SimEvents(FakeSm(), clock=lambda: next(clock))
    ev.subscribe(["FLAPS_INCR", "GEAR_TOGGLE"])
    ev._on_event(recv_event(FIRST_ID + 1))
    ev._on_event(recv_event(FIRST_ID, data=3))
    assert list(ev.fired) == [("GEAR_TOGGLE", 0, 10.0), ("FLAPS_INCR", 3, 11.0)]
    assert ev.take() == ["GEAR_TOGGLE", "FLAPS_INCR"]
    assert ev.take() == []


def test_send_maps_on_first_use_and_transmits():
    sm = FakeSm()
    ev = SimEvents(sm)
    ev.send("AP_MASTER")
    ev.send("AP_MASTER", 1)
    names = [name for name, _ in sm.dll.calls]
    assert names == ["MapClientEventToSimEvent", "TransmitClientEvent", "TransmitClientEvent"]
    assert sm.dll.calls[2][1][1:4] == (SimConnectEnum.SIMCONNECT_OBJECT_ID_USER, FIRST_ID, 1)


def hooked_sm():
    """A _HookedSimConnect without a DLL: only the dispatch routing is exercised."""
    sm = _HookedSimConnect.__new__(_HookedSimConnect)
    sm.data_handlers = {}
    sm.event_handlers = {}
    return sm


def test_dispatch_routes_known_event_ids_to_the_handler():
    sm = hooked_sm()
    seen = []
    sm.event_handlers[FIRST_ID] = lambda evt: seen.append((evt.uEventID, evt.dwData))
    evt = recv_event(FIRST_ID, data=7)
    sm.my_dispatch_proc(ctypes.pointer(evt), ctypes.sizeof(evt), None)
    assert seen == [(FIRST_ID, 7)]


def test_dispatch_passes_unknown_event_ids_to_the_package():
    sm = hooked_sm()
    handled = []
    sm.handle_id_event = lambda evt: handled.append(evt.uEventID)
    evt = recv_event(0)  # Python-SimConnect's own EVENT_SIM_START
    sm.my_dispatch_proc(ctypes.pointer(evt), ctypes.sizeof(evt), None)
    assert handled == [0]
