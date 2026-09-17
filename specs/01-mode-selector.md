# Spec 01 - Mode-selector display modes

**Depends on:** nothing. **Enables:** 02, 05, and the layer concept in 04.

## Goal
The rotary mode selector on the stick (positions 1, 2, 3) chooses which "app" the MFD shows:

| Mode | App | Spec |
|---|---|---|
| 1 | Data pages: FLIGHT, RADIO, AUTOPILOT, ... (what exists today; grows with 03) | this one |
| 2 | Comms: tuned station, later ATC text | 05 |
| 3 | Event log: the last things triggered in the cockpit | 02 |

Turning the selector switches instantly. Each app keeps its own state (current page, scroll position) while
another is showing.

## Facts to build on
- `x52_buttons.ButtonReader` already decodes the selector: `reader.mode` is 1, 2 or 3, updated from HID bits 23-25.
  Verified live. The selector is only reported when the stick sends a report, i.e. after any input change; at
  startup `mode` is `None` until the first report. Treat `None` as mode 1.
- MSFS also sees the selector positions as joystick buttons. In the stock X52 profile they are unbound; if the
  user binds them, both fire. Document it, do not try to solve it.
- The firmware still draws its own clock and stopwatch under our text regardless of mode.

## Design
1. **Extract a `Display` object** from `main()` in `mfd_sim.py`. It owns: the `X52Mfd`, the banner (text + expiry),
   the forced-redraw timer, and `mode`. It exposes `show(lines)`, `banner(text)`, `force_redraw_in(seconds)`,
   and is ticked once per loop. Everything that currently touches `mfd.set_lines` goes through it.
2. **An `App` protocol**, one instance per mode:
   ```python
   class App:
       name: str
       vars: list[str]                       # SimVars this app needs in the feed
       def on_button(self, name: str) -> None  # Start/Stop, Reset, ... while this app is active
       def render(self, values: dict) -> list[str]   # 3 lines, <= 16 chars each
       def on_activate(self) -> None         # mode switched to this app (show a banner, reset scroll, ...)
   ```
   `PagesApp` wraps today's pages and paging logic. `ALL_VARS` becomes the union of every app's `vars` plus
   `CLOCK_VARS` (the feed streams everything; unused values are free).
3. **Main loop**: `active = apps[display.mode]`; deliver button presses to `active.on_button`; `display.show(
   active.render(values))`. On mode change: `active.on_activate()`, banner `MODE 2 COMMS` for `BANNER_SECONDS`.
4. **Buttons per app.** Start/Stop and Reset keep their meaning inside each app (next/prev page in mode 1,
   scroll in mode 3). The firmware side effects and the forced redraw stay as they are in `Display`.
5. **CLI**: `--mode N` to force a mode for testing without touching the stick; `--mode-pages` is removed
   (superseded).
6. **Demo mode** must cover all three apps: `DemoSource` gets the extra variables and a fake event stream.

## Acceptance criteria
- [ ] Turning the selector switches the app within one loop tick (0.25 s) and shows a `MODE n NAME` banner.
- [ ] Mode 1 behaves exactly as today (pages, paging, banner, clock sync, brightness).
- [ ] Each app keeps its state across mode switches (leave mode 1 on RADIO, come back, still RADIO).
- [ ] `--mode N` works with `--demo` and without a stick present in the reader (no HID = stay in the forced mode).
- [ ] Offline test: a scripted sequence of mode values and presses drives a fake `Display` and yields the expected lines.
- [ ] README documents the three modes and the joystick-binding caveat.

## Steps
1. `Display` extraction with no behaviour change; run demo mode and live mode to confirm.
2. `App` protocol + `PagesApp`; placeholder apps for modes 2 and 3 that show their name and the time.
3. Mode switching, banner, `--mode`.
4. Tests, README.

## Open questions
- Should a long press of Reset return every app to its home state, or is that per app? Per app for now.
