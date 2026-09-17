"""
Driver for the MFD of a non-Pro Saitek/Logitech X52 (USB 06A3:075C).

Protocol (reverse-engineered by the libx52 project, https://github.com/nirenjan/libx52):
  USB vendor control transfer, bmRequestType 0x40, bRequest 0x91,
  wIndex = command, wValue = payload.
    line 1/2/3 select : 0xD1 / 0xD2 / 0xD4
    | 0x08            : clear that line
    | 0x00            : append two chars packed little-endian in wValue
    0xB1              : MFD brightness (0..128)
  Each line is 16 characters. Clock, date, brightness and indicator encodings are in
  the pack_* functions below (from libx52 control.c).

Windows needs the libusb-win32 *filter* driver on the stick (see README.md) and
pyusb's libusb0 backend. The libusb1 backend is kept for the record: it fails
while the stick is bound to HidUsb, because that backend rejects vendor requests.

CLI:  python -m x52_simconnect.mfd [libusb0|libusb1] "line 1" "line 2" "line 3"
"""

import contextlib
import sys
import time

import usb.core
import usb.util

VID, PID = 0x06A3, 0x075C  # X52 non-Pro (Pro is 0x0762 and uses DirectOutput instead)
REQ = 0x91
LINE_CMDS = (0xD1, 0xD2, 0xD4)
CLEAR = 0x08
MFD_BRIGHTNESS = 0xB1
LED_BRIGHTNESS = 0xB2
TIME_CLOCK1 = 0xC0
OFFS_CLOCK2 = 0xC1
OFFS_CLOCK3 = 0xC2
DATE_DDMM = 0xC4
DATE_YEAR = 0xC8
SHIFT_INDICATOR = 0xFD
BLINK_INDICATOR = 0xB4
INDICATOR_ON, INDICATOR_OFF = 0x51, 0x50
LINE_LEN = 16
MAX_BRIGHTNESS = 128
BM_OUT_VENDOR = usb.util.build_request_type(
    usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_DEVICE
)


# ---------------------------------------------------------------- payload encodings (pure)


def pack_chars(a, b):
    """Two characters -> one wValue, first character in the low byte."""
    return (ord(a) & 0xFF) | ((ord(b) & 0xFF) << 8)


def pack_clock(hour, minute, h24=True):
    return (int(bool(h24)) << 15) | ((hour & 0x7F) << 8) | (minute & 0xFF)


def pack_clock_offset(offset_minutes, h24=True):
    """Offset from clock 1 in minutes. The field holds +/- 1023, so wrap like libx52 does."""
    offset = int(offset_minutes)
    negative = offset < 0
    offset = abs(offset)
    while offset > 1023:
        offset -= 1440
    if offset < 0:
        negative = not negative
        offset = -offset
    return (int(bool(h24)) << 15) | (int(negative) << 10) | (offset & 0x3FF)


def pack_date_ddmm(day, month):
    return ((month & 0xFF) << 8) | (day & 0xFF)


def pack_year(year):
    return year % 100


def clamp_brightness(level):
    return max(0, min(MAX_BRIGHTNESS, int(level)))


# ---------------------------------------------------------------- device


def get_backend(name="libusb0"):
    if name == "libusb1":
        import libusb  # pip package bundling libusb-1.0.dll
        import usb.backend.libusb1 as b

        return b.get_backend(find_library=lambda _: libusb.dll._name)
    if name == "libusb0":
        import usb.backend.libusb0 as b

        return b.get_backend()
    raise ValueError(f"unknown backend {name}")


def find_device(backend_name="libusb0"):
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=get_backend(backend_name))
    if dev is None:
        raise RuntimeError(f"X52 ({VID:04X}:{PID:04X}) not found; is the libusb0 filter installed and loaded?")
    return dev


