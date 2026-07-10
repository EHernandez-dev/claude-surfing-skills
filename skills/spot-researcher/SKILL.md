---
name: spot-researcher
description: Research surf spots worldwide and generate comprehensive surf spot reports, aggregating swell/wind/tide forecasts, live buoy observations, spot guides, session reports, hazards (rip currents, reef, localism), wetsuit recommendations, and access info from Surfline, Wannasurf, surf-forecast.com, NOAA, Puertos del Estado, and Open-Meteo. Use when planning a surf trip or session, or when asked for a surf forecast, spot guide, surf conditions, or wave report.
---

# Spot Researcher

Research surf spots worldwide and generate comprehensive spot reports combining data from multiple sources including Surfline, Wannasurf, surf-forecast.com, Open-Meteo marine forecasts, NOAA buoys and tide stations, and community session reports.

**Data Sources:** This skill aggregates information from specialized surf websites (Surfline, Wannasurf, surf-forecast.com, SurferToday) plus free marine data APIs (Open-Meteo Marine, buoy observations from a regional network registry - NOAA NDBC in the US, Puertos del Estado in Spain - and NOAA CO-OPS tides). Report quality depends on how well-documented the spot is. Famous breaks get rich reports; obscure ones fall back to the Information Gaps pattern. Tide predictions are automatic for US spots (NOAA) and, when the optional `WORLDTIDES_KEY` environment variable is set, for the rest of the world via WorldTides; otherwise non-US spots get a documented gap with manual lookup links.

## Surf Folder

The plugin reads and writes everything relative to the user's working directory (the "surf folder"), no hidden state elsewhere:

- `surfer.yaml` - the surfer profile: skill level, comfort zone, boards, home spots, unit preference, target-day defaults (example to copy: `assets/surfer-template.yaml`). When present, verdicts are made for this surfer, not a generic expert, and its `units` preference applies (precedence: `--units` flag, then surfer profile, then metric).
- `spots/<slug>.yaml` - one spot profile per researched spot (schema: `assets/spot-profile-template.yaml`): works-on profile, coordinates, facing, tide source, pinned buoy, webcams, hazard one-liners, `last_researched`. Every research run writes or updates it (Phase 7). Profiles never expire: always state the profile's age when using one, and suggest re-research past ~6 months.
- `reports/` - generated reports, named `{target-date}-{spot-slug}-{verdict}.md`.
- `sessions/` - the surfer's own session logs.

## When to Use This Skill

Use this skill when the user requests:

- Research on a specific surf spot
- A surf forecast or conditions check for a break
- Surf trip planning information
- A spot guide (how a wave works, hazards, skill level, access)

Examples:

- "Research Ocean Beach SF"
- "I'm going to Ericeira next week, what should I know about Ribeira d'Ilhas?"
- "Generate a spot report for Pipeline"

## Progress Checklist

Research Progress:

- [ ] Phase 1: Spot Identification (spot validated, coordinates obtained)
- [ ] Phase 2: Spot Orientation (facing direction determined)
- [ ] Phase 3: Data Gathering (parallel execution)
  - [ ] Phase 3a: Python conditions fetch (swell, wind, tides, buoy, water temp, daylight)
  - [ ] Phase 3b: Researcher agents (3 in parallel - spot guides + community reports)
  - [ ] Phase 3c: Results aggregated
  - [ ] Phase 3d: Access/water quality (inline WebSearch)
- [ ] Phase 4: Spot Analysis (works-on profile, forecast match, hazards)
- [ ] Phase 5: Report Generation (Report Writer agent)
- [ ] Phase 6: Report Review & Validation (Report Reviewer agent)
- [ ] Phase 7: Spot Profile Update (spots/<slug>.yaml written or updated)
- [ ] Phase 8: Completion (user notified, next steps provided)

## Orchestration Workflow

### Phase 1: Spot Identification

**Goal:** Identify and validate the specific surf spot, and obtain coordinates.

0. **Read the surf folder first:**
   - If `surfer.yaml` exists in the working directory, read it: it personalizes the Phase 4 verdicts and the report, and supplies the units preference (pass `--surfer-file` in Phase 3A).
   - If `spots/<slug>.yaml` exists for the requested spot (check `spots/` for a slugified match), read it: coordinates and facing come from the profile (skip the triangulation below and Phase 2), tell the user the profile's age, and treat this run as a re-research that will update the profile in Phase 7.

1. **Extract Spot Name** from user message
   - Look for spot names, beach names, or region + break combos
   - Common patterns: "Ocean Beach", "Pipeline", "Mundaka", "Steamer Lane"

