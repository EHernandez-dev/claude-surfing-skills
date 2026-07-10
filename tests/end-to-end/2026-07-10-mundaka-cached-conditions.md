# E2E: Cached conditions check at Mundaka (2026-07-10)

Demonstrates the surf folder cached flow end to end (issue #5): a
`/surfing:conditions Mundaka` run against an existing spot profile, with a
surfer profile present.

**Setup (in `surf-folder/`):**

- `spots/mundaka.yaml` - spot profile as written by the 2026-07-08 research run (`2026-07-08-mundaka.md`)
- `surfer.yaml` - intermediate surfer, metric units, weekend target days

**What the cached flow did differently from the 2026-07-08 run:**

- No WebSearch, no geocoding, no researcher agents: coordinates, facing, tide source, and buoy came from the profile. One script call, ~5 s.
- The buoy observation came from the profile's pinned Puertos del Estado station 2136 directly (no station-list download), where the 2026-07-08 run had no buoy at all.
- Verdicts below are corrected to the profile's works-on data and weighed for the surfer in `surfer.yaml`.

**Command (single deterministic fetch, no re-research):**

```bash
cd skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/mundaka.yaml" \
  --surfer-file "$SURF_FOLDER/surfer.yaml" \
  --days 7
```

Payload highlights: `spot.profile = {last_researched: 2026-07-08, age_days: 2, reresearch_suggested: false}`; `units.system = metric` (from `surfer.yaml`, no `--units` flag passed); buoy station 2136 at 36 km; tides gapped (no `WORLDTIDES_KEY` in this environment).

---

## Conditions check output (as presented to the user)

# Mundaka - Quick Conditions Check (Fri 2026-07-10)

Using spot profile `spots/mundaka.yaml` (researched **2026-07-08, 2 days old** - fresh, no re-research needed). Verdicts are corrected to Mundaka's works-on profile and weighed for Elena (intermediate, comfortable to ~1.5 m clean).

**Spot:** Mundaka rivermouth left, 43.4095, -2.6995, faces N (350°). Offshore wind is S-SW. Works on: NW groundswell, 1.0-1.5 m minimum at 12 s+, low-to-mid tide.

## Right now (buoy observation)

Observed at **Boya de Bilbao-Vizcaya** (Puertos del Estado station 2136, 36 km away), as of 2026-07-10 16:00 UTC:

| Metric | Observed |
|--------|----------|
| Wave height | 0.7 m |
| Dominant period | 8.2 s |
| Direction | NW |
| Wind | 6 km/h NNW |
| Water temp | 24.7 °C |

The buoy confirms the model for today (0.7 m at ~7 s modeled vs 0.7 m at 8.2 s observed): small, short-period NW windswell. No meaningful disagreement.

## This week at Mundaka

| Date | Swell (max) | Generic score | Verdict (works-on corrected) |
|------|-------------|---------------|------------------------------|
| Fri Jul 10 | 0.7 m @ 7.2 s NW | fair 5.5 | 🔴 Skip - half the minimum size, 5 s under the minimum period |
| Sat Jul 11 | 0.6 m @ 9.0 s NW | fair 5.5 | 🔴 Skip - closest to the period window all week, still far under size |
| Sun Jul 12 | 0.7 m @ 8.0 s NNW | **good 6.4** | 🔴 Skip - the "good" is offshore-wind flattery: 0.5 m at 7.6 s cannot break on the sandbar (needs 1.0-1.5 m at 12 s+) |
| Mon Jul 13 | 0.7 m @ 6.8 s NW | poor 3.5 | 🔴 Skip |
| Tue Jul 14 | 0.4 m @ 5.5 s NNW | poor 3.5 | 🔴 Skip |
| Wed Jul 15 | 0.3 m @ 3.5 s NNW | flat 0 | 🔴 Skip - plus thunderstorm/hail risk |
| Thu Jul 16 | 0.3 m @ 3.6 s NNW | poor 3.5 | 🔴 Skip |

Sunday is the week's teaching example: the spot-agnostic quality score rates 14:00 "good" (12 km/h S offshore, glassy-groomed), but the works-on profile says the wave does not exist at this size/period. Without the profile, this check would have recommended driving out to a flat rivermouth.

## Tides

**No automated tide data.** Mundaka is outside NOAA coverage and no `WORLDTIDES_KEY` is set in this environment; the script degraded to the documented gap (`tides.error` + manual fallback note). Check [tide-forecast.com](https://www.tide-forecast.com/locations/Mundaka/tides/latest) manually. Irrelevant to this week's verdicts: there is no wave to time a tide window for.

## Water, light, weather

- **Water 24.7 °C** (buoy observation; model says 24.9 °C). Recommended: **boardshorts / rash guard**.
- **Daylight:** first light 06:04, sunrise 06:40, sunset 21:51, last light 22:27 (15.2 h).
- **Weather:** overcast into the weekend, hot Sunday (34 °C), thunderstorm with hail possible Wednesday Jul 15.

## Bottom line for your day

Elena: nothing at Mundaka this weekend - your target days (Sat/Sun) are 🔴 Skips at the rivermouth, and that would be true for any surfer, not just at your level. If you want wet anyway, Sunday 11:00-14:00 has warm water, light-to-offshore wind, and knee-high NW rollers: take the **fish (5'10", "0.5-1 m, weak or mushy")** to the beginner-friendly estuary beaches from the profile notes ([Playa de Laida](https://www.google.com/maps/search/?api=1&query=Playa+de+Laida) or [Playa de Laga](https://www.google.com/maps/search/?api=1&query=Playa+de+Laga)), and treat it as a small-wave practice session, not a Mundaka day. Check the free [surf-forecast.com cam](https://www.surf-forecast.com/breaks/Mundaka/webcams/latest) before driving.

## Gaps

- `tides: Nearest NOAA station (EASTPORT) is 4992 km away` - no `WORLDTIDES_KEY` set; manual fallback noted above.

---

*Cached flow verified 2026-07-10: profile age always displayed, re-research not suggested (2 days < ~6 months), pinned buoy fetched directly, surfer units honored, verdicts spot-corrected and personal. Compare `2026-07-08-mundaka.md` for the full research run that created the profile.*
