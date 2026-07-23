---
name: dashboard
description: Build and open a spot's tabbed HTML surf Dashboard (Today / Forecast / Windows / Spot info)
---

# Surf Dashboard

Build the single self-contained **Dashboard** for one spot: a tabbed HTML page (Today / Forecast / Windows / Spot info) that opens in the browser. This is the primary entry point for a full look at a spot. The Today, Forecast and Windows tabs are populated (Today: verdict, target-day conditions, and the tide chart with the aligned hourly strip clipped to daylight; Forecast: the interactive 7-day overview and per-day drilldown; Windows: the week's session windows in date order, each day expandable to its tide chart with the session shaded; Spot info: the spot's standing dossier, composed from the spot profile when one exists: works-on profile, peaks, hazards, buoy & water, access & logistics, nearby alternatives, webcams, community notes). For an unprofiled spot the Spot info tab renders its explicit absent states instead.

**This command is quiet.** On success, print nothing to chat except the opened file path (e.g. `Opened reports/2026-07-11-mundaka-dashboard.html`). Do NOT dump conditions, tables, or a verdict into the terminal: the Dashboard is where they are read. Only speak up when there is no HTML to open: a fetch failure or missing setup (see the failure path below).

If the user provided a spot name as an argument (e.g., `/surfing:dashboard Ocean Beach`), use that as the target spot. Otherwise, ask which spot.

## Phase 0: Surf Folder Check

Before any web lookup, check the working directory (the surf folder):

1. **Spot profile:** look for `spots/<slug>.yaml` matching the requested spot (slugified name; list `spots/` if unsure).
2. **Surfer profile:** note whether `surfer.yaml` exists.

**If a spot profile exists, skip Phase 1** (no re-research; the profile carries confirmed coordinates and facing) and fetch straight from it:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "{absolute path to spots/<slug>.yaml}" \
  --days 7
```

Add `--surfer-file "{absolute path to surfer.yaml}"` when it exists. Paths must be absolute: the `cd` moves out of the surf folder.

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
  --days 7
```

**If the fetch fails** (the script prints an `error` field, or nothing usable comes back): there is no HTML to open, so speak. Report the failure plainly and give manual check links (Waves `https://www.windy.com/-Waves-waves`, Buoys `https://www.ndbc.noaa.gov` / `https://portus.puertos.es`, Tides `https://www.tide-forecast.com`). Do not fabricate a Dashboard.

## Phase 2: Assemble the Data Package

Build the same Phase 5A data package the research flow produces (see SKILL.md Step 5A), from the fetch payload plus your analysis:

- `conditions`: the fetch payload verbatim, including a `report.filenames` object with `go`/`check`/`skip` entries under `reports/{target-date}-{slug}-{verdict}.md` (the renderer derives the stable dashboard name `reports/{target-date}-{slug}-dashboard.html` from it; the verdict slug itself is not used in the dashboard filename).
- `analysis.target_day`: `date`, `verdict` (`go`/`check`/`skip`, spot-corrected against the works-on profile when one exists), `one_liner`, `windows` (`[{from, to, label}]`).
- `analysis.week`: one entry per forecast day (`{date, verdict, swell, wind, why}`, display-ready strings with unit labels applied).
- `analysis.windows`: the ranked best session windows over the week, best first (`[{date, window: {from, to, label}, verdict, swell, wind, why}]`, display-ready strings). Keep the list ordered best-first (the contract); the Windows tab sorts by date for display. For a profiled spot, rank against the works-on profile (demote or drop out-of-window swell, shift times toward the ideal tide) and say why in `why`. Omit or leave empty when nothing stands out; the Windows tab then shows a "no standout windows" state.
- `spot_data`: the Spot info tab's content (same shape as SKILL.md Step 3C). **When a spot profile exists, build it from `spots/<slug>.yaml`; do not omit it**, or the tab shows "no researched works-on profile yet" next to the profile-freshness line the fetch payload carries. Map the YAML like so:
  - `spot_data.profile`: the works-on grid. From `works_on`: `ideal_swell_direction`, `ideal_swell_size`, `ideal_period_s` (skip when null), `ideal_wind`, `ideal_tide`, `best_season`. From `break`: `break_type`, `bottom`, `wave_direction`, `ability_level`. From the `notes` prose, when stated: `crowd`, `consistency`, plus `description` / `character_notes` (a sentence or two each). Include only fields the profile actually states.
  - `spot_data.peaks`, `spot_data.hazards`, `spot_data.webcams`: the YAML lists verbatim.
  - From the `notes` prose, when stated: `access` (`{parking, parking_coordinates, transit, entry_exit, facilities, fees}`), `lifeguards` (`{covered, season_hours}`), `rentals` (`[{name, url, offers, price_estimate}]`), `food`, `nearby_spots` (`[{name, note, approx_coordinates}]`).
  - `spot_data.community_notes`: `[]` (the dashboard command does no community sweep; the tab renders its explicit checked-and-absent state). Leave `water_quality` out for the same reason.
  - No spot profile: omit `spot_data` entirely; every card renders as absent.

Write the package to a JSON file (a temp path is fine).

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