2. **Resolve the spot** (no single canonical database exists - triangulate):

   - WebSearch: `"{spot_name} surf spot"` and `"{spot_name} site:wannasurf.com"`
   - Optionally geocode: `https://geocoding-api.open-meteo.com/v1/search?name={spot_name}&count=5` (WebFetch; returns lat/lon candidates)
   - Cross-check against Surfline (`"{spot_name} site:surfline.com"`) and Wikipedia for famous breaks

3. **Handle Ambiguity:**
   - Spot names collide constantly ("Ocean Beach" exists in SF and San Diego; there are dozens of "The Point"s). If **multiple plausible spots** match: Use AskUserQuestion to present options
     - For each option show: spot name, region/country, and a Google Maps link
     - Format: "[Spot Name] ([Region, Country]) - [Google Maps URL]"
     - Provide "Other" option if none match
   - If **single match**: Confirm with user, including a Google Maps link so they can verify
   - If **no matches**: Try variations (local name vs anglicized, nearby town name, "beach" vs "point"), then ask the user for a region or coordinates

4. **Pin the coordinates:**
   - Coordinates must be **in the water, just outside the break** - not the town center. A geocoder returns the town; nudge the point offshore toward the lineup using the map.
   - Store `latitude`, `longitude` in decimal degrees.

### Phase 2: Spot Orientation

**Goal:** Determine the direction the spot faces looking out to sea (`facing_deg`), which drives wind classification (offshore/onshore/cross-shore) and surf window ratings.

1. **From spot guides:** Wannasurf and surf-forecast.com usually state the ideal wind direction. The facing direction is roughly opposite the ideal (offshore) wind. Example: guide says "best wind E" for a west-facing beach -> `facing = 270`.
2. **From geography:** Check the coastline orientation on a map. A beach on the west coast of a landmass typically faces W (270); adjust for local coastline angle.
3. **Sanity check:** The dominant swell direction that works at the spot should be within ~90 degrees of the facing direction.

If facing direction cannot be determined, run the conditions script without `--facing` and note in Information Gaps that wind classification and surf windows were skipped.

**Once coordinates (and ideally facing) are obtained, immediately proceed to Phase 3.**

### Phase 3: Data Gathering

**Goal:** Gather comprehensive spot information from all available sources.

**Execution Strategy:** Run Python script for deterministic API data + dispatch specialized agents in parallel for web research. This hybrid approach minimizes token usage while maximizing parallelism.

#### Step 3A: Fetch Conditions Data (Python Script)

Run the conditions fetcher script to gather all API-based data:

```bash
cd "{repo_root}/skills/spot-researcher/tools"
uv run python fetch_conditions.py \
  --coordinates "{latitude},{longitude}" \
  --spot-name "{spot_name}" \
  --facing {facing_deg} \
  --days 7
```

