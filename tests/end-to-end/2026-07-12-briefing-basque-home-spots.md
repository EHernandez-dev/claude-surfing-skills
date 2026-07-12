# E2E: /surfing:briefing over three Basque home spots (2026-07-12)

Demonstrates the `/surfing:briefing` command and its `--alert` variant of issue #9
end to end: a live run that gives tomorrow morning's compact call across Elena's
home spots and runs the 5-day swell-alert scan. Today (2026-07-12) is a Sunday, so
"tomorrow" is Monday 2026-07-13. It is a small, flat summer week on the Basque coast
(the same window as the `/surfing:week` e2e), which is what makes it a good test of
per-profile thresholds: the same weak swell that fires La Salvaje's alert leaves
Mundaka silent, because each spot's gates come from its own works-on profile.

**What this run demonstrates (issue #9 acceptance criteria):**

- **Tomorrow's call for the home spots in one compact output:** no spot arguments,
  so the spot list resolved from `surfer.yaml` `home_spots` (mundaka,
  la-salvaje-sopelana, zarautz). One headline, one per-spot row for Monday, one
  5-day watch footer. `--surfer-file` on every fetch, so verdicts are personalized
  (intermediate, comfortable to ~1.5 m clean faces, dawn patrol, weekday sessions
  out by 09:00, target days Sat/Sun).
- **A specific call when a works-on threshold is met, live:** over Jul 12-16, La
  Salvaje's own works-on profile is met on Monday's clean offshore dawn, so the
  alert fires exactly one line, `La Salvaje (Sopelana) may turn on Monday: 0.6 m @
  3.6 s, SSE wind`, in the Phase 4B shape. This is a real fired alert, not a
  constructed one.
- **The alert emits nothing when no works-on threshold is met:** the scoped run
  `/surfing:briefing --alert Mundaka` prints only the single silent line, because
  Mundaka meets none of its gates in the 5-day window. Same machinery, empty result.
- **Thresholds come from each spot profile's works-on values, never global
  constants:** the same weak short-period NW windswell fires La Salvaje (size min
  0.5 m, no period floor, S-SE offshore) and leaves Mundaka silent (size min 1.0 m,
  12 s period floor). Zarautz is silent for a third reason: unprofiled, it has no
  `works_on` at all, so nothing can be met. One swell, three different outcomes, all
  keyed to the profile.
- **`docs/AUTOMATION.md`:** committed alongside this run; covers scheduled routines,
  cron + headless `claude -p`, notification channels, and the "notify only on the
  `may turn on` phrase" filter that keeps a flat week quiet.

**Setup (reuses `tests/end-to-end/surf-folder/`, unchanged from the `/surfing:week` e2e):**

- `surfer.yaml` - Elena, intermediate, metric units, target days Sat/Sun, dawn
  patrol on, weekday sessions must end by 09:00. `home_spots`: mundaka,
  la-salvaje-sopelana, zarautz (the third left intentionally UNPROFILED).
- `spots/mundaka.yaml` - profile researched 2026-07-08 (4 days old on the run day).
  `works_on`: swell NW, size "1.0-1.5 m minimum to break", `min_period_s: 12`, wind
  S-SW offshore.
- `spots/la-salvaje-sopelana.yaml` - profile researched 2026-07-12 (fresh). `works_on`:
  swell NW, size "0.5-1.5 m for the sand banks", `min_period_s: null` (the profile
  notes short-period windswell still produces learner waves on the banks), wind
  S-SE offshore.
- No `spots/zarautz.yaml` - the unprofiled path. Coordinates resolved to a point in
  the water off mid-beach (43.292, -2.177), facing estimated NNW (340 deg).

**Commands (three parallel fetches, run 2026-07-12 local):**

```bash
# ${CLAUDE_PLUGIN_ROOT} is the repo root; the run uses the surf folder as cwd.
PLUGIN_ROOT=/Users/elenahernandez/projects/claude-surfing-skills
SURF_FOLDER=$PLUGIN_ROOT/tests/end-to-end/surf-folder

# Phase 2: one fetch per spot, all issued in a single message so they run in
# parallel, --days 5 (covers tomorrow's call and the full 5-day alert horizon),
# --surfer-file on every run.
cd $PLUGIN_ROOT/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/mundaka.yaml" \
  --surfer-file "$SURF_FOLDER/surfer.yaml" --days 5

cd $PLUGIN_ROOT/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/la-salvaje-sopelana.yaml" \
  --surfer-file "$SURF_FOLDER/surfer.yaml" --days 5

cd $PLUGIN_ROOT/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --coordinates "43.2920,-2.1770" --spot-name "Zarautz" --facing 340 \
  --surfer-file "$SURF_FOLDER/surfer.yaml" --days 5
```

No `WORLDTIDES_KEY` in this environment, so Spanish tides gap at all three spots
(`tides.error`); the briefing keys tomorrow's window on swell, wind, and daylight
and points at tide-forecast.com. The alert never depends on tides. This is
documented degradation, exactly what the tool contract's error path is for.

---

# Morning briefing: three Basque home spots

**Tomorrow (Mon Jul 13): one worth-a-check window, La Salvaje dawn on the fish, out
by 09:00. Mundaka and Zarautz are flat.** A weak, short-period NW windswell (0.5-0.7
m at 3-7 s) with a light SSE-offshore dawn: enough to move the La Salvaje learner
banks, not enough anywhere else.

## Tomorrow (Monday, Jul 13)

| Spot | Verdict | Best window | Swell (ht @ period, dir) | Wind | Why |
|---|---|---|---|---|---|
| La Salvaje (Sopelana) | 🟡 Worth a check | 06:08-09:00 dawn | 0.6 m @ 3.6 s NW | 6 km/h SSE offshore | In the 0.5-1.5 m sand-bank band, clean light-offshore dawn on the learner banks. Short-period (the profile sets no period floor, so the banks still break), so it is a fish/small-day-board session, not the shortboard. Weekday: out by 09:00. `la-salvaje-sopelana.yaml`, fresh. |
| Mundaka | 🔴 Skip | - | 0.7 m @ 7.0 s WNW | 5 km/h SSW light | Under Mundaka's 1.0 m break minimum and 12 s period floor, and WNW sits off the NW window. Not breaking. `mundaka.yaml`, 4 days old (current). |
| Zarautz | 🔴 Skip | - | 0.5 m @ 6.8 s NW | 5 km/h WSW light | `unprofiled - run /surfing:research zarautz`. Spot-agnostic quality score only (poor); tiny short-period windswell, first-pass estimate. |

**5-day watch:** the only works-on trigger in the next 5 days is tomorrow's La
Salvaje window above (the alert fires it, see below); nothing bigger is behind it
through Thursday. Save the shortboard for the next real swell.

**Bottom line for Elena:** if you paddle out tomorrow, make it the La Salvaje dawn
on the fish before work, and keep expectations knee-high. Otherwise this is a
rest-day week until a real swell shows.

---

# Alert runs: `/surfing:briefing --alert`

## Fire path (all home spots, 5-day horizon Jul 12-16)

Output (the whole output, nothing else):

```
La Salvaje (Sopelana) may turn on Monday: 0.6 m @ 3.6 s, SSE wind
```

One line. La Salvaje's works-on profile is met on Monday's dawn window; Mundaka and
Zarautz contribute nothing. Per-spot, all gates read from each profile:

| Spot | Gate evaluation over Jul 12-16 | Result |
|---|---|---|
| La Salvaje (Sopelana) | Mon 06:08 dawn: size 0.6 m ≥ 0.5 m min ✓; period floor null, gate not applied ✓; wind SSE offshore and light ✓; swell NW inside the NW window ✓. Sunday's only ≥0.5 m blocks are cross-shore, so Monday is the earliest crossing. | **Fires** |
| Mundaka | 5-day max swell 0.7 m (< 1.0 m size min) and max period 8.1 s (< 12 s floor); no day crosses either gate. | Silent (below threshold) |
| Zarautz | Unprofiled: no `works_on`, so there is no threshold to meet. | Silent (unprofiled) |

Note the per-profile contrast: the same weak NW windswell that meets La Salvaje's
0.5 m no-period-floor banks does not come close to Mundaka's 1.0 m / 12 s
rivermouth. The 3.6 s period fires La Salvaje precisely because its profile declares
period a non-gate there; had the profile set, say, `min_period_s: 6`, this window
would not fire, and the fix would live in the profile, never in a global constant.

## Silent path (scoped to Mundaka)

`/surfing:briefing --alert Mundaka` runs the identical scan over a spot that meets
none of its gates. Output (the whole output, nothing else):

```
No swell alert: nothing meets works-on thresholds in the next 5 days.
```

This is the "emits nothing" behavior: the silent line contains no `may turn on`, so
the automation filter in `docs/AUTOMATION.md` forwards no notification.

## Format reference

The Phase 4B line format is `{Spot} may turn on {Weekday}: {size} @ {period},
{wind-direction} wind`. The live La Salvaje fire above is one instance; a bigger
in-season crossing reads the same way, e.g. a Mundaka autumn swell of 1.8 m @ 14 s
NW with a S offshore wind would emit `Mundaka may turn on Thursday: 1.8 m @ 14 s, S
wind` (not fetchable in July: Mundaka's season is October-March and the forecast
horizon is 7 days).

Buoy cross-checks agree the coast is flat: Mundaka's Boya de Bilbao-Vizcaya (2136,
36 km) reads 0.9 m @ 9.96 s from NE (off the NW window, under threshold); La
Salvaje's Boya Costera de Bilbao II (10.4 km) reads 0.7 m @ 9.6 s; Zarautz's Boya de
Pasaia II (24.6 km) reads 0.6 m @ 9.8 s NNW.

---

## Verification notes

- All numbers above are from the three live `fetch_conditions.py --days 5` payloads
  run 2026-07-12 (Mundaka/La Salvaje via `--spot-file`, Zarautz via
  `--coordinates`/`--facing`). Tomorrow's rows are the Jul 13 day summaries and surf
  windows; the alert maxima are `max` over the five returned `marine.days`; the La
  Salvaje fire is its Jul 13 first-light window block (0.6 m @ 3.65 s NW, 6 km/h SSE
  light).
- The briefing is chat-only by design (compact tomorrow-morning call), so there is
  no HTML companion to render; the multi-spot HTML lives in `/surfing:week`.
- The committed evidence is this Markdown doc. No files were written to the surf
  folder by this run (the briefing renders no HTML).
