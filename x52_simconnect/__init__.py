"""MSFS 2024 -> Saitek/Logitech X52 (non-Pro) MFD bridge.

Modules, hardware and sim I/O kept apart from the pure logic so the latter is unit-testable:

- ``mfd``         USB driver for the MFD, clocks and brightness (needs the stick).
- ``buttons``     HID reader for the stick's buttons and mode selector (needs the stick).
- ``sim_feed``    streaming SimConnect feed (needs the sim).
- ``formatting``  SimVar -> display-text helpers (pure).
- ``pages``       the MFD pages and the SimVars they need (pure).
- ``apps``        one app per mode-selector position: the pages, comms, event log (pure).
- ``display``     the MFD writer: banner, forced redraw, current mode (pure, given an MFD).
- ``clock_sync``  firmware clock/date/brightness from sim time (pure, given an MFD).
- ``sources``     ``SimSource`` (live) and ``DemoSource`` (fake data) behind one interface.
- ``bridge``      CLI and main loop: ``python -m x52_simconnect``.
"""

__version__ = "0.1.0"
