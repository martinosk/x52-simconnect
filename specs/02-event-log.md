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
- [x] Moving flaps, gear, parking brake, trim, throttle, lights and AP switches in the sim each produce one
      correctly worded line, once, within 0.5 s. (Verified live 2026-09-17 for flaps from the joystick, the
      parking brake, a cockpit switch clicked with the mouse and the trim; the rest offline against the rule
      engine.)
- [x] Continuous inputs (throttle, trim) do not spam: at most one line per settle/step, and nothing while idle.
- [x] Key events that change no state still appear as `EV NAME`.
- [x] Scrolling works with Start/Stop and Reset; the newest-marker behaviour is as described.
- [x] Offline tests cover every rule policy and the age formatting.
- [x] No extra SimConnect round trips: everything comes from the streaming feed and event notifications.

## Implemented (2026-09)
`x52_simconnect/event_rules.py` (`RULES`, `RuleEngine`, `KEY_EVENTS`), `EventLogApp` in
`x52_simconnect/apps.py`, `x52_simconnect/sim_events.py` (`SimEvents.subscribe/take/send`), the event branch
of the dispatch hook in `sim_feed.py`, `--events-banner`, the scripted demo timeline in `sources.py`.
Deviations from the text above:
- The age column is three characters (` 3s`, `41s`, `12m`, ` 2h`) plus a space, leaving 12 for the text, so
  every wording in the table fits without clipping (`PARK BRK OFF`, `COM1 118.750`, `ALT SEL 5000`).
  `EV` lines with long event names are clipped by the display.
- The `App` protocol gained `observe(values, now, events)` (every tick, every app, so the log collects while
  another mode shows), `on_hold(name, seconds)` (for Reset held) and `on_deactivate()` (for the banner
  mirror). `Bridge.step` takes `held` and `events`.
- A key event is also suppressed while any rule is still settling (trim moving), not only when a line was
  produced; otherwise `ELEV_TRIM_UP` would log before the 0.5 s `TRIM` line. Repeats of the same unexplained
  event within 1 s (a held hat at the trim stop) make one line.
- Flaps log the handle index (`FLAPS 2`); the surface angle lags the handle, so `FLAPS 2 (10)` would show the
  old angle at the moment of the change.

## Steps
1. `event_rules.py` + `tests/test_event_rules.py` (no sim).
2. `EventLogApp` with source A, demo timeline.
3. `sim_events.py` notification subscription, dispatch hook, dedupe against A.
4. Scrolling, banner mirror option, README.

## Open questions
- Flaps as index or degrees? Index, see above.
- Does MSFS 2024 deliver notifications for events fired by its own cockpit interaction (mouse click on the
  flap lever)? Verified 2026-09 for events sent through SimConnect (`TransmitClientEvent` from another client
  arrives as a notification and the sim acts on it, priority HIGHEST, not maskable) and for joystick
  bindings (`FLAPS_INCR`/`FLAPS_DECR` from the stick arrive, and pressing them at the flap stop shows
  `EV FLAPS_DECR` as intended). A cockpit switch clicked with the mouse changed its SimVar without a
  matching notification in the same run, so source A stays the primary one.
