# Feature specs

One file per feature in `specs/`, written so an AI agent or a contributor can pick any of them up cold. Before
starting one: read `CLAUDE.md`, the `x52-mfd` and `msfs-simconnect` skills, `README.md`, and the spec's "Depends
on" line. Each spec lists its offline tests; put them in `tests/` next to the existing ones.

| # | Spec | What | Depends on | Effort |
|---|---|---|---|---|
| 01 | [Mode-selector display modes](specs/01-mode-selector.md) | The stick's mode switch (1/2/3) picks which "app" the MFD shows | - | small |
| 02 | [Event log (mode 3)](specs/02-event-log.md) | Rolling log of what was last triggered: flaps, gear, brake, trim, AP ... | 01 | medium |
| 03 | [Aircraft profiles](specs/03-aircraft-profiles.md) | Pages per aircraft: profiles matched on the aircraft title (templates and the config file exist since 06) | 06 | small |
| 04 | [Bidirectional control](specs/04-bidirectional-control.md) | Stick buttons fire sim events, with MFD feedback | 01 (mode as layer), 03 (templates) | medium |
| 06 | [Config UI](specs/06-config-ui.md) | Browser page served by the bridge: build the mode 1 pages from templates, choose what the mode 3 log shows | 01, 02 | medium, done |
| 05 | [Comms (mode 2)](specs/05-comms.md) | Tuned station now; ATC text later via BeyondATC log or a toolbar addon | 01 | small, then large |

Suggested order: 01 -> 02 -> 03 -> 04 -> 05 (the ATC-text part of 05 is optional and the only one needing an
in-sim addon).

## Shared TODOs
- [x] `Display` object (MFD, banner, forced redraw, current mode) in `x52_simconnect/display.py`, `App`
      protocol and `PagesApp` in `x52_simconnect/apps.py`. Done in 01, everything else builds on it.
- [x] Shared template renderer (`{VAR:fmt}`, `{VAR|filter}`) over `x52_simconnect/formatting.py`: `templates.py`,
      built in 06; 03 and 04 reuse it.
- [ ] `SimFeed` extensions: custom `(name, unit)` datums not in Python-SimConnect's table, and STRING256 datums
      (05 wants `COM ACTIVE FREQ IDENT`, 03 wants `TITLE`).
- [ ] Windows autostart: launch the bridge when MSFS starts (a tray app that waits for the process, or a shortcut
      next to the sim launcher).
- [ ] Longer term: replace libusb-win32 with Logitech's own `SaiK075C` IOCTLs (see `x52-mfd` skill).
