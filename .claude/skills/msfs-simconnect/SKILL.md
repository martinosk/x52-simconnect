---
name: msfs-simconnect
description: Read and write MSFS 2024 flight data from Python with the Python-SimConnect package (SimVars, units, connection handling, polling cost, reconnect). Use for any task that talks to Microsoft Flight Simulator 2020/2024 via SimConnect from Python, or that needs SimVar names and units for a display or controller.
---

# MSFS 2024 via Python-SimConnect

Working implementation: `mfd_sim.py` (`SimSource` class). Reuse it.

## Environment facts (this machine)
- MSFS 2024 is the Store build (package `Microsoft.Limitless_8wekyb3d8bbwe`). No MSFS SDK is installed, so there is no
  `SimConnect.h`/`.lib`; the Python package ships its own `SimConnect.dll` and it opens MSFS 2024 fine.
- `pip install --user SimConnect` gives Python-SimConnect 0.4.26 (ctypes wrapper). Works on Python 3.14 64-bit.
- `hidapi`, `pyusb` are installed for the joystick side, see the `x52-mfd` skill.

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

## Streaming instead of polling (preferred): `sim_feed.py`
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
- Key events: `AircraftEvents(sm).find("AP_MASTER")()`. Local `L:` vars and calculator code are not reachable with
  plain SimConnect; that needs a WASM module such as WASimCommander.

## Testing without the sim
- Keep a `DemoSource` with the same `read(names)` interface and plausible moving values (see `mfd_sim.py`).
- Verify every formatter with all-`None` input as well as demo data.
- Run the real path with the sim down to check the retry loop stays quiet and does not spin.
