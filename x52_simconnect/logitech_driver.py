r"""
Detect Logitech's X52 driver, which must not be installed: it writes to the MFD as well, and with two writers the
stick stops answering for half a minute at a time. The bridge refuses to start while it is there (README.md has the
removal steps).

The driver shows itself as a private device interface on the stick's HID node:

    \\?\HID#VID_06A3&PID_075C#<instance>#{0c244c6f-2c78-4f0c-a036-8db0e9012b27}

CLI:  python -m x52_simconnect.logitech_driver      # says whether the driver is installed
"""

import ctypes
import sys
import uuid

VID, PID = 0x06A3, 0x075C
INTERFACE_GUID = uuid.UUID("0c244c6f-2c78-4f0c-a036-8db0e9012b27")


class _Guid(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, u):
        g = cls()
        g.d1, g.d2, g.d3 = u.time_low, u.time_mid, u.time_hi_version
        for i, b in enumerate(u.bytes[8:]):
            g.d4[i] = b
        return g


def interface_paths():
    """Every present device-interface path of the driver's private interface class. Empty off Windows."""
    if sys.platform != "win32":
        return []
    cm = ctypes.WinDLL("cfgmgr32")
    guid = _Guid.of(INTERFACE_GUID)
    size = ctypes.c_ulong()
    if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(guid), None, 0) != 0:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cm.CM_Get_Device_Interface_ListW(ctypes.byref(guid), None, buf, size.value, 0) != 0:
        return []
    return [path for path in buf[: size.value].split("\0") if path]


def installed(paths=None, vid=VID, pid=PID):
    """True when Logitech's driver sits on the stick. ``paths``: interface paths, looked up when omitted."""
    tag = f"VID_{vid:04X}&PID_{pid:04X}".lower()
    return any(tag in path.lower() for path in (interface_paths() if paths is None else paths))


def main():
    print("Logitech's X52 driver is installed: remove it, see README.md" if installed() else "not installed: good")


if __name__ == "__main__":
    main()
