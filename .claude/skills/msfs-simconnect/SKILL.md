---
name: msfs-simconnect
description: Read and write MSFS 2024 flight data from Python with the Python-SimConnect package (SimVars, units, connection handling, polling cost, reconnect). Use for any task that talks to Microsoft Flight Simulator 2020/2024 via SimConnect from Python, or that needs SimVar names and units for a display or controller.
---

# MSFS 2024 via Python-SimConnect

Working implementation: `x52_simconnect/sim_feed.py` (`SimFeed`, the streaming feed) and
`x52_simconnect/sources.py` (`SimSource`, reconnect logic). Reuse them; `tests/test_sim_feed.py` shows how to
test packet handling and reconnects without the sim.

## Environment facts (verified setup)
- MSFS 2024 Store build (package `Microsoft.Limitless_8wekyb3d8bbwe`). No MSFS SDK is installed, so there is no
  `SimConnect.h`/`.lib`; the Python package ships its own `SimConnect.dll` and it opens MSFS 2024 fine.
- The `SimConnect` pip package is Python-SimConnect 0.4.26 (ctypes wrapper). Works on Python 3.14 64-bit. It
  imports `ctypes.wintypes`, so it only imports on Windows; tests that touch it use `pytest.importorskip`.
- `hidapi`, `pyusb` cover the joystick side, see the `x52-mfd` skill.

## Connecting, and the trap in the constructor
```python
from SimConnect import SimConnect, AircraftRequests
sm = SimConnect()                       # does NOT raise when the sim is down
if not sm.ok:                           # Open() failed: ok stays False, no dispatch thread
    raise ConnectionError("SimConnect Open() failed")
aq = AircraftRequests(sm, _time=200)    # _time = per-variable cache in ms
```
- A failed `Open()` returns silently; `sm.ok` is only set once the OPEN message arrives on the dispatch thread.
  Any later `AddToDataDefinition` then logs `ERROR:SimConnect.Constants:SIM def(...)` for every variable.
- `sm.exit()` crashes with `AttributeError: timerThread` on a never-connected object. Guard it
  (`getattr(sm, "timerThread", None)`) or call `sm.dll.Close(sm.hSimConnect)` directly.
- Retry every ~5 s while the sim is down; connection takes ~0.4 s when it succeeds.
- Detect the sim going away by "every value came back `None` for N seconds", then close and reconnect.

## Reading values
`aq.get("NAME")` or `aq.get("NAME:1")` for indexed vars (engine, COM, NAV, transponder). Returns a float,
a bytes string for string vars, or `None` when no data. Unknown names return `None` silently, so verify names in
`site-packages/SimConnect/RequestList.py` (it maps `PYTHON_NAME` to `b'SIM NAME'` and a unit).

Cost: each `get` is a one-shot `RequestDataOnSimObjectType` followed by up to 10 x 10 ms waits. Budget roughly
10-100 ms per variable per refresh once the cache (`_time`) expires. Fine for a handful of values; not for a display.

## Streaming instead of polling (preferred): `x52_simconnect/sim_feed.py`
One data definition with every variable, pushed by the sim; reading is a dict lookup. Verified live: 31 vars,
~13 packets/s, zero polling. Reuse `SimFeed`, or copy the pattern:
- Subclass `SimConnect` and override `my_dispatch_proc`: the package ignores `SIMCONNECT_RECV_ID_SIMOBJECT_DATA`
  (it only handles the `_BYTYPE` variant), so catch it first and delegate the rest to `super()`.
- `sm.new_def_id()` / `sm.new_request_id()` give fresh ids. `sm.dll.AddToDataDefinition(h, def_id.value,
  b'SIM NAME', b'unit', SIMCONNECT_DATATYPE_FLOAT64, 0, SIMCONNECT_UNUSED)` per var; get name/unit from
  `AircraftRequests(sm).find(name).definitions[0]`.
- `sm.dll.RequestDataOnSimObject(h, req_id.value, def_id.value, SIMCONNECT_OBJECT_ID_USER,
  SIMCONNECT_PERIOD_VISUAL_FRAME, FLAG_DEFAULT, origin=0, interval=6, limit=0)`. Use VISUAL_FRAME, not SIM_FRAME,
  and not the CHANGED flag, so packets keep flowing while the sim is paused or in a menu (otherwise a "stale"
  detector reconnects needlessly).
- In the handler: `cast(obj.dwData, POINTER(c_double * n)).contents`, check `obj.dwDefineCount == n`.
- Track the time of the last packet; treat > 15 s as "sim gone" and reconnect.
- FLOAT64 only. Strings (`TITLE`) either via the one-shot API every few seconds, or add STRING256 datums and parse
  the mixed packet by offset.

