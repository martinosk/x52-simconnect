# Spec 05 - Comms (mode 2): tuned station now, ATC text later

**Depends on:** 01 (App protocol). **Needs:** `SimFeed` support for custom datums and STRING256 (shared TODO).

## Goal
Mode 2 shows what the radios are doing. In three stages:

| Stage | Shows | Source | Effort |
|---|---|---|---|
| 1 | Tuned stations: `COM1 118.100 TWR`, `EKCH TOWER`, `TX COM1  RX 1+2` | SimVars | small |
| 2 | Last ATC exchange when flying with Beyond ATC | BATC log file | small, fragile |
| 3 | Last stock-ATC message | in-sim toolbar addon via Coherent, pushed over a socket | large |

**Hard fact:** the stock MSFS ATC dialogue (2020 and 2024) is not exposed over SimConnect. No SimVar, no system
event. Stage 3 is the only way to get it, and it is a separate addon project.

## Stage 1 - tuned station (do this first)
SimVars (MSFS 2020 SU5+; not in Python-SimConnect's table, so `SimFeed` needs a way to add `(b'COM ACTIVE FREQ
IDENT:1', b'String', STRING256)` datums, see shared TODO):
- `COM ACTIVE FREQ IDENT:1/2` (string): station ident, e.g. `EKCH`
- `COM ACTIVE FREQ TYPE:1/2` (string): `TOWER`, `GROUND`, `ATIS`, `UNICOM`, `APPROACH`, ...
- `COM ACTIVE FREQUENCY:1/2`, `COM STANDBY FREQUENCY:1/2` (MHz), `COM STANDBY FREQ IDENT/TYPE:1/2`
- `COM TRANSMIT:1/2` (bool), `COM RECEIVE ALL` (bool), `COM STATUS:1` (enum), `COM VOLUME:1`
- `TRANSPONDER CODE:1` (BCD), `TRANSPONDER STATE:1` (enum: off/standby/test/on/alt/ground)

Layout (16 cols):
```
1 118.100 EKCH TW      <- active COM1, ident, type abbreviated (TW/GN/AT/AP/DP/CT/UN/MC)
S 121.500 GUARD        <- standby, with ident/type if known
TX1 RX12  SQK7000      <- transmit radio, receive radios, squawk (+ "STBY" if transponder not on)
```
Start/Stop toggles COM1/COM2 detail; Reset shows NAV1/NAV2 (ident and DME distance via `NAV IDENT:1`,
`NAV DME:1`) instead. A change of active frequency shows a 0.8 s banner `COM1 -> 118.100`.

## Stage 2 - Beyond ATC log
Only when the user runs Beyond ATC. BATC writes a text log under the user profile; the community
[AtcLogWatcher](https://github.com/fearlessfrog/AtcLogWatcher) tails it and calls this "a hack, not a supported
interface". Do the same: `x52_simconnect/comms_batc.py` tails the file, parses `[time] SPEAKER: text` lines, and pushes the last
N exchanges into the app. Word-wrap a message into 16-char lines and let Start/Stop and Reset page through it;
line 1 = who (`ATC` / `ME`), lines 2-3 = text, auto-advancing every 2.5 s with a `1/4` counter.
Config: `--batc-log PATH`, default the location AtcLogWatcher assumes. Must fail soft: no file, no stage 2.

## Stage 3 - stock ATC text via a toolbar addon
Separate project. Sketch so the effort is understood:
1. An MSFS in-game panel (HTML/JS in a community package) subscribes to the ATC menu/log the same way the built-in
   ATC panel does (Coherent calls; identify them by reading the stock panel's JS in the sim's `asobo-vcockpits-*`
   packages).
2. It cannot open sockets itself; the usual bridge is a local HTTP/WebSocket server the panel polls or connects to
   (in-game panels can `fetch` localhost), or a SimConnect client-data area written from a WASM module.
3. The bridge reads from that server. Same display as stage 2.
Risks: the Coherent interface is undocumented and changes with sim updates; in-game panels only run while the
toolbar panel is loaded. Decide after stages 1 and 2 whether it is worth it.

## Acceptance criteria
- [ ] Stage 1 shows correct ident/type for a tuned tower, ground and ATIS on a known airport; unknown frequency
      shows `----`.
- [ ] Transmit/receive indicators follow the audio panel; squawk follows the transponder.
- [ ] Frequency changes made in the cockpit produce the banner.
- [ ] Stage 2: with a sample BATC log file appended to during a test, the last message appears within 1 s and
      wraps correctly; no file means the page says `NO ATC FEED`.
- [ ] Offline tests for wrapping/paging and for the log parser.

## Steps
1. `SimFeed`: custom datums + STRING256 parsing (packet layout: 8 bytes per FLOAT64, 256 per STRING256, in
   definition order).
2. `CommsApp` stage 1, demo values.
3. Stage 2 tailer + parser + tests.
4. Stage 3 only as a separate spec if wanted.
