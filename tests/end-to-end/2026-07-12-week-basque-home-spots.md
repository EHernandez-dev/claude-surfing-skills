# E2E: /surfing:week over three Basque home spots (2026-07-12)

Demonstrates the `/surfing:week` multi-spot planner of issue #8 end to end: a live
run that sweeps Elena's home spots, ranks the whole week into one dashboard, and
renders it to HTML. Today (2026-07-12) is a Saturday-Sunday target window; it is
also a small, flat summer week on the Basque coast, which is exactly what makes it
a good honesty test.

**What this run demonstrates (issue #8 acceptance criteria):**

- **Home spots read from `surfer.yaml`:** no spot arguments were passed, so the
  sweep resolved the spot list from `home_spots` (mundaka, la-salvaje-sopelana,
  zarautz). Because `surfer.yaml` exists, `--surfer-file` was passed on every fetch
  and the verdicts are personalized (intermediate, comfortable to ~1.5 m clean
  faces, dawn patrol, weekday sessions out by 09:00, target days Sat/Sun).
- **Parallel fetches, profiled via `--spot-file`:** the two profiled spots fetched
  straight from their `spots/*.yaml` profiles (absolute paths, no re-research); the
  unprofiled spot fetched via `--coordinates`/`--spot-name`/`--facing`. All three
  Bash calls were issued in a single message so they ran in parallel.
- **Ranked Go / Worth a check / Skip dashboard:** one ranked view across all spots
  and days, best window first, using only the Go / Worth a check / Skip vocabulary
  (no raw quality score ever printed as a verdict).
- **Unprofiled Zarautz folded in on the quality score, flagged:** Zarautz has no
  `spots/zarautz.yaml`, so its verdicts come from the spot-agnostic quality score
  only and it carries the exact flag `unprofiled - run /surfing:research zarautz`.
- **Profile-correction beats the raw score:** Mundaka's Sunday dawn scores "fair"
  (5.5, the highest raw block of the week), yet the works-on profile corrects it to
  Skip because 0.5-0.6 m is under Mundaka's 1.0 m break minimum and 8 s is under its
  12 s period floor. This is the case the works-on correction exists for.
- **`--mode week` HTML rendered:** the assembled week data package rendered to
  `reports/2026-07-12-week.html` under the surf folder (verified, then deleted; the
  committed evidence is this Markdown doc, and the golden-file tests pin the
  renderer's HTML for a fixture of the same shape).

**Setup (in `tests/end-to-end/surf-folder/`):**

- `surfer.yaml` - Elena, intermediate, metric units, target days Sat/Sun, dawn
  patrol on, weekday sessions must end by 09:00. `home_spots` now lists a third
  entry, `zarautz`, added for this run and left intentionally UNPROFILED.
- `spots/mundaka.yaml` - existing profile, researched 2026-07-08 (4 days old).
- `spots/la-salvaje-sopelana.yaml` - existing profile, researched 2026-07-12 (fresh).
- No `spots/zarautz.yaml` - Zarautz is the unprofiled path. Coordinates were
  resolved live (Open-Meteo geocode plus coastline check) to a point in the water
  off mid-beach at 43.2920, -2.1770, facing estimated NNW (340 deg) from the
  north-facing Gipuzkoa coastline between Getaria and Orio.

**Commands (three parallel fetches + the render, run 2026-07-12 ~14:00 local):**

```bash
# ${CLAUDE_PLUGIN_ROOT} is the repo root; the sweep runs with the surf folder as cwd.
PLUGIN_ROOT=/Users/elenahernandez/projects/claude-surfing-skills
SURF_FOLDER=$PLUGIN_ROOT/tests/end-to-end/surf-folder

# Phase 2: one fetch per spot, all issued in a single message so they run in
# parallel, --days 7, --surfer-file on every run (surfer.yaml exists).
cd $PLUGIN_ROOT/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/mundaka.yaml" \
  --surfer-file "$SURF_FOLDER/surfer.yaml" --days 7

cd $PLUGIN_ROOT/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/la-salvaje-sopelana.yaml" \
  --surfer-file "$SURF_FOLDER/surfer.yaml" --days 7

cd $PLUGIN_ROOT/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --coordinates "43.2920,-2.1770" --spot-name "Zarautz" --facing 340 \
  --surfer-file "$SURF_FOLDER/surfer.yaml" --days 7

# Phase 5: render the assembled week data package, run from the surf folder so the
# HTML lands under reports/.
cd $SURF_FOLDER && uv run --project "$PLUGIN_ROOT/skills/spot-researcher/tools" \
  python "$PLUGIN_ROOT/skills/spot-researcher/tools/render_report.py" \
  --data "$SCRATCHPAD/week-package.json" --mode week
```

No `WORLDTIDES_KEY` in this environment, so Spanish tides gap at all three spots
(NOAA CO-OPS covers US coasts only). The sweep ranks on swell, wind, and daylight
and points at tide-forecast.com for manual tide overlay. This is documented
degradation, exactly what the tool contract's error path is for.

---

# Surf Week Planner: three Basque home spots (2026-07-12 to 2026-07-18)

**Sunday dawn at La Salvaje, and only just:** one clean, knee-high fish session on
the learner banks is the single window worth planning around. Zarautz Sunday
morning and La Salvaje Monday dawn are marginal backups. Everything else this week,
all of Mundaka included, is flat or blown out.

## Best windows this week (ranked, best first)

| Rank | Day/Date | Spot | Window | Verdict | Swell (ht @ period, dir) | Wind | Why |
|---|---|---|---|---|---|---|---|
| 1 | Sun Jul 12 | La Salvaje (Sopelana) | 06:07-11:00 dawn, glassy | 🟡 Worth a check | 0.5 m @ 8.3 s NNW | 2-6 km/h SSE offshore | Cleanest, longest-period window of the week and inside the 0.5-1.5 m sand-bank band; the Bilbao II buoy reads 0.7 m @ 9.6 s, a touch more energy than modeled. Small learner-bank fish session on your target day. Wind trashes it by 17:00 (36 km/h NW onshore). |
| 2 | Sun Jul 12 | Zarautz | 08:00-11:00 morning (score peaks fair at 17:00) | 🟡 Worth a check | 0.4 m @ 6.9 s N/NW | 11-17 km/h SE offshore (morning) | Unprofiled, so this is the spot-agnostic quality score only: it peaks "fair" (4.5) at 17:00 in cross-shore wind, but the clean SE-offshore morning is the practical check. First-pass estimate, no works-on correction. |
| 3 | Mon Jul 13 | La Salvaje (Sopelana) | 06:08-09:00 dawn, out by 09:00 | 🟡 Worth a check | 0.6 m @ 3.6 s NW | 6 km/h SSE offshore | Biggest, cleanest of the weak windswell mornings; short-period but the banks still break. Weekday, so the session ends by 09:00. |

No 🟢 Go anywhere this week, and no other window ranks: the remaining 18 spot-days
are all 🔴 Skip (flat or short-period windswell, and Mundaka sits under its break
threshold every single day). Tides gapped at all three spots (no `WORLDTIDES_KEY`):
overlay tides yourself from [tide-forecast.com](https://www.tide-forecast.com).

## Per-spot week overview

Column headers are the actual forecast days, starting on the run day (Sunday 12), not on Monday.

| Spot | Sun 12 | Mon 13 | Tue 14 | Wed 15 | Thu 16 | Fri 17 | Sat 18 |
|---|---|---|---|---|---|---|---|
| Mundaka | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| La Salvaje (Sopelana) | 🟡 | 🟡 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| Zarautz | 🟡 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |

- **Mundaka** - `mundaka.yaml`, researched 2026-07-08, 4 days old (current, no
  re-research needed). Not breaking any day this week: every day sits under the
  1.0 m size minimum and the 12 s period floor from its works-on profile, so the
  generically "fair" Sunday dawn score (5.5, the week's highest raw block) is
  corrected to Skip. Buoy Boya de Bilbao-Vizcaya (2136, 36 km) reads 0.8 m @
  10.35 s from NE, off the NW window and still under threshold, confirming the flat
  call. It is a rivermouth wave for autumn and winter; summer is flat.
- **La Salvaje (Sopelana)** - `la-salvaje-sopelana.yaml`, researched 2026-07-12,
  0 days old (fresh). The sand banks work at all tides and on short-period
  windswell (no minimum period in the profile), so the two clean offshore dawns
  (Sun, Mon) are Worth a check on the learner banks; the rest are flat or onshore.
  Buoy Boya Costera de Bilbao II (1103, 10.4 km, height/period only) reads 0.7 m @
  9.6 s, slightly more energy than the 0.5 m modeled. Surf the mid-beach banks, not
  La Triangular (that reef needs 2.5 m+ and is dormant all week).
- **Zarautz** - `unprofiled - run /surfing:research zarautz`. Coordinates resolved
  to a point in the water off mid-beach (43.292, -2.177), facing estimated NNW
  (340 deg) from the coastline, so wind classification and the quality score are
  available. Verdicts are the spot-agnostic quality score only: it does not know
  this break's swell window, ideal tide, or size ceiling, so treat Sunday as a
  first-pass estimate and check the beach. Buoy Boya de Pasaia II (1101, 24.6 km)
  reads 0.6 m @ 9.8 s from NNW.

**Bottom line for Elena:** if you surf once this week, make it Sunday dawn at La
Salvaje on the fish, before the 17:00 onshore blow (37 C, thundery, gusts to 70
km/h by evening). Zarautz Sunday morning is a near-identical clean-but-tiny backup
if you want the drive east. Everything from Tuesday on is flat; save the shortboard
for the next swell.

---

## HTML render

Week data package assembled from the three fetcher payloads (ranking ordered
best-first to match the dashboard table above), fed to `render_report.py --mode week`:

```json
{
  "mode": "week",
  "units": {"system": "metric", "wave_height": "m", "tide_height": "m", "wind_speed": "km/h", "temperature": "°C"},
  "week": {"start": "2026-07-12", "end": "2026-07-18"},
  "spots": [
    {
      "name": "Mundaka",
      "slug": "mundaka",
      "coordinates": [43.4095, -2.6995],
      "profiled": true,
      "profile_age_days": 4,
      "reresearch_suggested": false,
      "verdict_source": "works-on profile",
      "days": [
        {"date": "2026-07-12", "verdict": "skip", "best_time": null, "swell": "0.6 m @ 8.4 s NW", "wind": "5-7 km/h S light (dawn)", "why": "Under Mundaka's 1.0 m break minimum and 12 s period floor; buoy 0.8 m @ 10.35 s NE is off-window. Not breaking."},
        {"date": "2026-07-13", "verdict": "skip", "best_time": null, "swell": "0.7 m @ 3.8 s WNW", "wind": "3-5 km/h S light", "why": "Short-period windchop, well under the size and period minimums."},
        {"date": "2026-07-14", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 6.0 s NW", "wind": "4-5 km/h light", "why": "Effectively flat."},
        {"date": "2026-07-15", "verdict": "skip", "best_time": null, "swell": "0.7 m @ 3.5 s NNW", "wind": "10-12 km/h W/NW", "why": "Gutless short-period slop, onshore later."},
        {"date": "2026-07-16", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 3.5 s NNW", "wind": "4-7 km/h light early", "why": "Tiny, not breaking."},
        {"date": "2026-07-17", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 3.4 s N", "wind": "14-17 km/h W cross-shore", "why": "Flat and windy."},
        {"date": "2026-07-18", "verdict": "skip", "best_time": null, "swell": "0.3 m @ 3.8 s NNW", "wind": "6-7 km/h SW light", "why": "Flat; next weekend needs a new swell."}
      ]
    },
    {
      "name": "La Salvaje (Sopelana)",
      "slug": "la-salvaje-sopelana",
      "coordinates": [43.3905, -3.0015],
      "profiled": true,
      "profile_age_days": 0,
      "reresearch_suggested": false,
      "verdict_source": "works-on profile",
      "days": [
        {"date": "2026-07-12", "verdict": "check", "best_time": "06:07-11:00", "swell": "0.5 m @ 8.3 s NNW", "wind": "2-6 km/h SSE offshore (dawn)", "why": "Clean dawn on the learner banks, inside the 0.5-1.5 m band; buoy 0.7 m @ 9.6 s reads a touch more energy. Wind trashes it by 17:00. Target day."},
        {"date": "2026-07-13", "verdict": "check", "best_time": "06:08-09:00", "swell": "0.6 m @ 3.6 s NW", "wind": "6 km/h SSE offshore (dawn)", "why": "Biggest, cleanest of the weak windswell mornings; short-period but the banks still break. Weekday, so out by 09:00."},
        {"date": "2026-07-14", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 4.3 s NNW", "wind": "3-6 km/h SSE light", "why": "Tiny short-period, barely breaking."},
        {"date": "2026-07-15", "verdict": "skip", "best_time": null, "swell": "0.5 m @ 3.2 s NNW", "wind": "11-13 km/h NW onshore from 08:00", "why": "Onshore early, gutless."},
        {"date": "2026-07-16", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 3.5 s N", "wind": "7-10 km/h light early", "why": "Flat, onshore midday."},
        {"date": "2026-07-17", "verdict": "skip", "best_time": null, "swell": "0.3 m @ 3.2 s N", "wind": "9-14 km/h W to NNW", "why": "Flat and onshore."},
        {"date": "2026-07-18", "verdict": "skip", "best_time": null, "swell": "0.3 m @ 3.0 s NNW", "wind": "4 km/h SSW light", "why": "Flat; the script rates it 0."}
      ]
    },
    {
      "name": "Zarautz",
      "slug": "zarautz",
      "coordinates": [43.292, -2.177],
      "profiled": false,
      "profile_age_days": null,
      "reresearch_suggested": false,
      "verdict_source": "quality score (spot-agnostic)",
      "days": [
        {"date": "2026-07-12", "verdict": "check", "best_time": "08:00-11:00 (score peaks fair 17:00)", "swell": "0.4 m @ 6.9 s N/NW", "wind": "11-17 km/h SE offshore (morning)", "why": "Unprofiled: score peaks fair at 17:00 in cross-shore wind; the clean offshore morning is the practical check. First-pass only."},
        {"date": "2026-07-13", "verdict": "skip", "best_time": null, "swell": "0.5 m @ 4.1 s NW", "wind": "3-5 km/h light", "why": "Weak windswell, poor score."},
        {"date": "2026-07-14", "verdict": "skip", "best_time": null, "swell": "0.3 m @ 6.1 s NW", "wind": "4-5 km/h light", "why": "Near flat."},
        {"date": "2026-07-15", "verdict": "skip", "best_time": null, "swell": "0.6 m @ 3.75 s NNW", "wind": "6-12 km/h W to onshore", "why": "Short-period, onshore by 11:00."},
        {"date": "2026-07-16", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 3.5 s NNW", "wind": "8-9 km/h light early", "why": "Tiny."},
        {"date": "2026-07-17", "verdict": "skip", "best_time": null, "swell": "0.4 m @ 3.4 s N", "wind": "10-12 km/h W/NW", "why": "Flat, onshore."},
        {"date": "2026-07-18", "verdict": "skip", "best_time": null, "swell": "0.3 m @ 2.9 s NNW", "wind": "2-4 km/h light", "why": "Flat; score 0."}
      ]
    }
  ],
  "ranking": [
    {"date": "2026-07-12", "spot_slug": "la-salvaje-sopelana", "window": {"from": "06:07", "to": "11:00", "label": "dawn, light SSE offshore, glassy"}, "verdict": "check", "swell": "0.5 m @ 8.3 s NNW", "wind": "2-6 km/h SSE offshore", "why": "Cleanest, longest-period window of the week and inside the 0.5-1.5 m sand-bank band; buoy 0.7 m @ 9.6 s reads slightly more energy. Small learner-bank fish session on your target day; wind trashes it by 17:00."},
    {"date": "2026-07-12", "spot_slug": "zarautz", "window": {"from": "08:00", "to": "11:00", "label": "morning SE offshore (score peaks fair at 17:00 cross-shore)"}, "verdict": "check", "swell": "0.4 m @ 6.9 s N/NW", "wind": "11-17 km/h SE offshore", "why": "Unprofiled, so this is the spot-agnostic quality score only: it peaks fair at 17:00 in cross-shore wind, but the clean offshore morning is the practical check. First-pass estimate, no works-on correction."},
    {"date": "2026-07-13", "spot_slug": "la-salvaje-sopelana", "window": {"from": "06:08", "to": "09:00", "label": "dawn, light SSE offshore, out by 09:00"}, "verdict": "check", "swell": "0.6 m @ 3.6 s NW", "wind": "6 km/h SSE offshore", "why": "Biggest, cleanest of the weak windswell mornings; short-period but the banks still break. Weekday, so the session ends by 09:00."}
  ]
}
```

Renderer output (exit 0):

```json
{"html_path": "reports/2026-07-12-week.html"}
```

Verification before deleting: the file existed at `surf-folder/reports/2026-07-12-week.html`
at 178,117 bytes; `grep` confirmed all three spot names, the "Best windows this
week" ranking section, and the exact flag text `unprofiled - run /surfing:research
zarautz`. The rendered HTML was then deleted (and the empty `reports/` directory
removed): the committed evidence is this Markdown doc, and the renderer's HTML output
is pinned by the golden-file tests over `testdata/week-data-package.json` (a fixture
mirroring this run's shape, not this run's package).
