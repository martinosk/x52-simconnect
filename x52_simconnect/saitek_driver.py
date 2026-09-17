r"""
Read the X52 mode selector through Logitech's own filter driver on Windows.

With Logitech's X52 software installed, its HID filter driver (SaiK075C) removes the selector bits 23-25
from every input report, so neither hidapi nor the sim ever sees them. The profiler still follows the
switch, and it does so through a private device interface the filter adds to the HID node:

    \\?\HID#VID_06A3&PID_075C#<instance>#{0c244c6f-2c78-4f0c-a036-8db0e9012b27}

IOCTL 0x222848 ("GetCurrentShiftState" in the profiler's Sd.Devices.dll) with no input and a 16-byte
output returns the GUID of the active shift state; the X52 plugin defines one per selector position, with
and without the pinkie switch held. Recovered from the profiler's .NET assemblies, verified on driver
8.0.116.0. Without the Logitech driver the interface does not exist and ``DriverModeReader`` raises;
the HID reader in buttons.py then sees the selector bits itself.

CLI:  python -m x52_simconnect.saitek_driver      # prints the selector position, Ctrl-C to stop
"""

import ctypes
import uuid

VID, PID = 0x06A3, 0x075C
INTERFACE_GUID = uuid.UUID("0c244c6f-2c78-4f0c-a036-8db0e9012b27")  # TorontoDevice in the profiler
IOCTL_GET_CURRENT_SHIFT_STATE = 0x222848
SHIFT_STATE_LEN = 16

# shift-state GUID -> (mode, pinkie held), from the X52 controller plugin's device description
SHIFT_STATES = {
    uuid.UUID("cd957a00-26bf-4577-89ff-676166fe3b28"): (1, False),
    uuid.UUID("9fc9dcb2-8bbf-482e-9dd0-c76e22bbc381"): (2, False),
    uuid.UUID("e536be07-7569-4d2f-878a-795457b889d7"): (3, False),
    uuid.UUID("d056b485-0877-477f-952b-0ba8be61cee7"): (1, True),
    uuid.UUID("1cab9df5-4c24-4b13-b2e5-9a07957bb1fa"): (2, True),
    uuid.UUID("b7522229-9442-4aa0-b7b5-4c120a716742"): (3, True),
}

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ_WRITE = 0x3
OPEN_EXISTING = 3
INVALID_HANDLE = 2**64 - 1


def decode_shift_state(data):
    """16 bytes from the driver -> ``(mode, pinkie)``, or None for anything it does not recognise."""
    if len(data) != SHIFT_STATE_LEN:
        return None
    return SHIFT_STATES.get(uuid.UUID(bytes_le=bytes(data)))


class _Guid(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, u):
        g = cls()
        g.d1, g.d2, g.d3 = u.time_low, u.time_mid, u.time_hi_version
        for i, b in enumerate(u.bytes[8:]):
            g.d4[i] = b
        return g


def find_interface(vid=VID, pid=PID):
    """The device-interface path of the driver's private interface for the stick, or None."""
    cm = ctypes.WinDLL("cfgmgr32")
    guid = _Guid.of(INTERFACE_GUID)
    size = ctypes.c_ulong()
    if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(guid), None, 0) != 0:
        return None
    buf = ctypes.create_unicode_buffer(size.value)
    if cm.CM_Get_Device_Interface_ListW(ctypes.byref(guid), None, buf, size.value, 0) != 0:
        return None
    tag = f"VID_{vid:04X}&PID_{pid:04X}".lower()
    for path in buf[: size.value].split("\0"):
        if tag in path.lower():
            return path
    return None


class DriverModeReader:
    """Polls the filter driver for the selector position. ``read()`` per loop tick, ``mode`` keeps the last
    good answer. Raises OSError when the interface is missing (no Logitech driver) or cannot be opened."""

    def __init__(self, path=None):
        import ctypes.wintypes as w

        self.path = path or find_interface()
        if not self.path:
            raise OSError("Logitech X52 driver interface not found")
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k.CreateFileW.restype = w.HANDLE
        self._k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        self._k.DeviceIoControl.argtypes = [
            w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD,
            ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p,
        ]  # fmt: skip
        self._k.CloseHandle.argtypes = [w.HANDLE]
        self._dword = w.DWORD
        self._handle = self._k.CreateFileW(
            self.path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
        )
        if self._handle in (None, INVALID_HANDLE):
            raise OSError(f"cannot open {self.path}: Windows error {ctypes.get_last_error()}")
        self.mode = None
        self.pinkie = False
        self.error = None

    def read(self):
        """Ask the driver once. Returns the mode (1, 2, 3) or None when the answer is missing or unknown."""
        out = (ctypes.c_ubyte * SHIFT_STATE_LEN)()
        returned = self._dword(0)
        ok = self._k.DeviceIoControl(
            self._handle, IOCTL_GET_CURRENT_SHIFT_STATE, None, 0, out, SHIFT_STATE_LEN, ctypes.byref(returned), None
        )
        if not ok:
            self.error = ctypes.get_last_error()
            return None
        state = decode_shift_state(bytes(out[: returned.value]))
        if state is None:
            return None
        self.mode, self.pinkie = state
        self.error = None
        return self.mode

    def close(self):
        if self._handle not in (None, INVALID_HANDLE):
            self._k.CloseHandle(self._handle)
            self._handle = None


def main():
    import time

    rd = DriverModeReader()
    print(f"reading {rd.path}\nturn the mode selector, Ctrl-C to quit")
    last = None
    try:
        while True:
            rd.read()
            state = (rd.mode, rd.pinkie)
            if state != last:
                print(f"mode {rd.mode} pinkie {'held' if rd.pinkie else 'up'}")
                last = state
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        rd.close()


if __name__ == "__main__":
    main()