class X52Mfd:
    """Three 16-character lines plus clocks, date, brightness and the two indicators.
    Every write is cached, so repeating the same text or value costs no USB traffic."""

    RETRIES = 3  # per control transfer
    RETRY_DELAY = 0.05

    def __init__(self, backend="libusb0", dev=None):
        """``dev``: an already found pyusb device (or a stand-in with ``ctrl_transfer``); found via
        ``find_device(backend)`` when omitted."""
        self._backend_name = backend
        self.dev = None
        self._shown = [None, None, None]
        self._state = {}
        self.open(dev)

    def open(self, dev=None):
        self.dev = dev if dev is not None else find_device(self._backend_name)
        self._shown = [None, None, None]  # nothing is known to be on screen any more
        self._state = {}  # cached clock/date/brightness writes

    def reopen(self):
        """Drop the handle and find the stick again. Call after repeated USBError."""
        with contextlib.suppress(Exception):  # disposing a dead handle may itself fail
            usb.util.dispose_resources(self.dev)
        self.dev = None
        self.open()

    def _vendor(self, index, value=0):
        # The stick occasionally fails one transfer with Windows error 31 "A device attached to the
        # system is not functioning" (seen right after a firmware-handled button press). Retry briefly.
        for attempt in range(self.RETRIES):
            try:
                self.dev.ctrl_transfer(BM_OUT_VENDOR, REQ, value, index, None, timeout=1000)
                return
            except usb.core.USBError:
                if attempt == self.RETRIES - 1:
                    raise
                time.sleep(self.RETRY_DELAY)

    # ------------------------------------------------------------ text

    def set_line(self, line, text, force=False):
        text = str(text)[:LINE_LEN].ljust(LINE_LEN)
        if not force and self._shown[line] == text:
            return
        cmd = LINE_CMDS[line]
        self._shown[line] = None  # partial writes leave the line unknown
        self._vendor(cmd | CLEAR)
        for i in range(0, LINE_LEN, 2):
            self._vendor(cmd, pack_chars(text[i], text[i + 1]))
        self._shown[line] = text

    def set_lines(self, lines, force=False):
        for i, text in enumerate(list(lines)[:3]):
            self.set_line(i, text, force)

    def clear(self):
        self.set_lines(["", "", ""], force=True)

    # ------------------------------------------------------------ clock, date, brightness, indicators

    def _cached(self, key, index, value):
        if self._state.get(key) == (index, value):
            return
        self._vendor(index, value)
        self._state[key] = (index, value)

    def set_clock(self, hour, minute, h24=True):
        """Clock 1 (the firmware clock shown under the text). The firmware keeps it ticking."""
        self._cached("clock1", TIME_CLOCK1, pack_clock(hour, minute, h24))

    def set_clock_offset(self, clock, offset_minutes, h24=True):
        """Clock 2 or 3 as an offset from clock 1, in minutes."""
        index = {2: OFFS_CLOCK2, 3: OFFS_CLOCK3}[clock]
        self._cached(f"clock{clock}", index, pack_clock_offset(offset_minutes, h24))

    def set_date(self, day, month, year):
        """DD-MM-YY layout on the firmware date display."""
        self._cached("date_ddmm", DATE_DDMM, pack_date_ddmm(day, month))
        self._cached("date_year", DATE_YEAR, pack_year(year))

    def set_brightness(self, level):
        self._cached("mfd_brightness", MFD_BRIGHTNESS, clamp_brightness(level))

    def set_led_brightness(self, level):
        self._cached("led_brightness", LED_BRIGHTNESS, clamp_brightness(level))

    # Individual LED colours (0xB8) are X52 Pro only. The non-Pro has just these two indicators:
    def set_shift(self, on):
        """The SHIFT indicator on the MFD."""
        self._cached("shift", SHIFT_INDICATOR, INDICATOR_ON if on else INDICATOR_OFF)

    def set_blink(self, on):
        """Blink the POV hat and clutch LEDs."""
        self._cached("blink", BLINK_INDICATOR, INDICATOR_ON if on else INDICATOR_OFF)


def main():
    backend_name = sys.argv[1] if len(sys.argv) > 1 else "libusb0"
    lines = sys.argv[2:] or ["x52-simconnect", "MFD test", time.strftime("%H:%M:%S")]
    mfd = X52Mfd(backend_name)
    dev = mfd.dev
    print(f"backend={backend_name} device={dev.idVendor:04x}:{dev.idProduct:04x} bus={dev.bus} addr={dev.address}")
    for i, text in enumerate(lines[:3]):
        mfd.set_line(i, text, force=True)
        print(f"line {i + 1}: {text!r}")


if __name__ == "__main__":
    main()
