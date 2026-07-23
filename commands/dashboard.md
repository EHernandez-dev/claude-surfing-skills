---
name: dashboard
description: Build and open a spot's tabbed HTML surf Dashboard (Today / Forecast / Windows / Spot info); add `fast` for a ~20 s computed-call build
---

# Surf Dashboard

Build the single self-contained **Dashboard** for one spot: a tabbed HTML page (Today / Forecast / Windows / Spot info) that opens in the browser. This is the primary entry point for a full look at a spot. The Today, Forecast and Windows tabs are populated (Today: verdict, target-day conditions, and the tide chart with the aligned hourly strip clipped to daylight; Forecast: the interactive 7-day overview and per-day drilldown; Windows: the week's session windows in date order, each day expandable to its tide chart with the session shaded; Spot info: the spot's standing dossier, composed from the spot profile when one exists: works-on profile, peaks, hazards, buoy & water, access & logistics, nearby alternatives, webcams, community notes). For an unprofiled spot the Spot info tab renders its explicit absent states instead.

**This command is quiet.** On success, print nothing to chat except the opened file path (e.g. `Opened reports/2026-07-11-mundaka-dashboard.html`). Do NOT dump conditions, tables, or a verdict into the terminal: the Dashboard is where they are read. Only speak up when there is no HTML to open: a fetch failure or missing setup (see the failure path below).

If the user provided a spot name as an argument (e.g., `/surfing:dashboard Ocean Beach`), use that as the target spot. Otherwise, ask which spot.

**Two speeds.** If the argument list ends with the word `fast` (e.g., `/surfing:dashboard Sopelana fast`), run in **fast mode**: render the draft package exactly as `build_package.py` wrote it, no analysis pass (~20 s end to end for a profiled spot). Otherwise run in **normal mode** (default): take the same draft and edit only its judgment layer before rendering (Phase 2B). Both modes produce the same Dashboard file; fast trades analysis depth for speed and says so on the page.

## Phase 0: Surf Folder Check

Before any web lookup, check the working directory (the surf folder):

1. **Spot profile:** look for `spots/<slug>.yaml` matching the requested spot (slugified name; list `spots/` if unsure).
2. **Surfer profile:** note whether `surfer.yaml` exists.

**If a spot profile exists, skip Phase 1** (no re-research; the profile carries confirmed coordinates and facing) and fetch straight from it:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "{absolute path to spots/<slug>.yaml}" \
  --days 7 > {absolute path to payload.json}
```

Add `--surfer-file "{absolute path to surfer.yaml}"` when it exists. Paths must be absolute: the `cd` moves out of the surf folder. Redirect the payload to a JSON file (a temp path is fine); Phase 2 consumes the file, so there is no need to read it into context.

**If no spot profile exists**, run Phase 1 to resolve the spot, then fetch by coordinates. The Dashboard still builds; note in chat (this is a "missing setup" case worth speaking on) that the call is generic and a `/surfing:research {spot}` run would profile the spot and correct the verdicts.

## Phase 1: Spot Identification (only when no profile exists)

Same quick identification as `/surfing:conditions`:

1. WebSearch `"{spot_name} surf spot"` and/or geocode via `https://geocoding-api.open-meteo.com/v1/search?name={spot_name}&count=5` (WebFetch).
2. **Multiple matches:** use AskUserQuestion, showing region/country and a Google Maps link per option. Include "Other".
3. **Single match:** confirm with the user, including a Google Maps link.
4. Pin coordinates in the water, just outside the break.
5. Estimate `facing_deg` from the coastline orientation; without it, wind classification and surf windows are absent (state that assumption).

Then fetch:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --coordinates "{lat},{lon}" \
  --spot-name "{name}" \
  --facing {deg} \
  --days 7 > {absolute path to payload.json}
```

**If the fetch fails** (the script prints an `error` field, or nothing usable comes back): there is no HTML to open, so speak. Report the failure plainly and give manual check links (Waves `https://www.windy.com/-Waves-waves`, Buoys `https://www.ndbc.noaa.gov` / `https://portus.puertos.es`, Tides `https://www.tide-forecast.com`). Do not fabricate a Dashboard.

## Phase 2: Assemble the Data Package

The package is the same Phase 5A data package the research flow produces (see SKILL.md Step 5A). `build_package.py` drafts it deterministically in both modes; normal mode then edits the judgment layer.

### Phase 2A: Build the Draft Package (both modes)

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python build_package.py \
  --payload {abs path to payload.json} \
  --spot-file "{abs path to spots/<slug>.yaml}" \
  --output {abs path to package.json}