## Units that bite
| SimVar (Python name) | Unit returned | Note |
|---|---|---|
| `PLANE_HEADING_DEGREES_MAGNETIC`, `GPS_GROUND_MAGNETIC_TRACK` | **radians** despite the name | `math.degrees()` then `% 360` |
| `COM_ACTIVE_FREQUENCY:1`, `COM_STANDBY_FREQUENCY:1`, `NAV_ACTIVE_FREQUENCY:1` | MHz | format `07.3f` |
| `TRANSPONDER_CODE:1` | BCO16 (BCD) | display with `f"{int(v):04x}"`, e.g. 0x7000 -> "7000" |
| `VERTICAL_SPEED`, `AUTOPILOT_VERTICAL_HOLD_VAR` | feet/minute | |
| `INDICATED_ALTITUDE`, `PLANE_ALT_ABOVE_GROUND`, `AUTOPILOT_ALTITUDE_LOCK_VAR` | feet | |
| `AIRSPEED_INDICATED`, `AIRSPEED_TRUE`, `GROUND_VELOCITY` | knots | |
| `PLANE_LATITUDE`, `PLANE_LONGITUDE` | degrees | |
| `FUEL_TOTAL_QUANTITY` | gallons | |
| `AMBIENT_TEMPERATURE` | celsius | |
| `AUTOPILOT_MASTER`, `_HEADING_LOCK`, `_ALTITUDE_LOCK`, `_NAV1_LOCK` | bool as 0.0/1.0 | **Seen live: `AUTOPILOT_NAV1_LOCK` returned `1.4e-311` for false.** Test `v > 0.5`, never `if v:` |
| `TITLE` | bytes | aircraft name |
| `ZULU_TIME`, `LOCAL_TIME` | seconds since midnight | verified live; local minus zulu gives the sim timezone |
| `ZULU_DAY_OF_MONTH`, `ZULU_MONTH_OF_YEAR`, `ZULU_YEAR` | number | |
| `TIME_OF_DAY` | enum 0 dawn, 1 day, 2 dusk, 3 night | good for dimming hardware |

Always wrap values in a tolerant `float()` (None -> default) before formatting; values are `None` while a flight loads.

## Writing / events
- Settable vars: `aq.set("NAME", value)` (only those flagged `'Y'` in RequestList.py).
- Key events: `AircraftEvents(sm).find("AP_MASTER")()`, or `SimEvents.send` in `x52_simconnect/sim_events.py`.
  Local `L:` vars and calculator code are not reachable with plain SimConnect; that needs a WASM module such as
  WASimCommander.

## Key-event notifications (verified live, MSFS 2024, 2026-09): `x52_simconnect/sim_events.py`
Be told whenever a key event fires, from any client: `MapClientEventToSimEvent(h, id, b"FLAPS_INCR")`,
`AddClientEventToNotificationGroup(h, group, id, False)` (not maskable, so the sim still acts on it),
`SetNotificationGroupPriority(h, group, SIMCONNECT_GROUP_PRIORITY_HIGHEST)`. Each fire then arrives on the
dispatch thread as `SIMCONNECT_RECV_ID_EVENT` (`SIMCONNECT_RECV_EVENT.uEventID`, `.dwData`).
- Python-SimConnect's `my_dispatch_proc` routes every `RECV_ID_EVENT` to `handle_id_event`, which only knows
  its four system events (ids 0-3 of `SIMCONNECT_CLIENT_EVENT_ID`); hook the dispatch and look up your ids
  first (`_HookedSimConnect.event_handlers` in `sim_feed.py`).
- The enum classes used as ctypes argtypes have `from_param = int`, so plain ints work as event and group ids.
  `sim_events.py` numbers its events from 0x1000 and uses group 0x1000, clear of what `map_to_sim_event`
  appends to the package's enum (4, 5, ...).
- Mapping 27 events and 47 FLOAT64 datums in one definition took well under a second; no exceptions logged.
- Seen live: an event sent with `TransmitClientEvent` (priority HIGHEST, `GROUPID_IS_PRIORITY`) comes back as
  a notification in the same 0.25 s tick as the SimVar change it causes. Joystick bindings deliver them too
  (`FLAPS_INCR`/`FLAPS_DECR` from an X52 button, verified in a live flight). A cockpit switch clicked with
  the mouse only showed up as its SimVar changing, so do not rely on notifications for mouse interaction.
  `python -m x52_simconnect.sim_events` prints whatever arrives.
- Only subscribe to discrete events (`FLAPS_INCR`, `GEAR_TOGGLE`, `AP_MASTER`, ...). `AXIS_*` and `*_SET`
  events fire every frame from bound axes; see `event_rules.KEY_EVENTS`.
- Unit facts from this work: `BRAKE_PARKING_POSITION` is `Position` (0..1), `SPOILERS_HANDLE_POSITION` and
  `ELEVATOR_TRIM_PCT` are `Percent Over 100` (0..1, trim signed), `GENERAL_ENG_THROTTLE_LEVER_POSITION:1` is
  `Percent` (0..100), `FLAPS_HANDLE_INDEX` a small integer, `GEAR_HANDLE_POSITION` and the `LIGHT_*`,
  `PITOT_HEAT`, `ELECTRICAL_MASTER_BATTERY`, `GENERAL_ENG_MASTER_ALTERNATOR:1`, `SIM_ON_GROUND` flags are
  Bool (threshold, do not truth-test).

## Testing without the sim
- Keep `DemoSource` (`x52_simconnect/sources.py`) in step with the pages: every SimVar a page uses must be in
  `demo_values`; `tests/test_pages.py` checks that and renders every page with demo data and with all `None`.
- New formatters go in `x52_simconnect/formatting.py` with a test for `None` input.
- Run the real path with the sim down to check the retry loop stays quiet and does not spin.
