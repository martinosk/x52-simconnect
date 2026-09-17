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
- X52 non-Pro with Logitech's driver installed (it is only needed for joystick input; we do not use its software).
- The **libusb-win32 filter driver** on the stick (one-time setup below). It sits *beside* the Windows HID driver,
  so the joystick keeps working in the sim. WinUSB/Zadig would replace the HID driver and break joystick input.

## Setup
```
git clone <this repo> && cd x52-simconnect
pip install -e .
```
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

## Run
```
python -m x52_simconnect                  # waits for MSFS, then shows live data
python -m x52_simconnect --demo           # fake data, no sim needed
python -m x52_simconnect --cycle 5        # auto-advance pages
python -m x52_simconnect --mode 3         # force a mode, ignore the stick's selector
python -m x52_simconnect --events-banner  # flash each new event-log line in the other modes too
python -m x52_simconnect --no-clock --no-auto-brightness
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

With Logitech's X52 software installed, its filter driver removes the selector from the HID reports, so MSFS
never sees it and cannot bind it. The bridge asks that driver directly instead (the same private request the
X52 Profiler uses, see `saitek_driver.py`) and falls back to the HID reports when the driver is absent.

Default buttons in mode 1: **Start/Stop** = next page, **Reset** = previous page. A `P2/5 RADIO` banner flashes on
each change. Start/Stop and Reset keep their own meaning inside each app.

### Event log (mode 3)
```
 3s FLAPS 2
41s PARK BRK OFF
58s GEAR DOWN
```
A rolling log of the last 100 cockpit actions, collected in every mode, each line with its age. Two sources:
- **State changes** in the streaming feed (`event_rules.py`, a table of SimVar, wording and policy): flaps, gear,
  parking brake, spoilers, trim, throttle, lights, pitot heat, battery, alternator, autopilot modes, heading
  bug, selected altitude, COM1 active/standby, squawk, airborne/touchdown. Continuous inputs do not spam:
  trim and the heading bug log once they stop moving for 0.5 s, throttle logs on 5 % steps.
- **Key events** the sim reports (`sim_events.py`): `FLAPS_INCR`, `GEAR_TOGGLE`, `AP_MASTER`, ... from any
  source. They only make a line, `EV FLAPS_INCR`, when no state change explains them within 0.3 s, so a press
  that does nothing (flaps already up) still shows, and normal actions are not logged twice.

Buttons: **Start/Stop** = older entries, **Reset** = newer, **Reset held 1 s** = back to the newest. While
scrolled, new entries do not move the view; `+3 NEW` replaces the age on line 1 instead. Turning the
selector to 3 always shows the newest three. `--events-banner` also flashes each new line for 0.8 s while
another mode is showing. In `--demo` a scripted departure and return plays through the log every 92 s.

Hardware and sim diagnostics, each a small standalone tool:
```
python -m x52_simconnect.mfd libusb0 "line 1" "line 2" "line 3"   # raw write to the display
python -m x52_simconnect.buttons                                  # print button presses (Ctrl-C to stop)
python -m x52_simconnect.saitek_driver                            # print the mode selector via the Logitech driver
python -m x52_simconnect.sim_feed ZULU_TIME LOCAL_TIME            # watch the streaming feed for 3 s
python -m x52_simconnect.sim_events                               # print key events as the sim fires them, 30 s
```

### The MFD buttons are also handled by the stick firmware
Function cycles the firmware clock 1/2/3 (it draws a `1`..`3` under our text) and toggles the stopwatch view;
Start/Stop and Reset drive that stopwatch. This cannot be turned off over USB, so Function is unmapped by default
and the display is force-redrawn 0.3 s after any of these buttons to overwrite whatever the firmware drew.

## How it works
Hardware and sim I/O live in three modules; everything else is plain Python that runs without either.

| Module (`x52_simconnect/`) | Role | Needs |
|---|---|---|
| `mfd.py` | MFD driver: vendor request `0x91` with line, clear, brightness, clock, date, shift and blink commands. Writes are cached; transient USB errors are retried and the device is reopened after repeated failures. | stick |
| `buttons.py` | Shared-mode HID reader for all 34 buttons and the mode selector (layout from libx52io). | stick |
| `saitek_driver.py` | Mode selector via Logitech's filter driver, which hides it from HID; recovered from the profiler's own device library. | stick + Logitech driver |
| `sim_feed.py` | One SimConnect data definition with every SimVar, pushed every 6th visual frame. Hooks the dispatch of the `SimConnect` package, which otherwise ignores bulk data packets and unknown event ids. | sim |
| `sim_events.py` | Key-event notifications (be told when `FLAPS_INCR` fires anywhere) and sending events. | sim |
| `formatting.py` | SimVar value -> display text helpers; all tolerate `None`. | - |
| `pages.py` | The five pages: their SimVars and 16-character renderings. | - |
| `event_rules.py` | The event log's rule table (SimVar, wording, change/settled/step policy) and the engine that turns feed values into log lines. | - |
| `apps.py` | One app per selector position (`PagesApp`, `EventLogApp`, placeholder `CommsApp`) behind one `App` protocol; `ALL_VARS` and `ALL_EVENTS`, the union of what they need. | - |
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
dedupe, CLI validation, the feed's packet parsing and the event subscription against a fake DLL.
CI runs the same on Windows for each push and pull request. Changes to `mfd.py`, `buttons.py`, `saitek_driver.py`, `sim_feed.py` or `sim_events.py` still
need a check on the real stick or a live flight; `--demo` is the quickest hardware-only check.

New features are written up as specs first, see [SPECS.md](SPECS.md).

## Known limits
- USB control transfers to the stick fail sporadically with Windows error 31 ("A device attached to the system is
  not functioning"), typically near a firmware-handled button press. Retried, never fatal.
- libusb-win32 is unmaintained (last release 2021, still WHQL-signed and working on Windows 11). The cleaner
  long-term path is Logitech's own `SaiK075C` filter driver, whose IOCTLs would need reversing.
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
