"""SimConnect key events, both directions: be told when one fires anywhere in the sim (keyboard, joystick,
cockpit click), and fire one ourselves (spec 04).

    events = SimEvents(sm)                      # sm: the feed's _HookedSimConnect (has ``event_handlers``)
    events.subscribe(["FLAPS_INCR", "GEAR_TOGGLE"])
    events.take()                               # -> ["FLAPS_INCR", ...] fired since the last call
    events.send("AP_MASTER")

How notifications work: each event name is mapped to a client event id (``MapClientEventToSimEvent``),
added to one notification group (``AddClientEventToNotificationGroup``, not maskable so the sim still acts on
it) and the group is given a priority. The sim then sends ``SIMCONNECT_RECV_ID_EVENT`` with that id whenever
the event fires; the feed's dispatch hook routes ids it knows to ``event_handlers`` (see ``sim_feed.py``).
Python-SimConnect only knows its own four ids there, so this module allocates ids from ``FIRST_ID`` upward,
clear of the ids the package hands out from 0.
"""

import time
from collections import deque

from SimConnect.Constants import SIMCONNECT_GROUP_PRIORITY_HIGHEST, SIMCONNECT_OBJECT_ID_USER
from SimConnect.Enum import SIMCONNECT_EVENT_FLAG

from .event_rules import SKIPPED_EVENT_GROUPS, loggable

FIRST_ID = 0x1000  # first client event id; Python-SimConnect numbers its own from 0
GROUP_ID = 0x1000  # our one notification group


def all_key_events():
    """Every discrete key event in Python-SimConnect's table (EventList.py), minus the groups and names
    ``event_rules`` rules out: some 700 names. Subscribing to all of them takes ~0.1 s and an idle cockpit
    sends nothing (seen live), so the event log hears about switches it has no rule for."""
    from SimConnect import AircraftEvents
    from SimConnect.EventList import EventHelper

    names = []
    for group in vars(AircraftEvents).values():  # the nested group classes; AircraftEvents.list omits G1000
        if not (isinstance(group, type) and issubclass(group, EventHelper)):
            continue
        if group.__name__.lstrip("_") in SKIPPED_EVENT_GROUPS:
            continue
        names += [entry[0].decode() for entry in group.list]
    return tuple(dict.fromkeys(name for name in names if loggable(name)))


class SimEvents:
    def __init__(self, sm, group_id=GROUP_ID, first_id=FIRST_ID, clock=time.time):
        self.sm = sm
        self.group_id = group_id
        self._next_id = first_id
        self.ids = {}  # name -> client event id
        self.names = {}  # client event id -> name
        self.fired = deque()  # (name, dwData, time) appended on the dispatch thread
        self._clock = clock

    def _check(self, hr, what):
        if not self.sm.IsHR(hr, 0):
            raise RuntimeError(f"{what} failed")

    def map(self, name):
        """The client event id for ``name``, mapping it on first use."""
        if name in self.ids:
            return self.ids[name]
        eid = self._next_id
        self._check(self.sm.dll.MapClientEventToSimEvent(self.sm.hSimConnect, eid, name.encode()), f"map {name}")
        self._next_id += 1
        self.ids[name] = eid
        self.names[eid] = name
        return eid

    def subscribe(self, names, priority=SIMCONNECT_GROUP_PRIORITY_HIGHEST):
        """Get a notification whenever one of ``names`` fires, from any source, without blocking it."""
        h = self.sm.hSimConnect
        for name in names:
            eid = self.map(name)
            self._check(self.sm.dll.AddClientEventToNotificationGroup(h, self.group_id, eid, False), f"group {name}")
            self.sm.event_handlers[eid] = self._on_event
        self._check(self.sm.dll.SetNotificationGroupPriority(h, self.group_id, priority), "group priority")

    def _on_event(self, evt):
        """Dispatch-thread callback for SIMCONNECT_RECV_EVENT."""
        self.fired.append((self.names.get(evt.uEventID, f"#{evt.uEventID}"), evt.dwData, self._clock()))

    def take(self):
        """Names of the events fired since the last call, oldest first."""
        names = []
        while self.fired:
            names.append(self.fired.popleft()[0])
        return names

    def send(self, name, data=0):
        """Fire ``name`` on the user aircraft, as a keypress would."""
        eid = self.map(name)
        hr = self.sm.dll.TransmitClientEvent(
            self.sm.hSimConnect, SIMCONNECT_OBJECT_ID_USER, eid, data,
            SIMCONNECT_GROUP_PRIORITY_HIGHEST, SIMCONNECT_EVENT_FLAG.SIMCONNECT_EVENT_FLAG_GROUPID_IS_PRIORITY,
        )  # fmt: skip
        self._check(hr, f"send {name}")


def main(argv=None):
    """Print key events as they fire for 30 s:  python -m x52_simconnect.sim_events [EVENT ...]"""
    import sys

    from .sim_feed import SimFeed

    names = (argv if argv is not None else sys.argv[1:]) or list(all_key_events())
    feed = SimFeed(["ZULU_TIME"], events=names)
    feed.connect()
    print(f"subscribed to {len(names)} events; press things in the sim (30 s, Ctrl-C to stop)")
    t0 = time.time()
    try:
        while time.time() - t0 < 30:
            time.sleep(0.1)
            for name in feed.take_events():
                print(f"{time.time() - t0:6.2f}s {name}")
    except KeyboardInterrupt:
        pass
    finally:
        feed.close()


if __name__ == "__main__":
    main()
