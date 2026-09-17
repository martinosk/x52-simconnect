# Spec 04 - Bidirectional control: stick buttons trigger sim actions

**Depends on:** 01 (mode = binding layer, `Display` for feedback), 03 (template renderer for feedback text).
**Shares:** `x52_simconnect/sim_events.py` with 02 (same mapping/notification plumbing, used here to *send*).

## Goal
Press a button on the X52 and something happens in the sim (autopilot master, heading bug, COM swap, ...),
with immediate feedback on the MFD. The stick becomes a small control panel, not only a display.

## Design
1. **`x52_simconnect/sim_events.py`**: `SimEvents(sm)` wrapping `SimConnect_MapClientEventToSimEvent` and
   `SimConnect_TransmitClientEvent`. Python-SimConnect already exposes both: `sm.map_to_sim_event(b"AP_MASTER")`
   returns an event id, `sm.send_event(evt, DWORD(data))` fires it. Map lazily, cache per name. `SimFeed` owns
   the `sm` object; expose it (`feed.sm`) so events share the connection.
2. **Bindings are app/page-aware and layered by the mode switch** (01 makes the mode available as
   `display.mode`). Data model:
   ```toml
   # bindings.toml
   [defaults]
   repeat_after_ms = 400     # hold-to-repeat for inc/dec events
   repeat_hz = 10

   [[binding]]
   mode = 1                  # 0 = any mode
   page = "AUTOPILOT"        # "*" = any page of the pages app
   button = "T1_UP"
   event = "HEADING_BUG_INC"
   repeat = true
   feedback = "HDG {AUTOPILOT_HEADING_LOCK_DIR:03.0f}"   # template syntax from 03, shown as a banner

   [[binding]]
   mode = 1
   page = "AUTOPILOT"
   button = "C"
   event = "AP_MASTER"
   feedback = "AP {AUTOPILOT_MASTER|onoff:ON}"

   [[binding]]
   mode = 1
   page = "RADIO"
   button = "C"
   event = "COM_STBY_RADIO_SWAP"
   feedback = "COM1 {COM_ACTIVE_FREQUENCY:1|freq}"
   ```
   v1 is parameterless events only; `value = 5000` for `*_SET` events comes later.
3. **Button handling.** `x52_simconnect.buttons.ButtonReader` edge-detects presses and keeps `state` (held buttons). Add hold-to-repeat:
   while a `repeat = true` binding's button stays down, fire again at `repeat_hz` after `repeat_after_ms`.
   Start/Stop and Reset stay reserved for the active app unless a binding explicitly claims them.
4. **Feedback.** `display.banner(rendered feedback)`. Because the feed streams, the value shown is the post-event
   value within ~100 ms; re-render the banner each tick while it is active rather than once.
5. **Conflict with MSFS's own bindings.** MSFS also sees every X52 button through its controller profile. A
   button bound both in MSFS and here fires twice. Document a convention: clear those buttons in the MSFS X52
   profile, or prefer buttons the stock profile leaves unbound (check Options > Controls; T1-T3 and A/B/C are the
   usual candidates).

### Event names to start with (all parameterless key events)
`AP_MASTER`, `AP_HDG_HOLD`, `AP_ALT_HOLD`, `AP_NAV1_HOLD`, `AP_VS_HOLD`, `HEADING_BUG_INC`, `HEADING_BUG_DEC`,
`AP_ALT_VAR_INC`, `AP_ALT_VAR_DEC`, `AP_VS_VAR_INC`, `AP_VS_VAR_DEC`, `COM_STBY_RADIO_SWAP`, `COM2_RADIO_SWAP`,
`NAV1_RADIO_SWAP`, `XPNDR_IDENT_ON`, `TOGGLE_MASTER_BATTERY`, `PITOT_HEAT_TOGGLE`, `PARKING_BRAKES`.
Python-SimConnect's `EventList.py` has the full list with descriptions.

## Acceptance criteria
- [ ] `bindings.toml` loads at start; unknown button or event names fail fast with a clear message.
- [ ] Pressing a bound button fires the event once; holding a `repeat` binding fires repeatedly at the configured rate.
- [ ] Bindings only fire on their mode/page; the same button can do different things on different pages.
- [ ] Feedback banner shows the new value within 0.3 s and disappears after 0.8 s without disturbing the page.
- [ ] Paging still works; firmware-handled buttons still trigger the forced redraw.
- [ ] Offline test: a fake `SimEvents` records calls; a scripted press sequence (fake `ButtonReader` queue and
      state) yields the expected event list, including repeat timing.
- [ ] Live test checklist in README: AP master on/off, heading bug +/- with hold, COM swap; verify in cockpit.

## Steps
1. `sim_events.py` + expose `feed.sm`. Smoke test: fire `AP_MASTER` from the CLI and watch the cockpit.
2. `bindings.toml` loader with validation (`tomllib` is stdlib on Python 3.11+).
3. Repeat logic; wire bindings into the main loop through `Display`/apps from 01.
4. Feedback templates via 03's renderer.
5. Tests, README section, skill note in `msfs-simconnect` about events and double-firing.

## Open questions
- Does MSFS 2024 accept `HEADING_BUG_INC` on all default aircraft or do some need `AP_HDG_VAR_INC`? Check per aircraft.
