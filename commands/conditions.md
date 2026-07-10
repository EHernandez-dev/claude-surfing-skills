---
name: conditions
description: Quick surf conditions check for a spot (swell, wind, tides, buoy, water temp, daylight)
---

# Quick Conditions Check

Fetch current swell, wind, tide, buoy, water temperature, and daylight conditions for a surf spot. Much faster than a full spot research - no web scraping or agent dispatch needed.

If the user provided a spot name as an argument (e.g., `/surfing:conditions Ocean Beach`), use that as the target spot. Otherwise, ask which spot to check.

## Phase 0: Surf Folder Check

Before any web lookup, check the working directory (the surf folder):

1. **Spot profile:** look for `spots/<slug>.yaml` matching the requested spot (slugified name; list `spots/` if unsure).
2. **Surfer profile:** note whether `surfer.yaml` exists.

**If a spot profile exists, skip Phases 1-2 entirely** (no re-research, no WebSearch, no geocoding) and fetch straight from it:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "{absolute path to spots/<slug>.yaml}" \
  --days 7
```

Add `--surfer-file "{absolute path to surfer.yaml}"` when it exists. Paths must be absolute: the `cd` moves out of the surf folder.

Then present results per Phase 4, with three profile-specific additions:

- **Always state the profile's age first**, from the payload's `spot.profile` (e.g. "Using spot profile `spots/mundaka.yaml`, researched 2026-07-08, 2 days old"). If `reresearch_suggested` is true (older than ~6 months), suggest re-running `/surfing:research {spot}`; the profile never expires, so still use it. If `last_researched` is null (hand-created profile), say the profile's age is unknown and suggest a research run to fill it in.
- **Correct verdicts to the works-on profile:** judge each day against the profile's `works_on` (swell direction window, size range, minimum period, wind, tide), not the generic quality score alone. A "good" score from outside the swell window or under the minimum period is a Skip, and the reasoning must say why. Mention relevant `hazards`/`notes` one-liners when they affect the call.
- **Personalize when `surfer.yaml` exists:** end with a bottom line for this surfer - verdict weighed against their skill level and comfort zone, and the board from their quiver that fits (a small clean day can be a Go for a beginner; a day past their comfort zone is a Skip for them).

**If no spot profile exists**, continue with Phase 1 below, and at the end suggest `/surfing:research {spot}` to create one so future checks are instant and spot-corrected.

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

Optional flags: `--surfer-file "{absolute path to surfer.yaml}"` applies the surfer profile's units preference; `--units metric|imperial` (precedence: flag, then surfer profile, then metric; pass `imperial` if the user asked for it); `--tide-station {noaa_id}` overrides the nearest-station lookup when the spot has a known better station; `--days` accepts 1-7 (default 7, use a smaller number for a shorter look-ahead).

**If the script fails:** note the failure and provide manual check links:

- Waves: `https://www.windy.com/-Waves-waves`
- Buoys: `https://www.ndbc.noaa.gov` (US) or `https://portus.puertos.es` (Spain)
- Tides: `https://www.tide-forecast.com`

## Phase 4: Present Results

Keep the output concise and scannable, using tables. Include:

1. **Spot summary:** name, coordinates, facing direction (or note it was skipped)
2. **Current buoy observation:** station name/distance, observed wave height, period, direction, water temp; flag disagreement if it conflicts with the model forecast for today
3. **Swell forecast table:** one row per day - date, max wave/swell height, max period (s), dominant swell direction; label quantities with the payload's `units` object
4. **Wind:** on/off/cross-shore classification per day (only if `--facing` was provided), speed, gusts
5. **Tide table:** high/low events per day, or - if `tides.error` is present - a non-US gap note with a link to `https://www.tide-forecast.com`
6. **Water temp + wetsuit line:** from `sea_temperature`
7. **Daylight:** first light, sunrise, sunset, last light per day (dawn patrol planning)
8. **Surf windows table** (if present): best-rated session time per day with rating, swell, and wind
9. **Weather/UV:** only mention if notable (rain likely, high UV, extreme temps)
10. **Gaps:** list anything from `gaps[]` explicitly

Closing reminder depends on the surf folder:

- **With a spot profile:** verdicts above are already spot-corrected; remind the user to verify from the beach, and (only if `reresearch_suggested` was true) that the profile is over ~6 months old and worth refreshing with `/surfing:research {spot}`.
- **Without one:** this was a quick, spot-agnostic check - for hazards, spot character, and a spot-corrected verdict (plus a saved profile that makes future checks instant), run `/surfing:research {spot}`.
