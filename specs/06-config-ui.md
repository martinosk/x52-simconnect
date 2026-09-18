# Spec 06 - Config UI: choose the mode 3 events, build the mode 1 pages

**Depends on:** 01 (apps), 02 (rule table, key events). **Builds:** the template renderer and the config file of
spec 03, without its per-aircraft profiles. **Provides to 03:** `templates.py`, `config.py`, hot reload.

## Goal
Change what the MFD shows without editing Python, from a browser, while the bridge runs:
- Mode 1: add, remove, reorder and edit the data pages; each is a title and three 16-character lines.
- Mode 3: tick which kinds of event make a line in the log, and which unexplained key events to leave out.

The running bridge serves the page on `http://127.0.0.1:8052`. "Save and apply" writes the config file and the
stick shows the result within a tick, with a `CONFIG APPLIED` banner.

## Design
1. **Template language** (`templates.py`, pure; spec 03 item 2). `{VAR}`, `{VAR:format}`, `{VAR|filter}`,
   `{VAR|filter:arg}`; an index comes before the format (`{GENERAL_ENG_RPM:1:5.0f}`), so a bare number after
   the name is always an index. Integer formats (`d`, `x`) get an int, `%` scales, `{{` is a brace. Filters:
   `hdg`, `signed:N`, `freq[:decimals]`, `bcd`, `onoff:LABEL`, `either:ON,OFF`, `lat`, `lon`, `hms`.
   `check(template, known)` returns the problem or `None`: does not parse, unknown filter, format Python
   rejects, unknown SimVar, non-ASCII, or longer than 16 characters with every value at zero.
2. **Pages are templates** (`pages.py`). `DEFAULT_PAGES` replaces the five hand-written render functions;
   output compared byte for byte over 900 demo ticks, all-`None` and negative values before they were deleted.
3. **Config file** (`config.py`): TOML, `[[page]]` with `title` and `lines`, `[events]` with `hidden` (rule
   SimVars), `key_events`, `ignored_key_events`. Everything optional. Default location
   `%APPDATA%/x52-simconnect/config.toml`, `--config FILE` overrides. `from_dict` collects every problem with
   a path (`page.2.lines.1`) so the UI can put it next to the input. Written through a temporary file.
   TOML is read with `tomllib`; the writer is 15 lines because JSON strings are valid TOML strings.
4. **Hidden is not unwatched.** A hidden rule still runs: it explains its key events (hide the throttle and
   `THROTTLE_FULL` does not turn up as `EV` instead) and counts towards a burst. `RuleEngine.hidden`,
   `RuleEngine.fired`. Rules carry a `label` and a `group` for the UI.
5. **Server** (`config_server.py`, standard library). `ConfigStore` holds the current config, validates and
   saves submissions and hands the new config to the main loop (`take()`), which is the only thread that
   touches the apps. It also notices a hand-edited file (mtime, every 2 s); a broken file is reported in the
   UI and ignored, never applied. The main loop publishes `store.live` for the page to mirror.
   - `GET /` the page, `GET /api/state` config, defaults, rules, field catalogue, filters, limits.
   - `GET /api/live` MFD lines, mode, sim state, newest log lines, recent key events (polled at 2 Hz).
   - `POST /api/preview` renders the draft pages with live values (demo values without a sim) and returns
     text, problem and SimVar units per line. `POST /api/config` validates, saves, applies; 400 with `errors`.
   - Local only: binds 127.0.0.1, rejects a non-loopback `Host` (DNS rebinding), accepts JSON bodies only
     (no cross-site form posts). No authentication: anything running as the user can already edit the file.
6. **New SimVars on the fly.** `apps.feed_vars(config)` is `ALL_VARS` plus what the pages read. A data
   definition cannot grow, so `SimSource.set_names` replaces the feed and `ensure()` reconnects (~0.4 s).
   The UI validates names against Python-SimConnect's table (`sim_feed.simvar_units`, read without a
   connection) and shows each SimVar's unit under the line, which is how you notice `Radians`.
7. **Page** (`config_ui.html`, one file, no build step, no external resources). The blue 3x16 LCD is the
   centrepiece: a live mirror of the stick on the left, a small one per page that renders as you type. A
   field catalogue inserts ready-made fields (unit and width sorted out) at the cursor. Events are grouped
   checkboxes with an example of each line; key events seen in the sim recently can be left out with a click.
8. **`--no-stick`**: the whole loop against `mfd.NullMfd`, so the UI can be tried (and screenshotted) on a
   machine without an X52, and without fighting a running bridge for the USB device.

## Acceptance criteria
- [x] Pages edited in the browser show on the MFD after "Save and apply"; a page using a SimVar that was not
      streamed before gets live values (verified live with `--no-stick`: `AIRSPEED_MACH`, `TURB_ENG_N1:1`,
      feed went from 71 to 73 vars).
- [x] A line that does not parse, names an unknown SimVar or cannot fit is refused with the message next to it,
      and nothing is written.
- [x] Unticking a rule removes its lines from the log and does not produce `EV` lines in their place.
- [x] The config file round-trips, survives hand editing, and a broken file never blanks the display.
- [x] The built-in pages render exactly as before.
- [x] Offline tests: `test_templates.py`, `test_config.py`, `test_config_server.py` (real HTTP, port 0).
- [ ] Verified on the real stick (so far only with `--no-stick` against a live sim).

## Not in this spec
Per-aircraft profiles, `TITLE` matching and `extends` (spec 03, which now only needs those), editing the
button mapping, and mode 2.

## Open questions
- Should the UI open in the browser on start (`--open-ui`)? Not done: the bridge often starts unattended.
- Long `EV` names are still clipped at 12 characters; a rename table in the config would fix that.
