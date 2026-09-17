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
"""
import ctypes
import time
from ctypes import POINTER, c_double, cast

from SimConnect import SimConnect, AircraftRequests
from SimConnect.Enum import (SIMCONNECT_RECV_ID, SIMCONNECT_RECV_SIMOBJECT_DATA, SIMCONNECT_DATATYPE,
                             SIMCONNECT_PERIOD, SIMCONNECT_DATA_REQUEST_FLAG)
from SimConnect.Constants import SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_UNUSED


class _HookedSimConnect(SimConnect):
    """Python-SimConnect with a hook for SIMOBJECT_DATA packets (bulk definitions)."""

    def __init__(self, *a, **k):
        self.data_handlers = {}           # request id -> callable(SIMCONNECT_RECV_SIMOBJECT_DATA)
        super().__init__(*a, **k)

    def my_dispatch_proc(self, pData, cbData, pContext):
        if pData.contents.dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
            obj = cast(pData, POINTER(SIMCONNECT_RECV_SIMOBJECT_DATA)).contents
            handler = self.data_handlers.get(obj.dwRequestID)
            if handler:
                handler(obj)
                return
        return super().my_dispatch_proc(pData, cbData, pContext)


class SimFeed:
    def __init__(self, names, frame_interval=6):
        """names: Python-SimConnect style names ("PLANE_LATITUDE", "COM_ACTIVE_FREQUENCY:1").
        frame_interval: send every Nth visual frame (6 at 60 fps = 10 Hz)."""
        self.names = list(dict.fromkeys(names))
        self.frame_interval = frame_interval
        self.values = {n: None for n in self.names}
        self.sm = None
        self.units = {}
        self._last_packet = 0.0
        self._packets = 0

    # ---------------------------------------------------------------- lifecycle
    def connect(self):
        self.close()
        sm = _HookedSimConnect()
        if not sm.ok:                       # package swallows a failed Open(); see msfs-simconnect skill
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
        table = AircraftRequests(sm, _time=0)         # only used as the name -> (sim name, unit) table
        self.def_id = sm.new_def_id()
        self.req_id = sm.new_request_id()
        for n in self.names:
            req = table.find(n)
            if req is None:
                raise KeyError(f"unknown SimVar {n!r} (see SimConnect/RequestList.py)")
            sim_name, unit = req.definitions[0]
            self.units[n] = unit.decode()
            hr = sm.dll.AddToDataDefinition(sm.hSimConnect, self.def_id.value, sim_name, unit,
                                            SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64, 0, SIMCONNECT_UNUSED)
            if not sm.IsHR(hr, 0):
                raise RuntimeError(f"AddToDataDefinition failed for {n}")
        sm.data_handlers[self.req_id.value] = self._on_data
        hr = sm.dll.RequestDataOnSimObject(
            sm.hSimConnect, self.req_id.value, self.def_id.value, SIMCONNECT_OBJECT_ID_USER,
            SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_VISUAL_FRAME,
            SIMCONNECT_DATA_REQUEST_FLAG.SIMCONNECT_DATA_REQUEST_FLAG_DEFAULT,   # not CHANGED: keep packets flowing while paused
            0, self.frame_interval, 0)
        if not sm.IsHR(hr, 0):
            raise RuntimeError("RequestDataOnSimObject failed")

    def _on_data(self, obj):
        n = len(self.names)
        if obj.dwDefineCount != n:
            return
        arr = cast(obj.dwData, POINTER(c_double * n)).contents
        self.values = dict(zip(self.names, arr))
        self._last_packet = time.time()
        self._packets += 1

    @staticmethod
    def _close_sm(sm):
        try:
            if getattr(sm, "timerThread", None):
                sm.exit()
            elif sm.hSimConnect:
                sm.dll.Close(sm.hSimConnect)
        except Exception:
            pass

    def close(self):
        sm, self.sm = self.sm, None
        if sm is not None:
            self._close_sm(sm)
        self.values = {n: None for n in self.names}
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


if __name__ == "__main__":
    import sys
    names = sys.argv[1:] or ["INDICATED_ALTITUDE", "AIRSPEED_INDICATED", "ZULU_TIME", "LOCAL_TIME", "TIME_OF_DAY"]
    f = SimFeed(names)
    f.connect()
    t0 = time.time()
    while time.time() - t0 < 3:
        time.sleep(0.5)
        print(f"age={f.age():.3f}s packets={f._packets}", {k: (round(v, 3) if v is not None else None) for k, v in f.values.items()})
    f.close()
