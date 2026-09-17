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
| Firmware clock 1/2/3, date and MFD/LED brightness from sim time | Working |
| Auto-reconnect when the sim starts/stops; recovery from transient USB errors | Working |
| Mode-selector apps, event log, comms, aircraft profiles, buttons -> sim events | Specified, see [SPECS.md](SPECS.md) |

## Requirements
- Windows 10/11, Python 3.11+ (developed on 3.14), MSFS 2024 or 2020 (any edition; the Store build works).
- X52 non-Pro with Logitech's driver installed (it is only needed for joystick input; we do not use its software).
- The **libusb-win32 filter driver** on the stick (one-time setup below). It sits *beside* the Windows HID driver,
  so the joystick keeps working in the sim. WinUSB/Zadig would replace the HID driver and break joystick input.

## Setup
```
pip install -r requirements.txt
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
python mfd_sim.py                 # waits for MSFS, then shows live data
python mfd_sim.py --demo          # fake data, no sim needed
python mfd_sim.py --cycle 5       # auto-advance pages
python mfd_sim.py --no-clock --no-auto-brightness
python mfd_sim.py --next MOUSE_SCROLL_UP --prev MOUSE_SCROLL_DN   # remap paging; --list-buttons for names
python x52_mfd.py libusb0 "line 1" "line 2" "line 3"              # raw write
python x52_buttons.py             # print button presses (Ctrl-C to stop)
python sim_feed.py ZULU_TIME LOCAL_TIME                           # watch the streaming feed
```
Default buttons: **Start/Stop** = next page, **Reset** = previous page. A `P2/5 RADIO` banner flashes on each change.

### The MFD buttons are also handled by the stick firmware
Function cycles the firmware clock 1/2/3 (it draws a `1`..`3` under our text) and toggles the stopwatch view;
Start/Stop and Reset drive that stopwatch. This cannot be turned off over USB, so Function is unmapped by default
and the display is force-redrawn 0.3 s after any of these buttons to overwrite whatever the firmware drew.

## How it works
- `x52_mfd.py` - MFD driver: vendor request `0x91` with line, clear, brightness, clock, date, shift and blink
  commands (protocol documented in the file). Writes are cached; transient USB errors are retried and the device
  is reopened after repeated failures.
- `sim_feed.py` - one SimConnect data definition with every SimVar, pushed every 6th visual frame. Hooks the
  dispatch of the `SimConnect` Python package, which otherwise ignores bulk data packets.
- `x52_buttons.py` - shared-mode HID reader for all 34 buttons and the mode selector (layout from libx52io).
- `mfd_sim.py` - the bridge: pages, paging, clock sync, brightness, demo mode, reconnect logic.
- `.claude/skills/` - knowledge captured for AI coding agents (protocol, driver stack, SimConnect quirks).
  `CLAUDE.md` points at them.

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

## Credits
- [nirenjan/libx52](https://github.com/nirenjan/libx52) for the reverse-engineered protocol and HID layout.
- [Python-SimConnect](https://github.com/odwdinc/Python-SimConnect) for the SimConnect bindings.
- [libusb-win32](https://sourceforge.net/projects/libusb-win32/) for the filter driver.
