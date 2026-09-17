# Spec 03 - Aircraft-aware pages from a config file

**Depends on:** nothing (fits inside `PagesApp` once 01 exists). **Provides:** the template renderer used by 04 and 02.

## Goal
Different aircraft want different pages: a jet needs N1, FL and Mach; the C152 needs RPM and gallons. Page sets
are chosen automatically from the loaded aircraft and are defined in a config file, not in Python.

## Design
1. **`pages.toml`** with profiles; the first profile whose `match` list has a case-insensitive substring of the
   aircraft `TITLE` wins; `default` is used otherwise.
   ```toml
   [[profile]]
   name = "default"
   [[profile.page]]
   title = "FLIGHT"
   lines = ["IAS {AIRSPEED_INDICATED:3.0f} GS {GROUND_VELOCITY:3.0f}",
            "ALT {INDICATED_ALTITUDE:5.0f} V{VERTICAL_SPEED|signed5}",
            "HDG {PLANE_HEADING_DEGREES_MAGNETIC|hdg}  TRK {GPS_GROUND_MAGNETIC_TRACK|hdg}"]

   [[profile]]
   name = "jet"
   match = ["A320", "737", "A310", "Longitude", "CJ4", "Vision Jet"]
   extends = "default"          # inherit default's pages, then add/replace by title
   [[profile.page]]
   title = "ENGINE"
   lines = ["N1 {TURB_ENG_N1:1:5.1f} {TURB_ENG_N1:2:5.1f}",
            "FF {TURB_ENG_FUEL_FLOW_PPH:1:5.0f} PPH",
            "FUEL {FUEL_TOTAL_QUANTITY_WEIGHT:6.0f} LB"]
   ```
2. **Template mini-language.** `{VAR}`, `{VAR:fmt}` (Python format spec), `{VAR|filter}`, `{VAR|filter:arg}`.
   Filters are the existing helpers: `hdg` (radians to 000), `deg`, `freq` (07.3f), `bcd` (transponder),
   `signedN`, `onoff:LABEL`, `latN`/`lonE`. Implement with a `string.Formatter` subclass: `get_value` looks up the
   feed dict through `_num`, `format_field` applies filters. Indexed vars keep their `:1` suffix, so parse
   `NAME(:index)?` before the format spec: `{TURB_ENG_N1:1:5.1f}` -> var `TURB_ENG_N1:1`, spec `5.1f`.
   Every rendered line is clipped to 16 chars; missing values render as 0 via `_num`.
3. **Aircraft title.** `SimFeed` is FLOAT64-only today. Two options; (a) is fine for v1:
   (a) poll `TITLE` with the one-shot API every 10 s (`AircraftRequests(sm).get("TITLE")`, ~100 ms, negligible), or
   (b) extend `SimFeed` with STRING256 datums (shared TODO; 05 needs it too).
   Also subscribe to the `AircraftLoaded` system event to switch profiles immediately (Python-SimConnect:
   `sm.dll.SubscribeToSystemEvent(sm.hSimConnect, evt_id, b"AircraftLoaded")`, handled in the hooked dispatch).
4. **Variable set.** `ALL_VARS` becomes the union of every variable in every profile plus `CLOCK_VARS`, built at
   load time. Unknown names fail at load with the offending profile/page/line. Definition size is not a concern
   below a few hundred doubles.
5. **Hot reload.** Check `pages.toml` mtime every 2 s; on change, reload, revalidate, and only swap in if valid.
   Show `PAGES RELOADED` / `PAGES INVALID` as a banner.
6. **Migration.** Move the five current pages into `pages.toml` as the `default` profile; delete the Python
   page functions once the rendered output matches byte for byte (write the comparison test first).

## Acceptance criteria
- [ ] `pages.toml` replaces the hard-coded pages; rendered output for the C152 is identical to today's.
- [ ] Loading a profile with an unknown SimVar, a bad format spec, or a line over 16 chars fails with file/line context.
- [ ] Switching aircraft in the sim switches the profile within 10 s (or instantly with `AircraftLoaded`).
- [ ] Hot reload works and never leaves the display blank on a broken file.
- [ ] Offline tests: template rendering for every filter; profile matching and `extends`; union of vars; all-None rendering.
- [ ] `--profile NAME` CLI override for testing a profile without owning the aircraft.

## Steps
1. Template renderer + tests (pure Python, no sim).
2. `pages.toml` loader/validator; port the five pages; byte-for-byte comparison test.
3. `TITLE` polling and profile selection; `--profile` override.
4. Hot reload, banners.
5. Optional: STRING256 support in `SimFeed`, `AircraftLoaded` event.

## Open questions
- MSFS 2024 `TITLE` strings for marketplace aircraft are not standardised; collect a few and keep `match` lists generous.
