# x52-simconnect

MSFS 2024 -> Saitek/Logitech X52 (non-Pro) MFD bridge in Python 3.11+ on Windows 11. Public community repo:
keep code and docs shareable, no machine-specific paths, credentials or user names.

Skills in `.claude/skills/`, load them before touching the related code:
- `x52-mfd` - X52 MFD protocol, Windows driver stack, libusb-win32 filter setup, HID button layout, firmware quirks.
- `msfs-simconnect` - Python-SimConnect quirks, streaming feed pattern, SimVar names and units, testing without the sim.

Layout:
- `x52_simconnect/` is the package. Hardware/sim I/O: `mfd.py`, `buttons.py`, `saitek_driver.py`, `sim_feed.py`.
  Pure logic: `formatting.py`, `pages.py`, `apps.py`, `display.py`, `clock_sync.py`, `sources.py`. `bridge.py` is the CLI and main loop
  (`python -m x52_simconnect`). Module docstrings say what each does.
- `tests/` pytest suite with fakes for the stick and the feed; runs without hardware or sim.
- `README.md` user-facing setup, run and development docs. `SPECS.md` indexes the feature specs in `specs/`,
  numbered in build order.

Conventions:
- Keep I/O and logic apart: new display logic goes in a pure module with a test, not in `bridge.py` or a driver.
- Before finishing: `python -m pytest`, `python -m ruff check .`, `python -m ruff format .` (CI runs the same).
- Verify hardware changes on the real stick and sim changes against a live flight; `--demo` and the all-`None`
  rendering tests are the offline checks. Keep both working.
- Record any new hardware or SimConnect fact you had to discover in the matching skill.
