---
name: x52-mfd
description: Drive the MFD, brightness and clocks of a Saitek/Logitech X52 (non-Pro) from Python on Windows, and read its MFD buttons. Use when a task touches the X52 joystick, its LCD, DirectOutput, libusb, or the HID report of the stick. Covers device identification, the USB protocol, the Windows driver stack, and the gotchas that cost time.
---

# X52 (non-Pro) MFD from Python on Windows

Working implementation in the `x52_simconnect` package: `mfd.py` driver incl. clock/date/brightness,
`buttons.py` HID reader, `saitek_driver.py` mode selector via Logitech's driver, `sim_feed.py` streaming
SimConnect feed, `bridge.py` main loop. Read those before
writing new code; extend them rather than duplicating. `tests/test_mfd.py` and `tests/test_buttons.py` show how
to test against a fake device. `SPECS.md` holds the specs for the next features.

## 1. Identify the stick first. Everything depends on it.

| Model | USB ID | MFD control path |
|---|---|---|
| X52 Pro | `06A3:0762` | Logitech **DirectOutput SDK** (documented, LEDs are RGB, MFD has page/scroll buttons) |
| X52 non-Pro (this repo's stick) | `06A3:075C` (older units `06A3:0255`) | **Raw USB vendor control transfers**. DirectOutput does NOT support it. |

Check what is plugged in:
```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_06A3' } | Select Status,FriendlyName,InstanceId
```
Every existing MSFS X52 plugin (FS20-SaiMFD, x52msfsout, flightsim.to LED plugin) is DirectOutput and therefore Pro-only.
Do not suggest them for a non-Pro.

## 2. Protocol (non-Pro), from the libx52 project (nirenjan/libx52, Linux)

One control transfer per command. `bmRequestType=0x40` (vendor, device, host-to-device), `bRequest=0x91`,
`wIndex=command`, `wValue=payload`, no data stage.

| wIndex | Meaning | wValue |
|---|---|---|
| `0xD1` / `0xD2` / `0xD4` | select MFD line 1 / 2 / 3 and **append two chars** | `char[i] \| char[i+1] << 8` |
| line `\| 0x08` | clear that line | 0 |
| `0xB1` | MFD brightness | 0..128 |
| `0xB2` | LED brightness | 0..128 |
| `0xC0` | clock 1 time (verified live) | `h24<<15 \| hour<<8 \| minute`; the firmware keeps it ticking afterwards |
| `0xC1` / `0xC2` | clock 2 / 3 as offset from clock 1 (verified) | `h24<<15 \| negative<<10 \| abs_minutes&0x3FF`, max 1023 min, wrap by subtracting 1440 |
| `0xC4` then `0xC8` | date (verified) | `month<<8 \| day` for DD-MM layout, then `year % 100` |
| `0xFD` | shift indicator | `0x51` on, `0x50` off |
| `0xB4` | POV blink | `0x51` on, `0x50` off |
| `0xB8` | individual LED colour | Pro only |

A line is 16 chars. Write = clear command, then 8 char-pair writes. Keep an in-memory copy of what is shown and
skip unchanged lines (see `X52Mfd.set_line`). ASCII only is verified; bytes > 0x7F untested.

**Transfers fail sporadically.** Seen live after ~minutes of use, close to a firmware-handled button press:
`usb.core.USBError: libusb0-dll:err [control_msg] sending control message failed, win error: A device attached to
the system is not functioning.` (Windows error 31). It is transient. Retry each transfer 3x with ~50 ms gaps, mark
the line as unknown on failure so it gets rewritten, and if several writes in a row fail, dispose the handle and
`usb.core.find` the stick again (`X52Mfd.reopen`). Never let one failed transfer kill the app.

## 3. Windows driver stack and why libusb-1.0 fails

The stick is a single-interface HID device. Its stack after installing Logitech's driver 8.0.116.0:
```
USB node : libusb0 (added by us) > HidUsb > SaiU075C (Logitech lower filter) > USBHUB3
HID node : hidgamepad > SaiK075C (Logitech "programming driver", upper filter) > HidUsb
```
- **libusb-1.0 (pip `libusb` + pyusb libusb1 backend) cannot send vendor requests to a HidUsb-bound device.**
  It raises `NotImplementedError: Operation not supported or unimplemented on this platform`. Not fixable in code.
- **WinUSB via Zadig / libusbK / UsbDk all replace HidUsb**, which kills the joystick for Windows and the sim. Do not use them here.
- **libusb-win32 filter driver** (1.2.7.3, 2021, WHQL-signed, works on Windows 11) is the only thing that sits beside
  HidUsb. Use pyusb's **libusb0** backend with it: `usb.backend.libusb0.get_backend()`. libusb-1.0 does not talk to the filter.
- The proper long-term path is Logitech's own `SaiK075C.sys`, which the X52 profiler uses to write the MFD. Its
  IOCTLs are recovered in section 5 (vendor-command IOCTLs on the SaitekDevice interface); wiring the MFD to
  them is not done yet.

## 4. Installing the filter (once per machine)

1. `libusb-win32-devel-filter-1.2.7.3.exe` from SourceForge (unsigned NSIS installer, `/S` for silent, needs UAC).
   It launches `install-filter-win.exe` at the end; pick the X52 there, or use the CLI:
   `"C:\Program Files\LibUSB-Win32\bin\install-filter.exe" install --device=USB\VID_06A3&PID_075C`
2. **The filter only loads on re-enumeration.** `pnputil /restart-device` and disable/enable both fail with
   "Device is pending system reboot to complete a previous operation". Unplug and replug the stick (or reboot).
3. Verify. This must list `\Driver\libusb0` above `\Driver\HidUsb`, and the `libusb0` service must be Running:
   ```powershell
   (Get-PnpDeviceProperty -InstanceId 'USB\VID_06A3&PID_075C\<instance>' -KeyName DEVPKEY_Device_Stack).Data
   (Get-Service libusb0).Status
   "C:\Program Files\LibUSB-Win32\bin\testlibusb.exe"     # should list "Saitek X52 Flight Control System"
   ```
4. Undo: `install-filter.exe uninstall --device=USB\VID_06A3&PID_075C` or uninstall LibUSB-Win32. HID input is untouched either way.

Python deps: `pip install -e .` from the repo root (the `libusb` pip package only for the negative libusb1 test).

## 5. Reading the MFD buttons (paging) without stealing the joystick

Open the HID device shared with `hidapi` (`hid.device().open(0x06A3, 0x075C)`). Input report is 14 bytes
(hidapi on Windows prefixes a report-id byte, so strip a 15th). Layout from libx52io `parser.c`:

- bytes 0-3 LE: X (11 bit) | Y (11 bit) | RZ (10 bit); bytes 4-7: Z, RX, RY, slider; byte 12 hi nibble: hat; byte 13: thumb XY nibbles
- bytes 8-12: 40-bit little-endian button field. Bit index: 0 trigger, 1 fire, 2-4 A/B/C, 5 pinky, 6-7 D/E,
  8-13 T1-T3 up/down, 14 trigger stage 2, 15-22 POV hats, **23-25 mode 1/2/3, 26 Function, 27 Start/Stop, 28 Reset**,
  29 clutch, 30-33 mouse buttons / scroll.

The stick only sends a report when something changes, so a read loop with no user input returns nothing. That is
normal, not a permissions problem. Edge-detect presses in a thread (`ButtonReader`).

**The mode selector is stripped from HID with Logitech's driver installed** (verified 2026-09 on driver
8.0.116.0, filters `SaiK075C` on the HID node and `SaiU075C` on the USB node): bits 23-25 are zero in every HID
report and in the Windows joystick API, in all three positions, profiler running or not, replug or not. Other
buttons (Start/Stop = bit 27) arrive fine and the descriptor still declares 34 buttons. MSFS cannot see the
selector either. Dead ends: HID `GET_REPORT` through libusb0 returns a constant 8 bytes `1b 00 ...`; interrupt
reads on endpoint 0x81 through the libusb0 filter fail with `invalid configuration 0` (HidUsb configured the
device; forcing `set_configuration` would reset its pipes, not tried).

