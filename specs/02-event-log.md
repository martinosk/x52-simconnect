# Spec 02 - Event log (mode 3): what was last triggered

**Depends on:** 01 (App protocol, Display). **Reuses:** the template renderer from 03 if it exists; not required.

## Goal
Mode 3 shows a rolling log of the last cockpit actions, newest on top, with age:
```
  3s FLAPS 10
 41s PARK BRK ON
 58s GEAR DOWN
```
Flaps, gear, parking brake, spoilers, trim, throttle, lights, pitot heat, battery, autopilot modes, and the
key events behind them. Start/Stop and Reset scroll the history; Reset held returns to the newest.

## Two sources, use both
### A. State transitions (primary, reliable)
Watch SimVars in the streaming feed and log a line when a value changes meaningfully. All names exist in
Python-SimConnect's table (`RequestList.py`):

| SimVar | Unit | Log line | Rule |
|---|---|---|---|
| `FLAPS_HANDLE_INDEX` | number | `FLAPS n` (or degrees via `TRAILING_EDGE_FLAPS_LEFT_ANGLE`) | on change |
| `GEAR_HANDLE_POSITION` | bool | `GEAR DOWN` / `GEAR UP` | on change (>0.5 threshold, see skill) |
| `BRAKE_PARKING_POSITION` | position | `PARK BRK ON/OFF` | on change |
| `SPOILERS_HANDLE_POSITION` | percent/100 | `SPOILERS 50%` | on change, quantised to 10 % |
| `ELEVATOR_TRIM_PCT` | percent/100 | `TRIM +12%` | log when it stops moving (no change for 0.5 s after a change) |
| `GENERAL_ENG_THROTTLE_LEVER_POSITION:1` | percent | `THR 75%` | crossing 5 % steps, debounced 0.5 s |
| `LIGHT_LANDING`, `LIGHT_TAXI`, `LIGHT_STROBE`, `LIGHT_NAV`, `LIGHT_BEACON` | bool | `LDG LIGHT ON` | on change |
| `PITOT_HEAT`, `ELECTRICAL_MASTER_BATTERY`, `GENERAL_ENG_MASTER_ALTERNATOR:1` | bool | `PITOT HEAT ON` | on change |
| `AUTOPILOT_MASTER`, `_HEADING_LOCK`, `_ALTITUDE_LOCK`, `_NAV1_LOCK`, `_VERTICAL_HOLD` | bool | `AP HDG ON` | on change |
| `AUTOPILOT_HEADING_LOCK_DIR`, `AUTOPILOT_ALTITUDE_LOCK_VAR` | deg / ft | `HDG BUG 270`, `ALT SEL 5000` | when it stops moving |
| `COM_ACTIVE_FREQUENCY:1`, `COM_STANDBY_FREQUENCY:1`, `TRANSPONDER_CODE:1` | MHz / BCD | `COM1 118.750`, `SQK 7000` | on change |
| `SIM_ON_GROUND` | bool | `AIRBORNE` / `TOUCHDOWN` | on change |

Rules are data: a list of `(simvar, formatter, policy)` where policy is `change`, `settled(seconds)` or
`step(size, debounce)`. Bool SimVars must be thresholded (`> 0.5`), never truth-tested; see the
`msfs-simconnect` skill for why.

### B. Key-event notifications (secondary, "what was pressed")
SimConnect can tell a client whenever a key event fires from any source (keyboard, joystick, cockpit click):
map the event with `MapClientEventToSimEvent`, add it to a notification group with
`AddClientEventToNotificationGroup(..., bMaskable=False)`, set the group priority, and it arrives as
`SIMCONNECT_RECV_ID_EVENT` with `uEventID`. Python-SimConnect binds all of these (`Attributes.py`) and its
dispatch routes `RECV_ID_EVENT` to `handle_id_event`, which only knows its own four ids; extend the hooked
subclass in `x52_simconnect/sim_feed.py` to look up ours first.
- Subscribe to discrete events only: `FLAPS_INCR/DECR/UP/DOWN`, `GEAR_TOGGLE/UP/DOWN`, `PARKING_BRAKES`,
  `ELEV_TRIM_UP/DN`, `SPOILERS_TOGGLE`, `AP_*` toggles, `TOGGLE_*_LIGHTS`, `PITOT_HEAT_TOGGLE`,
  `TOGGLE_MASTER_BATTERY`, `COM_STBY_RADIO_SWAP`. Never `AXIS_*` or `*_SET` events: they fire every frame.
- Log as `EV FLAPS_INCR` only when source A does not produce a line within 0.3 s (so a key press that changes
  nothing, e.g. FLAPS_INCR at full flaps, still shows up, but normal actions are not logged twice).
- This is the same plumbing spec 04 needs to *send* events; share the module (`x52_simconnect/sim_events.py`).

## Design
1. `EventLogApp(App)`: `vars` = union of the table above; `render` = three visible entries from a deque of
   (timestamp, text), starting at `scroll`; age column is recomputed every tick (`  3s`, ` 41s`, `12m`).
2. Rule engine in `x52_simconnect/event_rules.py`: pure functions over (previous values, current values, now) -> list of lines.
   Unit-testable with dicts, no sim.
3. History: 100 entries. Start/Stop = older, Reset = newer, Reset held 1 s = jump to newest. New entries while
   scrolled do not move the view; a `+3 NEW` marker replaces the age column on line 1 instead.
4. When activated (mode 3 selected) show the newest three; banner `MODE 3 EVENTS`.
5. Optional: mirror the newest entry as a 0.8 s banner in mode 1 (`--events-banner`), so you get the feedback
   without switching modes.
6. Demo mode: a scripted timeline of value changes (flaps, gear, brake) so the app can be watched without the sim.

## Acceptance criteria
- [ ] Moving flaps, gear, parking brake, trim, throttle, lights and AP switches in the sim each produce one
      correctly worded line, once, within 0.5 s.
- [ ] Continuous inputs (throttle, trim) do not spam: at most one line per settle/step, and nothing while idle.
- [ ] Key events that change no state still appear as `EV NAME`.
- [ ] Scrolling works with Start/Stop and Reset; the newest-marker behaviour is as described.
- [ ] Offline tests cover every rule policy and the age formatting.
- [ ] No extra SimConnect round trips: everything comes from the streaming feed and event notifications.

## Steps
1. `event_rules.py` + `tests/test_event_rules.py` (no sim).
2. `EventLogApp` with source A, demo timeline.
3. `sim_events.py` notification subscription, dispatch hook, dedupe against A.
4. Scrolling, banner mirror option, README.

## Open questions
- Flaps as index or degrees? Index is universal; degrees read better. Show `FLAPS 2 (10)` if both fit.
- Does MSFS 2024 deliver notifications for events fired by its own cockpit interaction (mouse click on the
  flap lever)? FSX did for most; verify early, it decides how much source B is worth.
