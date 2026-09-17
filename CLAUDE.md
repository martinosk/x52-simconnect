# x52-simconnect

MSFS 2024 -> Saitek/Logitech X52 (non-Pro) MFD bridge in Python 3.14 on Windows 11. Intended to become a public
community repo, so keep code and docs in a shareable state: no machine-specific paths, credentials or user names.

Skills in `.claude/skills/`, load them before touching the related code:
- `x52-mfd` - X52 MFD protocol, Windows driver stack, libusb-win32 filter setup, HID button layout, firmware quirks.
- `msfs-simconnect` - Python-SimConnect quirks, streaming feed pattern, SimVar names and units, testing without the sim.

Layout:
- `mfd_sim.py` bridge, `sim_feed.py` streaming SimConnect feed, `x52_mfd.py` MFD driver, `x52_buttons.py` HID reader.
- `README.md` user-facing setup and run docs. `SPECS.md` indexes the feature specs in `specs/`, numbered in build order.

Conventions:
- Verify hardware changes on the real stick and sim changes against a live flight; demo mode (`--demo`) and
  all-None rendering are the offline tests. Keep both working.
- Record any new hardware or SimConnect fact you had to discover in the matching skill.