**Read it from the driver instead: `x52_simconnect/saitek_driver.py`.** The filter adds private device
interfaces to the HID node; the profiler's `Sd.Devices.dll` (.NET, readable with dnfile/dncil or ILSpy) names
them and their IOCTLs:
- `{0c244c6f-2c78-4f0c-a036-8db0e9012b27}` ("TorontoDevice", profiles): `GetCurrentShiftState` 0x222848, no
  input, 16-byte output = GUID of the active shift state. The X52 plugin
  (`Controllers/e81d998b_...dll`) defines them: Mode 1 `cd957a00-26bf-4577-89ff-676166fe3b28`, Mode 2
  `9fc9dcb2-8bbf-482e-9dd0-c76e22bbc381`, Mode 3 `e536be07-7569-4d2f-878a-795457b889d7`, and `d056b485-...`,
  `1cab9df5-...`, `b7522229-...` for Mode 1/2/3 with the pinkie switch held. Also `GetVersion` 0x222820
  (16 bytes, four uint32: 8.0.116.0), `IsProfileActive` 0x222828 (4 bytes), `GetProfile` 0x222804,
  `SetProfile` 0x222800, `Clear` 0x222808, `Activate` 0x22280C, `StartCplMode` 0x22282C / `StopCplMode`
  0x222830 (no buffers). Wrong buffer sizes give Windows error 1784.
- `{1493ba78-4807-418c-a8ca-bab2fe9eb28a}` ("SaitekDevice"): `WriteVendorCommand` 0x222BC0,
  `ReadVendorCommand` 0x222BC4, `VendorInCommand` 0x222BC8, `VendorOutCommand` 0x222BCC (request 0x91 out /
  0x90 in, value, index; the same vendor requests libx52 uses). Untested here, but this is the route to drive
  the MFD through Logitech's driver without libusb-win32.
Open the interface path with `CreateFileW` (GENERIC_READ|WRITE, share all) and poll with `DeviceIoControl`;
an IOCTL at 4 Hz is free. Without the Logitech package the interface does not exist; then `buttons.py` sees
the selector bits itself (as libx52io does on Linux). Mode switching is `Bridge.step` in `bridge.py` with the
apps in `apps.py`; `--mode N` forces one.

**The three MFD buttons are also handled by the stick firmware and this cannot be disabled over USB** (verified on
the real stick): Function cycles the firmware clock 1/2/3 and draws a `1`..`3` over your text, then toggles the
stopwatch view; Start/Stop and Reset drive that stopwatch. Consequences for any app that uses them:
- Prefer Start/Stop and Reset for paging, leave Function alone, or use buttons the sim ignores (mouse scroll wheel).
- Force a full redraw of all three lines ~0.3 s after any of these presses to overwrite what the firmware drew.
- Do not rely on the firmware digits as a page indicator; draw your own (e.g. a short `P2/5 TITLE` banner).

## 6. Quick commands

```
python -m x52_simconnect.mfd libusb0 "line 1" "line 2" "line 3"
python -m x52_simconnect.buttons        # prints presses and the mode; Ctrl-C to stop (no --help)
python -m x52_simconnect --demo --cycle 3
```
`X52 (06A3:075C) not found; is the libusb0 filter installed and loaded?` means the filter is not in the live
stack: see step 4.2. The bridge is a foreground loop; when running it from a script or agent, give it a timeout.