```

- Omit `--spot-file` when no profile exists (the draft then has no `spot_data` and its verdicts are rating-only).
- Add `--surfer-file "{abs path to surfer.yaml}"` when it exists (passed through as `surfer_profile`).
- Add `--target-day YYYY-MM-DD` when the user named a day, or when `surfer.yaml`'s `target_days` default selects one inside the forecast window (that default is your call to apply; the script never reads it). The script keys the analysis to it and keeps `conditions.report` consistent.

On success it echoes `{"package_path", "target_day", "verdict"}`. On `{"error", "note"}` (fetch payload unusable, unreadable inputs) there is no HTML to open, so speak: report the error per the failure path above.

The draft it writes is complete and render-ready: `conditions` (the payload verbatim), draft `analysis` (verdicts from quality-rating bands corrected against the machine-readable works-on fields, per ADR 0007; display-ready strings; windows clipped to daylight), and `spot_data` (the structured YAML fields mapped, the `notes` prose carried verbatim as `profile.description`, `community_notes: []`). Prose-derived cards (access, lifeguards, rentals, food, nearby spots, crowd, consistency) stay absent in a draft: the script does not parse prose.

**Fast mode: skip Phase 2B entirely.** Render the draft as-is. Its `one_liner` ends with the "Computed call, no analyst pass." tag; leave it in place, that is the page's honesty marker.

### Phase 2B: Edit the Judgment Layer (normal mode only)

Edit `package.json` in place; touch only the judgment layer, the mechanical fields are already right. Judge against the full works-on profile, including the prose fields the script cannot read (size range, tide window, wind preference):

- `analysis.target_day.one_liner`: **always rewrite it** (one sentence tying swell + wind + tide). Never leave the draft tag in a normal-mode dashboard.
- `analysis.week[]`: check each row's draft verdict against the works-on profile; override where judgment disagrees and rewrite `why` to say the real reason.
- `analysis.windows`: re-rank against the works-on profile (demote or drop out-of-window swell, shift times toward the ideal tide) and rewrite `why`. Keep the list best-first (the contract); the Windows tab sorts by date for display. Empty is fine when nothing stands out.
- `analysis.target_day.windows`: adjust `from`/`to`/`label` toward the ideal tide when the profile states one.
- `spot_data`: reshape the carried prose. From the `notes` in `profile.description`, when stated: `crowd`, `consistency`, `character_notes`, `access` (`{parking, parking_coordinates, transit, entry_exit, facilities, fees}`), `lifeguards` (`{covered, season_hours}`), `rentals` (`[{name, url, offers, price_estimate}]`), `food`, `nearby_spots` (`[{name, note, approx_coordinates}]`) (shapes in SKILL.md Step 3C), then rewrite `description` as a proper sentence or two. Leave `water_quality` out and `community_notes` as `[]` (the dashboard command does no community sweep; the tab renders its explicit checked-and-absent state).

Do not edit `conditions`: it is the fetch payload as `build_package.py` wrote it (with `report` kept consistent with the target day); the renderer derives the stable dashboard name `reports/{target-date}-{slug}-dashboard.html` from `report.filenames`.

## Phase 3: Render and Open

Run the renderer **from the surf folder** so the files land in its `reports/`. `cd` there explicitly: the Phase 0/1 fetch steps `cd` into the tools directory and a shell's working directory can persist between commands; a renderer run from the wrong directory silently writes a second `reports/` tree there (and the browser keeps showing the stale copy in the surf folder).

```bash
cd {absolute path to the surf folder} && \
uv run --project "${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools" python \
  "${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools/render_report.py" \
  --data {abs path to package.json} --mode dashboard
```

On exit 0 the script prints JSON either way:

- **Success:** `{"html_path": "reports/{date}-{slug}-dashboard.html", "md_path": "reports/{date}-{slug}-dashboard.md"}`. It writes the self-contained HTML Dashboard plus a paired flat Markdown twin, all four sections populated (Spot info from `spot_data`, falling back to explicit absent states without it). A re-run the same day overwrites both.
- **Soft failure:** `{"error": ..., "note": ...}`. There is no HTML to open, so speak: report the error and point the user at the Markdown twin / manual links.

Open the HTML on the **Today** tab (no fragment needed; Today is the default):

- macOS: `open {html_path}`
- Linux: `xdg-open {html_path}`

Then print only the opened path to chat, e.g. `Opened reports/2026-07-11-mundaka-dashboard.html`. Nothing else.
