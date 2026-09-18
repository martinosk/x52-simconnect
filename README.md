# x52-simconnect

Drive the MFD of a **Saitek / Logitech X52 (non-Pro)** from Microsoft Flight Simulator 2024 (and 2020) over
SimConnect: live flight data on the three-line display, paging with the MFD buttons, and the stick's own clock,
date and brightness following sim time.

```
IAS 118 GS 124      COM1 118.750      AP ON  HDG 270
ALT  3500 V +500    STBY 121.500      ALT  5000 V +700
HDG 270  TRK 268    NAV1 110.50 7000  --- SPD 200
```

## Why this exists
Logitech's DirectOutput SDK, which every existing MSFS X52 plugin uses, only supports the X52 **Pro** (USB
`06A3:0762`). The non-Pro X52 (`06A3:075C`, older units `06A3:0255`) has no SDK at all. Its MFD is driven by raw
USB vendor control transfers, reverse-engineered by the [libx52](https://github.com/nirenjan/libx52) Linux
project. This repo brings that to Windows and connects it to the sim.

If you own an X52 **Pro**, use a DirectOutput-based tool instead, e.g.
[x52msfsout](https://github.com/mocsa/x52msfsout) or [FS20-SaiMFD](https://github.com/feketedev/FS20-SaiMFD).

## Status
| Feature | State |
|---|---|
| Write text to the MFD from Python on Windows | Working |
| Live flight data via SimConnect, streaming (one data definition, ~13 packets/s, no polling) | Working, verified in MSFS 2024 |
| Five pages: FLIGHT, RADIO, AUTOPILOT, ENGINE, POSITION | Working |
| Paging with the MFD buttons, page banner | Working |
| Mode selector 1/2/3 picks the app on the MFD (pages, comms, event log) | Working; mode 2 is a placeholder until spec 05 |
| Event log (mode 3): flaps, gear, brakes, trim, throttle, lights, AP, radios, unexplained key presses, with age and scrolling | Working, verified in MSFS 2024 |
| Firmware clock 1/2/3, date and MFD/LED brightness from sim time | Working |
| Auto-reconnect when the sim starts/stops; recovery from transient USB errors | Working |
| Comms, aircraft profiles, buttons -> sim events | Specified, see [SPECS.md](SPECS.md) |

## Requirements
- Windows 10/11, Python 3.11+ (developed on 3.14), MSFS 2024 or 2020 (any edition; the Store build works).
- X52 non-Pro on Windows' own joystick driver. **Logitech's X52 software and driver must not be installed**: with
  it, pressing stick buttons while the bridge runs freezes the MFD for half a minute at a time and makes the stick
  drop out and re-calibrate in flight. The bridge refuses to start beside it. Removal is below; MSFS does not need it.
- The **libusb-win32 filter driver** on the stick (one-time setup below). It sits *beside* the Windows HID driver,
  so the joystick keeps working in the sim. WinUSB/Zadig would replace the HID driver and break joystick input.

## Setup
```
git clone <this repo> && cd x52-simconnect
pip install -e .
```
Remove Logitech's X52 driver, if present (once, admin terminal). Uninstalling "Logitech X52" from the Windows
app list leaves the driver in place, so:
```
pnputil /enum-drivers                                 # find the oemNN.inf whose original name is sai075c.inf
pnputil /delete-driver oemNN.inf /uninstall /force    # then unplug and replug the stick
python -m x52_simconnect.logitech_driver              # must say "not installed"
```
Do this before installing the filter below: removing the driver also removes the filter.

Install the libusb-win32 filter (once):
1. Download `libusb-win32-devel-filter-1.2.7.3.exe` from the
   [libusb-win32 SourceForge project](https://sourceforge.net/projects/libusb-win32/files/libusb-win32-releases/1.2.7.3/)
   and run it (needs admin). At the end it opens the filter wizard: pick **Saitek X52 Flight Control System**.
   CLI alternative: `"C:\Program Files\LibUSB-Win32\bin\install-filter.exe" install --device=USB\VID_06A3&PID_075C`
2. **Unplug and replug the stick** (or reboot). The filter only loads when the device re-enumerates; Windows refuses
   a software restart with "pending system reboot".
3. Verify: `"C:\Program Files\LibUSB-Win32\bin\testlibusb.exe"` must list the X52, and
   ```powershell
   (Get-PnpDeviceProperty -InstanceId 'USB\VID_06A3&PID_075C\<your instance>' -KeyName DEVPKEY_Device_Stack).Data
   ```
   must show `\Driver\libusb0` above `\Driver\HidUsb`.

After a replug the MFD shows the stick's power-on text with the backlight off until the bridge starts.

## Run
```
python -m x52_simconnect                  # waits for MSFS, then shows live data
python -m x52_simconnect --demo           # fake data, no sim needed
python -m x52_simconnect --cycle 5        # auto-advance pages
python -m x52_simconnect --mode 3         # force a mode, ignore the stick's selector
python -m x52_simconnect --events-banner  # flash each new event-log line in the other modes too
python -m x52_simconnect --no-clock --no-auto-brightness
python -m x52_simconnect --demo --no-stick  # no sim and no X52: try the config UI anywhere
python -m x52_simconnect --next MOUSE_SCROLL_UP --prev MOUSE_SCROLL_DN   # remap paging; --list-buttons for names
python -m x52_simconnect --help
```
`pip install -e .` also installs an `x52-simconnect` command with the same options (in your Python `Scripts`
directory, which may not be on PATH).

### Modes
The rotary mode selector on the stick picks which "app" the MFD shows. Each app keeps its own state (current
page, scroll position) while another one is showing, and a `MODE 2 COMMS` banner flashes on each change.

| Selector | App | Shows |
|---|---|---|
| 1 | Pages | The data pages: FLIGHT, RADIO, AUTOPILOT, ENGINE, POSITION |
| 2 | Comms | Tuned station and ATC text, spec 05. Placeholder for now |
| 3 | Events | The last things triggered in the cockpit, newest on top with its age (below) |

`--mode N` forces one app for testing, ignoring the selector. Until the stick sends its first report (any input
change) the selector position is unknown and mode 1 is assumed.

Default buttons in mode 1: **Start/Stop** = next page, **Reset** = previous page. A `P2/5 RADIO` banner flashes on
each change. Start/Stop and Reset keep their own meaning inside each app.

### Config UI
While the bridge runs, **http://127.0.0.1:8052** is its setup page (`--ui-port N` to move it, `--no-ui` to
switch it off). It mirrors the MFD live and has two tabs:
- **Data pages, mode 1.** Add, remove, reorder and edit pages. A page is a name and three 16-character lines:
  plain text with fields in braces, `IAS {AIRSPEED_INDICATED:3.0f}`. Each page has its own small LCD that
  renders as you type, with live sim values. Pick ready-made fields from the list, or write any SimVar from
  Python-SimConnect's table by hand: `{VAR:5.0f}` takes a Python number format, `{GENERAL_ENG_RPM:1:5.0f}` is
  engine 1, `{VAR|hdg}` applies a filter (`hdg`, `signed:5`, `freq`, `bcd`, `onoff:HDG`, `either:ON,OFF`,
  `lat`, `lon`, `hms`). The unit of each SimVar is shown under the line; mind the ones in radians.
- **Event log, mode 3.** Tick which kinds of event make a line, per item or per group, switch the `EV` lines
  for unexplained key presses on or off, and leave single key events out (`BRAKES`, say). Key events the sim
  fired recently are listed with a one-click "leave out".

"Save and apply" writes `%APPDATA%\x52-simconnect\config.toml` (`--config FILE` for another one) and the stick
shows the result at once, with a `CONFIG APPLIED` banner. The file is plain TOML and can be edited by hand;
the bridge picks up changes within two seconds, and ignores a file with mistakes instead of blanking the
display. The page is only served to the local machine.

### Event log (mode 3)
```
 3s FLAPS 2
41s PARK BRK OFF
58s GEAR DOWN
```
A rolling log of the last 100 cockpit actions, collected in every mode, each line with its age. Two sources:
- **State changes** in the streaming feed (`event_rules.py`, a table of SimVar, wording and policy): flaps, gear,
  parking brake, spoilers, trim, throttle, propeller, mixture, starter, engine running, fuel pump and tank,
  lights, pitot heat, alternate static, anti-ice and de-ice, battery, alternator, avionics, autopilot modes,
  flight director, yaw damper, heading bug, selected altitude, altimeter setting, COM1 and NAV1
  active/standby, squawk, airborne/touchdown. Continuous inputs do not spam: trim, the heading bug and the
  altimeter setting log once they stop moving for 0.5 s, the levers log on 5 % steps. A flight restart,
  which changes everything at once, is one `12 CHANGES` line.
- **Key events** the sim reports (`sim_events.py`): every discrete key event Python-SimConnect knows, some
  700 of them (`FLAPS_INCR`, `GEAR_TOGGLE`, `TOGGLE_ALTERNATE_STATIC`, ...), from any source; not `AXIS_*`
  and `*_SET` events, which fire continuously, nor pause, view, slew, ATC and multiplayer keys. They only make a
  line, `EV FLAPS_INCR`, when no state change explains them within 0.3 s, so a press that does nothing
  (flaps already up) still shows, a switch without a rule of its own shows by its event name, and normal
  actions are not logged twice.

Buttons: **Start/Stop** = older entries, **Reset** = newer, **Reset held 1 s** = back to the newest. While
scrolled, new entries do not move the view; `+3 NEW` replaces the age on line 1 instead. Turning the
selector to 3 always shows the newest three. `--events-banner` also flashes each new line for 0.8 s while
another mode is showing. In `--demo` a scripted departure and return plays through the log every 92 s.

Hardware and sim diagnostics, each a small standalone tool:
```
python -m x52_simconnect.mfd libusb0 "line 1" "line 2" "line 3"   # raw write to the display
python -m x52_simconnect.buttons                                  # print button presses (Ctrl-C to stop)
python -m x52_simconnect.logitech_driver                          # check that Logitech's driver is not installed
python -m x52_simconnect.sim_feed ZULU_TIME LOCAL_TIME            # watch the streaming feed for 3 s
python -m x52_simconnect.sim_events                               # print key events as the sim fires them, 30 s
```

### The firmware also draws on the MFD
Function cycles the firmware clock 1/2/3 (it draws a `1`..`3` under our text) and toggles the stopwatch view;
Start/Stop and Reset drive that stopwatch. This cannot be turned off over USB, so Function is unmapped by default
and the bridge redraws the display 0.3 s after any of the three is pressed.

## How it works
Hardware and sim I/O live in three modules; everything else is plain Python that runs without either.

| Module (`x52_simconnect/`) | Role | Needs |
|---|---|---|
| `mfd.py` | MFD driver: vendor request `0x91` with line, clear, brightness, clock, date, shift and blink commands. Writes are cached; transient USB errors are retried and the device is reopened after repeated failures. | stick |
| `buttons.py` | Shared-mode HID reader for all 34 buttons and the mode selector (layout from libx52io). | stick |
| `logitech_driver.py` | Detects Logitech's X52 driver, so the bridge can refuse to start beside it. | - |
| `sim_feed.py` | One SimConnect data definition with every SimVar, pushed every 6th visual frame. Hooks the dispatch of the `SimConnect` package, which otherwise ignores bulk data packets and unknown event ids. | sim |
| `sim_events.py` | Key-event notifications (be told when `FLAPS_INCR` fires anywhere; `all_key_events` is the list the bridge subscribes to) and sending events. | sim |
| `config_server.py` | The config UI: a local web page (`config_ui.html`) and its JSON API, plus the `ConfigStore` that hands a saved config to the main loop. | - |
| `formatting.py` | SimVar value -> display text helpers; all tolerate `None`. | - |
| `templates.py` | The template language pages are written in: `{VAR:5.0f}`, `{VAR|filter}`; parsing, rendering, checking. | - |
| `pages.py` | The built-in pages as templates, and `template_page` for the configured ones. | - |
| `config.py` | The config file: pages and event-log choices, validation with per-input error paths, TOML load and save, the field catalogue. | - |
| `event_rules.py` | The event log's rule table (SimVar, wording, change/settled/step policy) the engine that turns feed values into log lines, and the filter for which key events are worth a notification. | - |
| `apps.py` | One app per selector position (`PagesApp`, `EventLogApp`, placeholder `CommsApp`) behind one `App` protocol; `ALL_VARS`, the union of what they need. | - |
| `display.py` | `Display`: the one writer of MFD text, with the banner, the forced redraw and the current mode. | - |
| `clock_sync.py` | Firmware clock/date and brightness from sim time. | - |
| `sources.py` | `SimSource` (live, with reconnect) and `DemoSource` (fake data) behind one `ensure`/`read`/`close` interface. | - |
| `bridge.py` | CLI and the main loop; `Bridge.step` is its pure body (mode switching, button routing, rendering). | - |

`.claude/skills/` holds the hardware and SimConnect knowledge captured for AI coding agents; `CLAUDE.md` points at it.

## Development
```
pip install -e .[dev]
python -m pytest              # unit tests: fakes for the stick and the feed, no hardware or sim needed
python -m ruff check . && python -m ruff format .
```
The tests cover the formatters, every page and app with demo and all-`None` data, clock and date encoding, the
USB retry and caching logic, HID decoding and edge detection, the display's banner and redraw timing, paging,
mode switching with a scripted selector, every event-log rule policy and wording, scrolling and the key-event
dedupe, CLI validation, the feed's packet parsing and the event subscription against a fake DLL, the
template language, config validation and round trip, and the config UI's server over real HTTP.
CI runs the same on Windows for each push and pull request. Changes to `mfd.py`, `buttons.py`, `sim_feed.py` or `sim_events.py` still
need a check on the real stick or a live flight; `--demo` is the quickest hardware-only check, and
`--demo --no-stick` runs everything, config UI included, with neither.

New features are written up as specs first, see [SPECS.md](SPECS.md).

## Known limits
- USB control transfers to the stick fail sporadically with Windows error 31 ("A device attached to the system is
  not functioning"), typically near a firmware-handled button press. Retried, never fatal.
- libusb-win32 is unmaintained (last release 2021, still WHQL-signed and working on Windows 11).
- Only ASCII is safe on the MFD; the character ROM for bytes > 0x7F is untested.
- The firmware clock keeps ticking between our writes; we rewrite it when the sim minute changes, so time
  acceleration and time jumps are followed within a minute.

## Uninstall the filter
```
"C:\Program Files\LibUSB-Win32\bin\install-filter.exe" uninstall --device=USB\VID_06A3&PID_075C
```
or remove LibUSB-Win32 from Apps & features. Joystick input goes through HidUsb either way and is unaffected.

## License
GNU AGPL-3.0-or-later, see [LICENSE](LICENSE). The bridge subclasses the AGPL-licensed
[Python-SimConnect](https://github.com/odwdinc/Python-SimConnect) package, so the program as a whole is
copyleft; for a desktop tool that already publishes its source this changes nothing in practice. pyusb and
hidapi are BSD.

## Credits
- [nirenjan/libx52](https://github.com/nirenjan/libx52) for the reverse-engineered protocol and HID layout.
- [Python-SimConnect](https://github.com/odwdinc/Python-SimConnect) for the SimConnect bindings.
- [libusb-win32](https://sourceforge.net/projects/libusb-win32/) for the filter driver.
