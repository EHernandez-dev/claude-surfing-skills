---
name: week
description: Plan the week across your home spots - one ranked dashboard of the best session windows
---

# Week Planner

Answer the "where and when should I surf this week" question across several spots at once: a single ranked dashboard of the best session windows over the next 7 days, best first, so you know the one session worth building the week around ("Thursday dawn at La Salvaje, skip everything else").

This is a multi-spot sweep, not a single-spot check. It fetches every spot in parallel, corrects each profiled spot's verdicts to its own works-on profile, folds the unprofiled spots in on the spot-agnostic quality score (visibly flagged), and ranks the whole week into one list. For a deep dive on one spot use `/surfing:windows`; to research and profile a spot use `/surfing:research`.

If the user passed spot names as arguments (e.g., `/surfing:week Mundaka, Meñakoz`), those are the spots to sweep. Otherwise sweep the surfer's home spots.

## Phase 0: Surf Folder Check

Before any web lookup, resolve the spot list and read the surf folder (the working directory):

1. **Resolve the spot list** (first match wins):
   - **Arguments:** if the user named spots, use exactly those (comma- or space-separated).
   - **Home spots:** else read `surfer.yaml` and use `home_spots`.
   - **Neither:** if there are no arguments and no `surfer.yaml` (or it lists no `home_spots`), stop and tell the user how to set one up: copy `assets/surfer-template.yaml` to `surfer.yaml` in the surf folder and list their usual spots under `home_spots`, or pass spot names as arguments this once. Do not guess a spot list.

2. **Note the surfer profile:** record whether `surfer.yaml` exists. When it does, pass `--surfer-file` on every fetch (its `units` preference applies, precedence: `--units` flag, then surfer profile, then metric) and personalize the verdicts for this surfer per Phase 3 (SKILL.md Step 4B item 6): skill level, comfort zone, the fitting board from their quiver, and their target days when windows tie.

3. **Partition the spots:** for each spot, check for a spot profile at `spots/<slug>.yaml` (slugify the name; list `spots/` if unsure).
   - **Profiled** spots carry confirmed coordinates, facing, and a works-on profile: fetch straight from the profile, no re-research.
   - **Unprofiled** spots need a lightweight coordinate and facing resolution (Phase 1) before they can be fetched, and their verdicts come from the spot-agnostic quality score only.

## Phase 1: Unprofiled Spot Resolution (Lite)

This is a light touch, NOT a research run. Do just enough per unprofiled spot to fetch conditions; `/surfing:research <spot>` is what turns a spot into a profiled one.

For each unprofiled spot:

1. **Resolve coordinates** the quick way (as `/surfing:conditions` Phase 1 does): a fast `WebSearch` sanity check on the location and/or geocode via `https://geocoding-api.open-meteo.com/v1/search?name={spot_name}&count=5` (WebFetch). Pin the point in the water, just outside the break, not the town center. If a name is genuinely ambiguous, prefer the candidate in the surfer's home region rather than stopping the sweep for a disambiguation prompt.
2. **Estimate facing** (`facing_deg`) from the coastline orientation on a map or known spot knowledge, per SKILL.md Phase 2. Facing enables wind classification and surf windows, which the quality score needs.
3. **When facing cannot be estimated,** run that spot without `--facing` and surface the degradation in the dashboard (wind on/off/cross-shore and its quality score will be absent for that spot). Do not block the sweep on it.

## Phase 2: Parallel Fetches

One `fetch_conditions.py` run per spot, with **all Bash calls issued in a single message** so they run in parallel (the same fan-out SKILL.md Step 4E uses for backup spots). Use the `${CLAUDE_PLUGIN_ROOT}` cd idiom, absolute paths for every profile file (the `cd` moves out of the surf folder), `--days 7` and the same units precedence everywhere.

Profiled spot:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "{absolute path to spots/<slug>.yaml}" \
  --days 7
```

Unprofiled spot:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --coordinates "{lat},{lon}" \
  --spot-name "{name}" \
  --facing {deg} \
  --days 7
```

Add `--surfer-file "{absolute path to surfer.yaml}"` to every run when the surfer profile exists. Add `--units imperial` only if the user asked for it (otherwise units follow the surfer profile, then metric). For an unprofiled spot whose facing could not be estimated, drop `--facing`.

Allow ~60s per run. A spot that fails does not block the sweep: `fetch_conditions.py` exits 0 with an `error` field on network or API trouble (never a hard fail), so degrade that spot to a dashboard row that notes the error and carry on with the rest.

## Phase 3: Per-Spot Verdicts

Turn each spot's forecast into per-day verdicts (Go, Worth a check, Skip). Never expose the raw quality score as a verdict.

