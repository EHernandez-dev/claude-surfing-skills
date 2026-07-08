---
name: conditions
description: Quick surf conditions check for a spot (swell, wind, tides, buoy, water temp, daylight)
---

# Quick Conditions Check

Fetch current swell, wind, tide, buoy, water temperature, and daylight conditions for a surf spot. Much faster than a full spot research - no web scraping or agent dispatch needed.

If the user provided a spot name as an argument (e.g., `/surfing:conditions Ocean Beach`), use that as the target spot. Otherwise, ask which spot to check.

## Phase 1: Spot Identification

1. **Resolve the spot to coordinates:**

   - WebSearch: `"{spot_name} surf spot"` for a quick sanity check on the location
   - Geocode: `https://geocoding-api.open-meteo.com/v1/search?name={spot_name}&count=5` (WebFetch; returns lat/lon candidates)

2. **Handle results:**

   - **Multiple matches:** Use AskUserQuestion to present options. For each, show region/country and a Google Maps link: "[Spot Name] ([Region, Country]) - [Google Maps URL]". Include an "Other" option.
   - **Single match:** Confirm with user, including a Google Maps link so they can verify.
   - **No matches:** Try variations (local name vs anglicized, nearby town name, "beach"/"point"/"reef"), then ask the user for a region or coordinates.

3. **Pin the coordinates:** they must be in the water, just outside the break, not the town center. Nudge the geocoded point offshore toward the lineup if needed.

## Phase 2: Facing Direction

Quickly estimate the direction the spot faces looking out to sea (`facing_deg`):

- From known spot knowledge or the coastline orientation visible on a map
- From a spot guide's stated ideal wind direction (facing is roughly opposite the offshore wind)

If uncertain, skip `--facing` and note in the results that wind classification (onshore/offshore/cross-shore) and surf windows will be absent.

## Phase 3: Fetch Conditions

Run the conditions fetcher script:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --coordinates "{lat},{lon}" \
  --spot-name "{name}" \
  --facing {deg} \
  --days 7
```

Optional flags: `--tide-station {noaa_id}` overrides the nearest-station lookup when the spot has a known better station; `--days` accepts 1-7 (default 7, use a smaller number for a shorter look-ahead).

**If the script fails:** note the failure and provide manual check links:

- Waves: `https://www.windy.com/-Waves-waves`
- Buoys: `https://www.ndbc.noaa.gov`
- Tides: `https://www.tide-forecast.com`

## Phase 4: Present Results

Keep the output concise and scannable, using tables. Include:

1. **Spot summary:** name, coordinates, facing direction (or note it was skipped)
2. **Current buoy observation:** station name/distance, observed wave height, period, direction, water temp; flag disagreement if it conflicts with the model forecast for today
3. **Swell forecast table:** one row per day - date, max wave/swell height (ft), max period (s), dominant swell direction
4. **Wind:** on/off/cross-shore classification per day (only if `--facing` was provided), speed, gusts
5. **Tide table:** high/low events per day, or - if `tides.error` is present - a non-US gap note with a link to `https://www.tide-forecast.com`
6. **Water temp + wetsuit line:** from `sea_temperature`
7. **Daylight:** first light, sunrise, sunset, last light per day (dawn patrol planning)
8. **Surf windows table** (if present): best-rated session time per day with rating, swell, and wind
9. **Weather/UV:** only mention if notable (rain likely, high UV, extreme temps)
10. **Gaps:** list anything from `gaps[]` explicitly

Remind the user this is a quick check: for hazards, spot character, and a spot-corrected verdict, run `/surfing:research {spot}`.
