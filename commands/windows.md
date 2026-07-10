---
name: windows
description: Find the best surf session windows for a spot over the next 7 days
---

# Best Session Windows

Answer the "when should I surf this week" question for a spot: a ranked list of the best session windows over the next 7 days.

If the user provided a spot name as an argument (e.g., `/surfing:windows Ocean Beach`), use that as the target spot. Otherwise, ask which spot to check.

## Phase 1: Spot Identification

Same quick identification as `/surfing:conditions`:

1. WebSearch: `"{spot_name} surf spot"` and/or geocode via `https://geocoding-api.open-meteo.com/v1/search?name={spot_name}&count=5` (WebFetch)
2. **Multiple matches:** use AskUserQuestion, showing region/country and a Google Maps link per option: "[Spot Name] ([Region, Country]) - [Google Maps URL]". Include "Other".
3. **Single match:** confirm with user, including a Google Maps link.
4. **No matches:** try variations (local vs anglicized name, nearby town, "beach"/"point"/"reef"), then ask for a region or coordinates.
5. Pin coordinates in the water, just outside the break.

**Facing direction is REQUIRED for this command** - surf windows and wind classification cannot be computed without it. Estimate `facing_deg` from the coastline orientation or known spot knowledge, and state the assumption explicitly in the output (e.g., "assuming this spot faces ~270 (W); offshore wind is E").

## Phase 2: Fetch Conditions

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --coordinates "{lat},{lon}" \
  --spot-name "{name}" \
  --facing {deg} \
  --days 7
```

**If the script fails:** note the failure and provide manual check links:

- Waves: `https://www.windy.com/-Waves-waves`
- Buoys: `https://www.ndbc.noaa.gov` (US) or `https://portus.puertos.es` (Spain)
- Tides: `https://www.tide-forecast.com`

## Phase 3: Analysis

Start from `surf_windows` (the best-rated daylight block per day), then adjust:

1. **Overlay tides:** for each day, check `tides.days[].events[]` against the window's `best_time`. Note whether the window falls near a tide event (e.g., "2h before high"). The generic score does not know this spot's ideal tide position, so flag this as a caveat rather than silently trusting the raw score.
2. **Overlay daylight:** confirm each window's `best_time` falls between `daylight` first light and last light; call out dawn-patrol-only windows.
3. **Cross-check buoy vs model for today:** compare `buoy` observed wave height/period against today's `marine` forecast. If they disagree meaningfully, flag it and note which one to trust for today's session.

## Phase 4: Output

Present a single ranked table of the **top 3-5 windows this week**, best first:

| Day/Date | Time | Rating | Swell (ht @ period, dir) | Wind | Tide context |
|---|---|---|---|---|---|

Then:

- **One-line recommendation** of the single best session this week (day, time, why).
- **Caveat:** these ratings are spot-agnostic (period + size + wind only) - they don't know this spot's swell window, ideal tide, or size ceiling. For a spot-corrected verdict that accounts for the actual break, run `/surfing:research {spot}`.