**Profiled spots** are corrected to their own works-on profile, exactly as SKILL.md Step 4B lays out (do not restate it here, apply it): judge each day's swell direction against the profile's swell window, its size against the working range and ceiling, its period against the minimum, and shift the recommended session time toward the profile's ideal tide; cross-check today against the pinned buoy observation and flag disagreement. A generically "good" day from outside the swell window, under the minimum period, or over the size ceiling is a Skip, and the reasoning says why. Mention a `hazards`/`notes` one-liner when it changes the call.

- **State each profile's age** from the payload's `spot.profile` (e.g. "mundaka.yaml, researched 2026-07-08, 4 days old"). If `reresearch_suggested` is true (older than ~6 months), suggest a refresh with `/surfing:research <spot>`; the profile never expires, so still use it. If `last_researched` is null (hand-created profile), say the age is unknown and suggest a research run.
- **Personalize when `surfer.yaml` exists:** weigh each verdict against the surfer's skill level and comfort zone, name the fitting board from their quiver on Go / Worth-a-check days, and prefer their target days when the week is close.

**Unprofiled spots** have no works-on profile to correct against, so map the spot-agnostic quality straight to verdicts and label them honestly: the score knows period, size, and wind only, not this break's swell window, ideal tide, or size ceiling. These verdicts are a first-pass estimate, flagged as such in the dashboard.

## Phase 4: The Ranked Dashboard

The dashboard is the chat output and stays canonical (the HTML in Phase 5 is a companion). One ranked view across ALL spots, best windows first. Use the Go / Worth a check / Skip vocabulary only; never print a raw quality score as a verdict.

1. **Headline one-liner:** the single best session of the week and the honest rest, e.g. "Thursday dawn at La Salvaje, skip everything else" or "Two windows worth it: Thu dawn at La Salvaje, Sat mid-morning at Mundaka."

2. **Ranked best-windows table**, best first across every spot and day:

   | Rank | Day/Date | Spot | Window | Verdict | Swell (ht @ period, dir) | Wind | Why |
   |---|---|---|---|---|---|---|---|

   Order strictly best-first. Label every quantity with the payload's `units` object.

3. **Per-spot week overview:** a compact strip per spot, one verdict emoji per day (🟢 Go, 🟡 Worth a check, 🔴 Skip), so the week reads at a glance. Column headers are the actual days of the forecast window, which starts on the run day, not on Monday:

   | Spot | Sun 12 | Mon 13 | Tue 14 | Wed 15 | Thu 16 | Fri 17 | Sat 18 |
   |---|---|---|---|---|---|---|---|

   (Example headers; use the real dates from the payload's `marine` days.)

   - State each profiled spot's age next to its row (and a re-research nudge when `reresearch_suggested`).
   - Flag every unprofiled spot with exactly `unprofiled - run /surfing:research <spot>`, and note when facing was unknown (wind and quality degraded).
   - A spot whose fetch failed gets a row noting the error and a manual-check link (below), not a fabricated verdict.

## Phase 5: HTML Render

Render the multi-spot dashboard to HTML as a companion to the chat output.

1. Assemble the **week data package** (schema in SKILL.md, next to Step 6C) into a temp JSON file: `mode: "week"`, the `units` in effect, the `week` start/end, one `spots[]` entry per spot (with `profiled`, `profile_age_days`, `reresearch_suggested`, `verdict_source`, and chronological `days[]`), and the `ranking[]` array ordered best-first exactly as the dashboard table. The renderer preserves your `ranking` order and never re-sorts, so rank it here.
2. Run from the surf folder (so the HTML lands under `reports/`):

   ```bash
   uv run --project "${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools" python "${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools/render_report.py" \
     --data {absolute path to package.json} \
     --mode week
   ```

3. The script prints JSON on exit 0 either way:
   - Success: `{"html_path": "reports/{week.start}-week.html"}` (pass `--out` to override the path).
   - Soft failure: `{"error": ..., "note": ...}`. The markdown dashboard remains canonical; note the failure and continue, do not block on it.
4. Open the HTML for the user: `open {html_path}` on macOS, `xdg-open {html_path}` on Linux.

## Error Handling

- **A spot fails to fetch:** `fetch_conditions.py` exits 0 with an `error` field, never a hard fail. Keep the spot in the dashboard as a row noting the failure, and continue the sweep. Manual checks: waves `https://www.windy.com/-Waves-waves`, buoys `https://www.ndbc.noaa.gov` (US) or `https://portus.puertos.es` (Spain), tides `https://www.tide-forecast.com`.
- **A non-US spot has no tide data** (`tides.error`, no `WORLDTIDES_KEY`): note the tide gap for that spot with a `https://www.tide-forecast.com` link; rank it on swell, wind, and daylight.
- **Facing unknown for an unprofiled spot:** rank it on what is available and flag that wind classification and its quality score were skipped.
- **The HTML render fails:** the markdown dashboard is canonical; note it and move on.
- **No spots resolved** (no arguments, no `surfer.yaml` home spots): stop and give the setup guidance from Phase 0 rather than sweeping nothing.