Optional args: `--spot-file {path/to/spots/slug.yaml}` loads coordinates, name, facing, tide station, and pinned buoy from an existing spot profile (pass it instead of `--coordinates`/`--spot-name`/`--facing` when a profile exists; use the absolute path, the surf folder is the user's working directory, not the tools directory); `--surfer-file {path/to/surfer.yaml}` applies the surfer profile's units preference; `--units metric|imperial` (precedence: flag, then surfer profile, then metric; metric = heights m, wind km/h, temps °C; imperial = ft, kn, °F); `--target-day YYYY-MM-DD` keys the report filename to the day the user intends to surf (when the user names no day and `surfer.yaml` sets `target_days`, pass the next date matching one of them; otherwise it defaults to the forecast window's first day); `--tide-station {noaa_id}` overrides nearest-station lookup when the spot has a known better station; `--days` 1-7 (default 7).

Optional environment: `WORLDTIDES_KEY` enables station-grade tide extremes from WorldTides for spots outside NOAA coverage (heights on chart datum). Without it, non-US spots report a tide gap.

All JSON keys are unit-neutral; read the actual units from the payload's `units` object and label every quantity in the report with them.

This returns JSON with:

- **spot**: echo of inputs + `facing_compass` + `timezone`; when `--spot-file` was passed, also `profile` (`path`, `last_researched`, `age_days`, `reresearch_suggested` - true past ~6 months; profiles never expire, so this only prompts a suggestion to re-research)
- **units**: the units in effect - `system` ("metric"/"imperial") plus display labels `wave_height`, `tide_height`, `wind_speed`, `temperature`
- **report**: report naming inputs - `directory` ("reports"), `target_date` (the target day, falling back to the forecast window's first day - never the run date; null when neither is known), `spot_slug`, and `filenames` (the exact report path per verdict slug, e.g. `{"go": "reports/2026-07-11-mundaka-go.md", "check": ..., "skip": ...}`)
- **marine**: per-day forecast. Each day has `summary` (`wave_height_max`, `swell_height_max`, `swell_period_max_s`, `swell_direction_dominant`) and `blocks[]` (3-hourly, 05:00-21:00 local): `wave_height`, `swell_height`, `swell_period_s`, `swell_direction`(+`_deg`), `wind_wave_height`, `wind_speed`, `wind_gust`, `wind_direction`, `wind_type` (offshore/onshore/cross-shore/light; requires `--facing`), and `quality` (`score` 0-10 + `rating` flat/poor/fair/good/epic; requires `--facing`)
- **buoy**: nearest buoy real observation from the regional network registry (NOAA NDBC in the US, Puertos del Estado on Spanish coasts) - `station` (id, name, distance_km, url), `observed_at` (UTC), `wave_height`, `dominant_period_s`, `mean_wave_direction`, `wind_speed`, `wind_direction`, `water_temp`. Coastal stations may report wave height/period only (null direction/wind/temp). This is **observed ground truth** - cross-check the model forecast against it and flag disagreement
- **tides**: high/low predictions from a source ladder - `source` ("NOAA CO-OPS" where a station is within range, else "WorldTides" when the `WORLDTIDES_KEY` environment variable is set), `datum` ("MLLW" for NOAA, "CD" chart datum for WorldTides - both match published tide tables), `days[]` with high/low `events[]` (`time`, `height`, `type`), and `station` (NOAA: id, name, distance_km, url; WorldTides: name + url when a named station backs the prediction, absent for atlas points). WorldTides responses also carry a `copyright` string. With no nearby NOAA station and no key, returns an `error` + `note` (tide-forecast.com fallback / set `WORLDTIDES_KEY`). The key is read from the environment only and never appears in the payload
- **sea_temperature**: `current`, `source` ("buoy observation" preferred over "model SST" when both exist), `model`, `buoy`, and a deterministic `wetsuit` recommendation
- **daylight**: per-day `first_light`, `sunrise`, `sunset`, `last_light`, `daylight_hours` (dawn patrol planning)
- **weather**: per-day air conditions - `conditions`, `icon`, `temp_max`/`temp_min`, `precip_probability_pct`, `uv_index_max`
- **surf_windows**: best-rated surfable-light block per day (`date`, `best_time`, `rating`, `score`, swell + wind summary) - only present when `--facing` was provided; `best_time` is clamped to first light so it never lands in the dark
- **gaps**: any API failures or skipped computations

**Important caveat on `quality` ratings:** the script's heuristic is spot-agnostic (period + size + wind). It does NOT know the spot's swell window, ideal tide, or size ceiling. Phase 4 must adjust these ratings using the works-on profile from research (e.g., a 3 m WNW swell rates "good" generically but closes out a beach break that maxes at 2 m).

**Run this in parallel with Step 3B** - include the Bash command and all 3 Task calls in the same response turn to maximize parallelism.

#### Step 3B: Dispatch Researcher Agents (Parallel)

Dispatch 3 Researcher agents in a single message (all Task calls together).

**Agent 1: Surfline + Wannasurf (spot guide)**

```
Task(
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""You are a surf researcher gathering spot data for {spot_name} ({latitude},{longitude}).

## Your Assignment
Research from these sources: Surfline, Wannasurf

## Surfline Research
1. Search: "{spot_name} site:surfline.com"
2. Use WebFetch on the spot guide page to extract: break type, ability level,
   best season, ideal swell direction/size, ideal wind, ideal tide, crowd factor
3. Surfline is JS-heavy; if WebFetch returns a shell page, use the fetching ladder:

   ```bash
   # Fast path (httpx with browser-like headers, no browser)
   uv run python {repo_root}/skills/spot-researcher/tools/cloudscrape.py "{url}"

   # If the above returns {"error": ...} or content is JS-rendered:
   uv run python {repo_root}/skills/spot-researcher/tools/cloudscrape.py --render "{url}"
   ```

4. Note: Surfline's forecast numbers are paywalled - extract the free spot GUIDE
   content (how the wave works), not the forecast. Record the spot URL and cam URL if one exists.

## Wannasurf Research
1. Search: "{spot_name} site:wannasurf.com"
2. WebFetch the spot page and extract the structured fields: break type
   (beach/point/reef/rivermouth), bottom (sand/rock/coral), direction (L/R),
   ideal swell direction, ideal wind, ideal tide position and movement,
   swell size range it works in, ability level, crowd, access notes, hazards

## Output Format (return EXACTLY this JSON)

```json
{
  "sources": ["Surfline", "Wannasurf"],
  "spot_profile": {
    "break_type": "...", "bottom": "...", "wave_direction": "left|right|both",
    "ideal_swell_direction": "...", "ideal_swell_size": "... (state the unit)", "ideal_period_s": "...",
    "ideal_wind": "...", "ideal_tide": "...", "best_season": "...",
    "ability_level": "...", "crowd": "...", "consistency": "..."
  },
  "hazards": ["rips", "rocks at low tide", "..."],
  "urls": {"surfline": "...", "surfline_cam": "...", "wannasurf": "..."},
  "gaps": ["what couldn't be fetched and why"]
}
```"""
)
```

**Agent 2: surf-forecast.com + SurferToday + Wikipedia (spot personality)**

```
Task(
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""You are a surf researcher gathering spot data for {spot_name} ({latitude},{longitude}).

## Your Assignment
Research from these sources: surf-forecast.com, SurferToday, Wikipedia, MagicSeaweed archives via Surfline

## surf-forecast.com Research
1. Search: "{spot_name} site:surf-forecast.com"
2. WebFetch the spot page and extract: spot description, break type, reliability/consistency,
   ideal conditions statement ("works best with {swell dir} swell and {wind dir} wind"),
   best tide position, hazards, nearby alternative spots
3. If WebFetch fails, use the fetching ladder (cloudscrape.py, then --render), as in:

   ```bash
   uv run python {repo_root}/skills/spot-researcher/tools/cloudscrape.py "{url}"
   uv run python {repo_root}/skills/spot-researcher/tools/cloudscrape.py --render "{url}"
   ```

## SurferToday / Wikipedia Research
1. Search: "{spot_name} surfing" and "{spot_name} site:surfertoday.com"
2. Extract: spot history/reputation, notable characteristics, competitions held there,
   documented incidents (useful hazard signal), best season

## Note
MagicSeaweed shut down in 2023 (absorbed into Surfline) - do not cite it as live. Archived
MSW guide text sometimes surfaces in search results and is fine as background.

## Output Format (return EXACTLY this JSON)

```json
{
  "sources": ["surf-forecast.com", "SurferToday", "Wikipedia"],
  "spot_profile": {
    "description": "...", "break_type": "...", "consistency": "...",
    "ideal_swell_direction": "...", "ideal_wind": "...", "ideal_tide": "...",
    "best_season": "...", "ability_level": "..."
  },
  "character_notes": "reputation, wave character, notable facts",
  "hazards": ["..."],
  "nearby_spots": [{"name": "...", "note": "backup option when..."}],
  "urls": {"surf_forecast": "...", "surfertoday": "...", "wikipedia": "..."},
  "gaps": ["what couldn't be fetched and why"]
}
```"""
)
```

**Agent 3: Community reports + access (Reddit, forums, webcams)**

```
Task(
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""You are a surf researcher gathering recent first-hand information for {spot_name} ({latitude},{longitude}).

## Your Assignment
Find recent session reports, local knowledge, and practical access info.

## Community Reports
1. Search: "{spot_name} reddit surf" and "site:reddit.com {spot_name} surf"
   (r/surfing plus regional subs like r/SanDiego, r/bayarea, r/Portugal_Surf)
2. Search: "{spot_name} surf report {current_month} {current_year}"
3. Extract from each useful post: approximate date, what conditions were like,
   crowd notes, hazards encountered, localism warnings, board choice
4. Prioritize posts from the last 12 months. Note the date on everything.

## Localism & Etiquette
1. Search: "{spot_name} localism" and "{spot_name} surf etiquette locals"
2. Report honestly: heavy localism is a real safety/planning factor. Distinguish
   documented incidents from vague reputation.

## Access & Practical
1. Search: "{spot_name} parking surf access" and "{spot_name} webcam"
2. Extract: parking situation (lot/street/fees), paddle-out entry and exit points,
   walking distance, facilities (showers/toilets), free webcam URLs
   (Surfline cams are paywalled - prefer free cams: city/harbor/hotel cams, windy.com webcams)

## Output Format (return EXACTLY this JSON)

```json
{
  "sources": ["Reddit", "forums", "webcams"],
  "session_reports": [
    {"date": "...", "source_url": "...", "summary": "...", "conditions": "...", "crowd": "...", "hazards": "..."}
  ],
  "localism": {"level": "none|mild|moderate|heavy|unknown", "notes": "...", "evidence": "..."},
  "access": {"parking": "...", "entry_exit": "...", "facilities": "...", "fees": "..."},
  "webcams": [{"name": "...", "url": "...", "free": true}],
  "gaps": ["what couldn't be found"]
}
```"""
)
```

**Execute all 3 agents in parallel by including all Task calls in a single response.**

#### Step 3C: Aggregate Results

After the Python script and all agents return, aggregate into a unified data structure:

```json
{
  "conditions": { /* from fetch_conditions.py */ },
  "spot_data": {
    "profile": { /* merged spot_profile from Agents 1+2; note conflicts */ },
    "hazards": [ /* merged */ ],
    "session_reports": [ /* from Agent 3 */ ],
    "localism": { /* from Agent 3 */ },
    "access": { /* from Agent 3 */ },
    "webcams": [ /* from Agent 3 */ ],
    "nearby_spots": [ /* from Agent 2 */ ],
    "urls": { /* merged */ }
  },
  "gaps": [ /* merged gaps from all sources */ ]
}
```

**Partial Failure Handling:**

- If any agent fails entirely, proceed with data from successful agents
- Note failed sources in the gaps array
- Minimum viable: conditions data + at least one spot guide source

#### Step 3D: Water Quality & Advisories (Inline)

Water quality is the surf analog of road closures - actively check it, don't punt.

```
WebSearch: "{spot_name} water quality advisory {current_year}"
WebSearch: "{beach/county} beach water quality" (US: county health dept; CA: beachreportcard.org; EU: bathing water quality portal)
```

- If the region has a rain-runoff rule of thumb (e.g., Southern California's "72 hours after rain"), state it and check the precipitation forecast against it.
- Check for active advisories (sewage, algal blooms, closures). Synthesize a dated statement with a source link.
- If nothing found, say so explicitly rather than implying clean water.

### Phase 4: Spot Analysis

**Goal:** Build the spot's works-on profile and match the actual forecast against it.

#### Step 4A: Consolidate the Works-On Profile

Merge Agent 1 + Agent 2 spot profiles into one:

- **Break type** (beach/point/reef/rivermouth) and bottom (sand/rock/coral)
- **Swell window:** direction range, minimum period, size range it works in and its ceiling
- **Ideal wind:** direction (should be consistent with `facing_deg` - flag if not)
- **Ideal tide:** position (low/mid/high) and movement (incoming/outgoing)
- **Ability level** and consequence level
- **Best season** and consistency

Note conflicts between sources explicitly ("Wannasurf says all tides; surf-forecast says mid-to-high").

#### Step 4B: Match Forecast Against Profile

This is the core judgment step - the script's generic `quality` ratings must be corrected with spot knowledge:

1. For each forecast day, compare `swell_direction_deg` against the spot's swell window. Swell outside the window doesn't arrive at the break regardless of size - downgrade to flat/poor and say why.
2. Compare swell size against the spot's working range. Over the ceiling -> closed out / too heavy; under the minimum -> flat.
3. Cross-reference **tides**: overlay tide events on the daily surf windows. If the spot needs mid-incoming, shift each day's recommended session time toward the matching tide, even if the raw wind score peaked elsewhere.
4. Cross-check the **buoy observation** against today's model forecast. If the buoy shows 1.5 m at 18 s and the model says 0.6 m at 9 s, trust the buoy and note the discrepancy. Interpret the direction of the disagreement: a long-period reading inside the spot's swell window means MORE rideable energy than the model suggests (upside), not just uncertainty.
5. Produce a **"This Week's Outlook"**: per-day verdict (skip / worth a check / go) with the best session time and one-line reasoning that references tide + wind + swell together.
6. **Personalize for the surfer** (when `surfer.yaml` exists): verdicts are for THIS surfer, not a generic expert. Weigh their skill level and comfort zone (a small clean day is a Go for a beginner; a day past their comfort zone is a Skip for them even when the wave is world-class), name the board from their quiver that fits each Go / Worth-a-check day, and respect their scheduling constraints (target days, dawn-patrol willingness, notes).

#### Step 4C: Hazard Synthesis

Organize hazards by type with explicit, SEPARATE sub-sections - safety-critical, be comprehensive. Extract from spot guides AND session reports:

- **Rip currents:** location relative to the break (channel positions), how locals use them, escape guidance
- **Rocks / reef:** exposure by tide level ("inside section dries below +0.6 m"), entry/exit timing
- **Wave hazards:** hold-downs, shallow sections, closeouts, size at which character changes
- **Marine life:** documented (sharks, urchins, jellyfish, stingrays - include the stingray shuffle where relevant), not speculative
- **Crowds & localism:** from Agent 3, with evidence level
- **Water quality:** from Step 3D, dated
- **Skill-level match:** honest statement of who should surf this spot and at what size

#### Step 4D: Identify Information Gaps

Explicitly document what was **not found or unreliable:**

- No tide data (non-US spot, no `WORLDTIDES_KEY`) - link tide-forecast.com for the location
- Facing direction estimated rather than confirmed
- No recent session reports
- Conflicting ideal-tide claims between sources
- Buoy too far away to be representative

### Phase 5: Report Generation

**Goal:** Create the report by dispatching a Report Writer agent.

#### Step 5A: Prepare Data Package

Organize all gathered and analyzed data into structured JSON (conditions + spot_data + analysis + gaps, per Step 3C plus Phase 4 outputs). When `surfer.yaml` exists, include its contents as `surfer_profile` so the writer can render the "Bottom line for your day" block.

#### Step 5B: Dispatch Report Writer Agent

```
Task(
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""You are a Report Writer generating a surf spot report.

## Instructions

1. **Read the report template:**
   Use the Read tool to read: {repo_root}/skills/spot-researcher/assets/report-template.md

2. **Generate report following template structure exactly:**
   - Header with spot name, region, date
   - AI disclaimer (prominent ocean-safety warning)
   - Overview: break type, skill level, best season, works-on profile
   - This Week's Outlook: per-day verdict table with best session windows
   - Current Conditions: swell forecast, buoy observation, tides, wind, water temp/wetsuit, daylight
   - The Wave: how it breaks, sections, ideal conditions
   - Hazards: comprehensive, separate sub-sections
   - Access & Logistics: parking, entry/exit, webcams
   - Session Reports: dated community reports with links
   - Information Gaps: explicitly list missing data
   - Data Sources: links to all sources used

3. **Markdown Formatting Rules:**
   - ALWAYS add blank line before lists and after headers
   - Label every quantity with the unit labels from the data package's `units` object
     (JSON keys are unit-neutral); never mix unit systems within the report
   - Use `-` for bullets, `**text**` for bold (sparingly - only critical details)
   - Link specific attributions: any statement from a particular session report or
     guide MUST be a Markdown link [date/source](url), never plain text
   - Every named place (spot, parking, nearby break) gets a Google Maps link

4. **Save the report:**
   Use the Write tool to save into the `reports/` folder of the user's current
   working directory (create it if missing), named by the naming rule:
   reports/{target_date}-{spot_slug}-{verdict}.md
   - Pick the exact path from the data package's `report.filenames` object using the
     slug for the target day's verdict from the analysis: go (🟢 Go),
     check (🟡 Worth a check), or skip (🔴 Skip)
   - The date in the name is the target day (the day the user intends to surf, falling
     back to the forecast window's first day) - NEVER the run date
   - Example: reports/2026-07-11-mundaka-go.md

## Data Package

{data_package_json}

## Output Format (return EXACTLY this JSON)
```json
{
  "status": "SUCCESS",
  "file_path": "/absolute/path/to/reports/report.md",
  "filename": "reports/{target-date}-{spot-slug}-{go|check|skip}.md",
  "sections_generated": N
}
```"""
)
```

#### Step 5C: Capture Report File Path

Extract `file_path` from the agent's JSON response for Phase 6.

### Phase 6: Report Review & Validation

**Goal:** Validate report quality by dispatching a Report Reviewer agent.

#### Step 6A: Dispatch Report Reviewer Agent

```
Task(
  subagent_type="general-purpose",
  model="opus",
  prompt="""You are a Report Reviewer validating a surf spot report.

## Instructions

1. **Read the report:**
   Use the Read tool to read: {report_file_path}

2. **Perform systematic quality checks:**

   **Factual Consistency:**
   - Dates match their stated day-of-week
   - Tide times in the outlook match the tide table
   - Recommended session windows fall in daylight (check against sunrise/sunset)
   - Swell heights/periods consistent across all mentions
   - One unit system throughout, matching the conditions payload's `units` object - no mixed m/ft or °C/°F
   - Wind called offshore/onshore consistently with the spot's facing direction

   **Internal Logic:**
   - Per-day verdicts follow from the stated swell/wind/tide reasoning
   - Buoy vs model discrepancies are flagged, not silently averaged
   - Hazard warnings align with the break type (reef spots mention the reef; beach breaks mention rips)
   - Wetsuit recommendation matches the stated water temperature

   **Completeness:**
   - Report path is reports/{target-date}-{spot-slug}-{verdict}.md with verdict slug go/check/skip
     matching the target day's verdict in the Outlook (the date is the target day, not the run date)
   - No placeholder texts like {{spot_name}} or {{YYYY-MM-DD}}
   - All referenced links actually provided
   - Mandatory sections present: Overview, Outlook, Current Conditions, The Wave, Hazards, Information Gaps, Data Sources

   **Safety & Responsibility:**
   - AI disclaimer present and prominent
   - Rip current and skill-level guidance present
   - Water quality statement is dated with a source, or explicitly marked unknown
   - Users directed to verify conditions on-site before paddling out ("if in doubt, don't go out")

   **Links (verify INDEPENDENTLY):**
   - Buoy and tide station links point at the right station IDs
   - Every named place has a working map link
   - Session report attributions are hyperlinks, not plain text

3. **Fix issues:**
   - **Critical** (safety errors, factual errors, missing disclaimers): MUST fix using Edit tool
   - **Important** (completeness, consistency): SHOULD fix
   - **Minor** (formatting, polish): FIX if quick

## Output Format (return EXACTLY this JSON)
```json
{
  "status": "PASS" | "PASS_WITH_FIXES" | "FAIL",
  "issues_found": N,
  "fixes_applied": ["..."],
  "remaining_issues": ["..."],
  "report_path": "/absolute/path/to/report.md"
}
```"""
)
```

#### Step 6B: Process Validation Results

- **PASS or PASS_WITH_FIXES:** Proceed to Phase 7 with the `report_path`
- **FAIL:** Present `remaining_issues` to the user and ask for guidance

### Phase 7: Spot Profile Update

**Goal:** Persist what research learned, so future conditions checks skip re-research and correct verdicts to this break.

Write or update `spots/{spot_slug}.yaml` in the user's working directory (create `spots/` if missing; the slug is the same slugified name used for report filenames), following the schema in `{repo_root}/skills/spot-researcher/assets/spot-profile-template.yaml`:

- `name`, `region`, `coordinates`, `facing_deg` from Phases 1-2
- `tide_source` + `tide_station`: the source that served the tides (`tide_station` is the NOAA CO-OPS station id when NOAA served them, otherwise null)
- `buoy`: pinned from the conditions payload's `buoy.station` (`network` is the registry name, e.g. "NOAA NDBC" or "Puertos del Estado", plus `station_id`, `name`, `distance_km`); omit the block when no buoy was in range
- `works_on` from Step 4A (swell direction, size range, minimum period, wind, tide, season)
- `break` (type, bottom, direction, ability)
- `hazards` as one-liners from Step 4C
- `webcams` (free ones first)
- `notes`: anything a future quick check must know (tide windows, localism, seasonal character)
- `last_researched`: today's date

If the profile already exists, update it in place, preserving hand-edits that don't conflict with fresh findings; where they conflict, fresh research wins and the change is worth mentioning to the user.

`fetch_conditions.py --spot-file` reads exactly this schema (the pytest suite loads the template through the CLI); changing the schema means changing the template, the script, and this section together.

### Phase 8: Completion

Report to user:

1. **Success message:** "Spot research complete for {Spot Name}"
2. **File locations:** Full absolute path to the generated report, and the spot profile written/updated in Phase 7
3. **Summary:** 2-3 sentences - break type and skill level, this week's best window, key hazards or gaps
4. **Next steps:** Encourage the user to:
   - Check the free webcam (if found) before driving
   - Verify conditions on-site - forecasts miss local effects
   - Re-run `/surfing:conditions {spot}` the morning of for fresh numbers - now instant and spot-corrected via the saved profile
   - **Post-session log**: offer `skills/spot-researcher/assets/session-log-template.md` as a starting point for logging the session into `sessions/{date}-{spot_slug}.md` in the surf folder

**Example completion message:**

```
Spot research complete for Ocean Beach (SF)!

Report saved to: reports/2026-07-09-ocean-beach-sf-go.md
Spot profile saved to: spots/ocean-beach-sf.yaml (future conditions checks skip re-research)

Summary: Ocean Beach is a heavy, shifty beach break for advanced surfers - powerful rips,
cold water (14°C, 4/3 + booties), no channel. Best window this week is Thursday 08:00 on
the incoming mid tide with light E wind before the onshores fill in. Water quality clear;
main gaps: no recent session reports found.

Next steps: check the cam before driving out, and verify conditions from the beach -
OB changes block by block. If in doubt, don't paddle out.
```

## Error Handling Principles

### Script Failures

- **Don't block:** If the Python script fails, note in "Information Gaps" and continue
- **Provide alternatives:** Include manual check links (Windy waves layer, Surfline, tide-forecast.com, ndbc.noaa.gov, portus.puertos.es for Spain)
- **One retry:** Retry once on network timeouts, then continue

### Missing Data

- **Be explicit:** Always document what wasn't found
- **Be helpful:** Provide links for manual checking
- **Don't guess:** Never fabricate data to fill gaps - especially hazards and tides

### Search Failures

- **Try variations:** Local vs anglicized names, nearby town, "beach"/"point"/"reef" suffixes
- **Ask user:** If still not found, ask for a region or coordinates
- **Provide guidance:** Suggest checking Wannasurf's area browse pages

### WebFetch/WebSearch Issues

- **Fetching ladder:** WebFetch first -> `cloudscrape.py "{url}"` (fast httpx, no browser) -> `cloudscrape.py --render "{url}"` (Patchright stealth browser, for JS-rendered pages like Surfline)
- **Graceful degradation:** Missing one source shouldn't stop the research; cloudscrape.py exits 0 on failure
- **Document gaps:** Note which sources were unavailable
- **Prioritize safety:** If hazard info is unavailable, emphasize that in the gaps section

## Execution Timeouts

- **fetch_conditions.py:** 60s (five API round-trips; NOAA station list is large)
- **WebFetch/WebSearch:** default timeouts
- **Total skill execution:** Target 3-5 minutes, acceptable up to 10 for comprehensive research

## Quality Principles

Every generated report must:

1. ✅ **Include safety disclaimer** prominently at top
2. ✅ **Document all information gaps** explicitly
3. ✅ **Cite sources** with links
4. ✅ **Name by target day and verdict** - `reports/{target-date}-{spot-slug}-{verdict}.md`, never the run date
5. ✅ **Follow template structure** exactly
6. ✅ **Provide actionable information** (session windows keyed to tide + wind + daylight)
7. ✅ **Emphasize verification** - forecasts are a starting point, the ocean gets the final say

## Implementation Notes

### fetch_conditions.py Command Reference

```bash
cd skills/spot-researcher/tools
uv run python fetch_conditions.py \
  --coordinates "37.759,-122.513" \
  --spot-name "Ocean Beach" \
  --facing 265 \
  --days 7
```

**Options:**

- `--coordinates "lat,lon"` (required unless `--spot-file` provides them) - point in the water near the break
- `--spot-name` (required unless `--spot-file` provides it)
- `--spot-file PATH` (optional) - spot profile (`spots/<slug>.yaml`); supplies coordinates, name, facing, tide station, and pinned buoy. Explicit flags override. Pass an absolute path: the surf folder is the user's working directory, not the tools directory
- `--surfer-file PATH` (optional) - surfer profile (`surfer.yaml`); supplies the units preference
- `--facing N` (optional) - degrees true the spot faces out to sea; enables `wind_type`, `quality`, `surf_windows`
- `--days N` (optional, default 7, max 7)
- `--units metric|imperial` (optional) - output units; precedence: flag, then surfer profile, then metric
- `--target-day YYYY-MM-DD` (optional) - the day the user intends to surf; keys `report.target_date` (defaults to the forecast window's first day)
- `--tide-station ID` (optional) - NOAA CO-OPS station override
- `WORLDTIDES_KEY` (optional environment variable) - enables WorldTides tide extremes (chart datum) outside NOAA coverage

### Facing Direction Quick Reference

- West-facing (California outer coast): ~270. Offshore wind = E
- North Shore Oahu: ~315-360. Offshore = S/SE (trades are side-off)
- East coast US: ~90-135. Offshore = W
- The script treats wind within ±45° of facing as onshore, within ±45° of the reciprocal as offshore, the rest cross-shore; under ~11 km/h (6 kn) is "light" regardless

### Map Link Patterns

- Google Maps: `https://www.google.com/maps/search/?api=1&query={lat},{lon}` (coordinates) or `?api=1&query={URL-encoded place name}` (named places)
- Windy waves layer: `https://www.windy.com/-Waves-waves?waves,{lat},{lon},10`
- NDBC buoy: `https://www.ndbc.noaa.gov/station_page.php?station={id}`
- Puertos del Estado buoys (Spain): `https://portus.puertos.es/` (portal map; no per-station deep link)
- NOAA tides: `https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id={id}`
- tide-forecast.com (non-US fallback when `WORLDTIDES_KEY` is unset): `https://www.tide-forecast.com/locations/{slug}/tides/latest`
- WorldTides portal: `https://www.worldtides.info/`

---

**Skill Version:** 0.3.0 | **Last Updated:** 2026-07-10
