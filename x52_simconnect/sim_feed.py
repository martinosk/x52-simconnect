"""
Streaming SimConnect feed: one data definition holding every SimVar we care about,
pushed by the sim every few visual frames. Reading a value is a dict lookup.

This replaces Python-SimConnect's per-variable one-shot polling (10-100 ms per
variable per refresh). It still uses that package for the DLL bindings, the
dispatch thread and the SimVar name/unit table, but hooks the dispatch to
capture SIMCONNECT_RECV_ID_SIMOBJECT_DATA, which the package ignores.

    feed = SimFeed(["INDICATED_ALTITUDE", "COM_ACTIVE_FREQUENCY:1", ...])
    feed.connect()              # raises ConnectionError if the sim is not up
    feed.values["INDICATED_ALTITUDE"]   # float, or None before the first packet
    feed.age()                  # seconds since the last packet

    feed = SimFeed([...], events=["FLAPS_INCR", "GEAR_TOGGLE"])   # also be told when these key events fire
    feed.take_events()          # -> names fired since the last call (see sim_events.py)

CLI:  python -m x52_simconnect.sim_feed [SIMVAR ...]     # print the feed for 3 s
"""

import time
from ctypes import POINTER, c_double, cast

from SimConnect import AircraftRequests, SimConnect
from SimConnect.Constants import SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_UNUSED
from SimConnect.Enum import (
    SIMCONNECT_DATA_REQUEST_FLAG,
    SIMCONNECT_DATATYPE,
    SIMCONNECT_PERIOD,
    SIMCONNECT_RECV_EVENT,
    SIMCONNECT_RECV_ID,
    SIMCONNECT_RECV_SIMOBJECT_DATA,
)

from .sim_events import SimEvents


class _HookedSimConnect(SimConnect):
    """Python-SimConnect with hooks for SIMOBJECT_DATA packets (bulk definitions) and for key-event
    notifications with ids the package does not know (see sim_events.py)."""

    def __init__(self, *a, **k):
        self.data_handlers = {}  # request id -> callable(SIMCONNECT_RECV_SIMOBJECT_DATA)
        self.event_handlers = {}  # client event id -> callable(SIMCONNECT_RECV_EVENT)
        super().__init__(*a, **k)

    def my_dispatch_proc(self, pData, cbData, pContext):
        dw_id = pData.contents.dwID
        if dw_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
            obj = cast(pData, POINTER(SIMCONNECT_RECV_SIMOBJECT_DATA)).contents
            handler = self.data_handlers.get(obj.dwRequestID)
            if handler:
                handler(obj)
                return
        elif dw_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EVENT:
            evt = cast(pData, POINTER(SIMCONNECT_RECV_EVENT)).contents
            handler = self.event_handlers.get(evt.uEventID)
            if handler:
                handler(evt)
                return
        return super().my_dispatch_proc(pData, cbData, pContext)


class SimFeed:
    def __init__(self, names, frame_interval=6, events=()):
        """names: Python-SimConnect style names ("PLANE_LATITUDE", "COM_ACTIVE_FREQUENCY:1").
        frame_interval: send every Nth visual frame (6 at 60 fps = 10 Hz).
        events: key event names to be notified about (``take_events``)."""
        self.names = list(dict.fromkeys(names))
        self.frame_interval = frame_interval
        self.event_names = list(dict.fromkeys(events))
        self.values = dict.fromkeys(self.names)
        self.sm = None
        self.events = None  # SimEvents while connected and subscribed
        self.units = {}
        self._last_packet = 0.0
        self.packets = 0

    # ---------------------------------------------------------------- lifecycle
    def connect(self):
        self.close()
        sm = _HookedSimConnect()
        if not sm.ok:  # package swallows a failed Open(); see msfs-simconnect skill
            self._close_sm(sm)
            raise ConnectionError("SimConnect Open() failed (is MSFS running?)")
        self.sm = sm
        try:
            self._define()
        except Exception:
            self.close()
            raise

    def _define(self):
        sm = self.sm
        table = AircraftRequests(sm, _time=0)  # only used as the name -> (sim name, unit) table
        self.def_id = sm.new_def_id()
        self.req_id = sm.new_request_id()
        for n in self.names:
            req = table.find(n)
            if req is None:
                raise KeyError(f"unknown SimVar {n!r} (see SimConnect/RequestList.py)")
            sim_name, unit = req.definitions[0]
            self.units[n] = unit.decode()
            hr = sm.dll.AddToDataDefinition(
                sm.hSimConnect, self.def_id.value, sim_name, unit,
                SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64, 0, SIMCONNECT_UNUSED,
            )  # fmt: skip
            if not sm.IsHR(hr, 0):
                raise RuntimeError(f"AddToDataDefinition failed for {n}")
        sm.data_handlers[self.req_id.value] = self._on_data
        hr = sm.dll.RequestDataOnSimObject(
            sm.hSimConnect, self.req_id.value, self.def_id.value, SIMCONNECT_OBJECT_ID_USER,
            SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_VISUAL_FRAME,
            SIMCONNECT_DATA_REQUEST_FLAG.SIMCONNECT_DATA_REQUEST_FLAG_DEFAULT,  # not CHANGED: keep flowing while paused
            0, self.frame_interval, 0,
        )  # fmt: skip
        if not sm.IsHR(hr, 0):
            raise RuntimeError("RequestDataOnSimObject failed")
        if self.event_names:
            self.events = SimEvents(sm)
            self.events.subscribe(self.event_names)

    def _on_data(self, obj):
        """Dispatch-thread callback: unpack one SIMOBJECT_DATA packet of len(names) FLOAT64s."""
        n = len(self.names)
        if obj.dwDefineCount != n:
            return
        arr = cast(obj.dwData, POINTER(c_double * n)).contents
        self.values = dict(zip(self.names, arr, strict=True))
        self._last_packet = time.time()
        self.packets += 1

    @staticmethod
    def _close_sm(sm):
        try:
            if getattr(sm, "timerThread", None):
                sm.exit()
            elif sm.hSimConnect:
                sm.dll.Close(sm.hSimConnect)
        except Exception:  # noqa: BLE001 - closing a half-open connection may fail; nothing to do about it
            pass

    def close(self):
        sm, self.sm = self.sm, None
        self.events = None
        if sm is not None:
            self._close_sm(sm)
        self.values = dict.fromkeys(self.names)
        self._last_packet = 0.0

    # ---------------------------------------------------------------- queries
    @property
    def connected(self):
        return self.sm is not None

    def age(self):
        """Seconds since the last packet; inf before the first one."""
        return time.time() - self._last_packet if self._last_packet else float("inf")

    def get(self, names):
        return {n: self.values.get(n) for n in names}

    def take_events(self):
        """Key events fired since the last call (names, oldest first); empty without a subscription."""
        return self.events.take() if self.events else []


def main(argv=None):
    import sys

    names = (argv if argv is not None else sys.argv[1:]) or [
        "INDICATED_ALTITUDE", "AIRSPEED_INDICATED", "ZULU_TIME", "LOCAL_TIME", "TIME_OF_DAY",
    ]  # fmt: skip
    f = SimFeed(names)
    f.connect()
    t0 = time.time()
    try:
        while time.time() - t0 < 3:
            time.sleep(0.5)
            shown = {k: (round(v, 3) if v is not None else None) for k, v in f.values.items()}
            print(f"age={f.age():.3f}s packets={f.packets}", shown)
    finally:
        f.close()


if __name__ == "__main__":
    main()
