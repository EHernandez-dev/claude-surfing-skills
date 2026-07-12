---
name: briefing
description: Tomorrow's compact surf call across your home spots, plus a silent-unless-triggered 5-day swell alert
---

# Morning Briefing & Swell Alert

Answer the "is it worth a dawn patrol tomorrow" question across your home spots in one compact, few-second output, and (as a separate variant) fire a swell alert only when a spot's works-on profile is about to be met. This is the week planner's fetch-and-correct machinery (`commands/week.md` Phases 0-3) run at a one-day horizon: the same parallel `--spot-file` fetches and the same works-on verdict correction, reported as tomorrow's call instead of a ranked week. For the full week dashboard use `/surfing:week`; for a deep dive on one spot use `/surfing:windows`.

Read `commands/week.md` for the shared steps rather than restating them here; this command only calls out where it differs (one-day call, no HTML companion, and the swell-alert scan below).

## Two modes

- **Briefing (default):** `/surfing:briefing` prints tomorrow morning's call for the home spots: one headline, a compact per-spot row for tomorrow, and a one-line 5-day swell watch. Always produces output.
- **Alert:** `/surfing:briefing --alert` (also triggered by the bare word `alert` as an argument) prints **only** the swell alert and **nothing else**. It stays silent unless a home spot's works-on thresholds are forecast to be met within the next 5 days. This is the variant meant for automation (see `docs/AUTOMATION.md`).

Both modes fetch the same `--days 5` window per spot, so the single fetch serves tomorrow's detailed call and the 5-day alert scan at once.

## Phase 0: Surf Folder Check & Mode

Before any web lookup, resolve mode and spot list and read the surf folder (the working directory):

1. **Resolve the mode:** alert mode if the arguments contain `--alert` or a bare `alert` token; otherwise the default briefing. Strip that token before reading spot names.
2. **Resolve the spot list** (same rules as `week.md` Phase 0, first match wins): named spots in the arguments (comma- or space-separated, after the mode token is removed), else `surfer.yaml` `home_spots`, else stop and give the setup guidance (copy `assets/surfer-template.yaml` to `surfer.yaml` and list `home_spots`, or pass spot names this once). Do not guess a spot list. The briefing is built around home spots; arguments are the override.
3. **Note the surfer profile** and **partition profiled vs unprofiled** spots exactly as `week.md` Phase 0 items 2-3 (pass `--surfer-file` on every fetch when `surfer.yaml` exists; check for `spots/<slug>.yaml` per spot).

**Alert-mode short-circuit:** an unprofiled spot has no `works_on`, so it can never meet a threshold (Phase 4B). In `--alert` mode, drop unprofiled spots from the sweep entirely (they cannot fire). If that leaves **no** profiled spots, there is nothing that can trigger, so print the ordinary silent line (below) and stop, keeping alert mode quiet; the "profile a home spot to get alerts" nudge belongs in the default briefing, not the alert. In the default briefing, keep unprofiled spots: they still get a tomorrow row on the spot-agnostic quality score, flagged, and the footer notes when no home spot is profiled.

## Phase 1: Unprofiled Spot Resolution (Lite)

Only for the default briefing, and only for spots you will actually report. Resolve coordinates and estimate facing exactly as `week.md` Phase 1 (a light touch, not a research run). Alert mode skips this entirely (unprofiled spots were already dropped in Phase 0).

## Phase 2: Parallel Fetches

One `fetch_conditions.py` run per spot, **all Bash calls issued in a single message** so they run in parallel (allow ~60s per run). Use exactly the same invocations as `week.md` Phase 2 (the `${CLAUDE_PLUGIN_ROOT}` cd idiom, **absolute** paths for every profile file, `--surfer-file` on every run when `surfer.yaml` exists, `--units imperial` only if the user asked, and `--coordinates`/`--spot-name`/`--facing` for the unprofiled variant), with **one change: `--days 5`** instead of `--days 7` (enough to cover tomorrow's call and the full 5-day alert horizon in a single fetch). The profiled-spot form, for reference:

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "{absolute path to spots/<slug>.yaml}" \
  --surfer-file "{absolute path to surfer.yaml}" \
  --archive "{absolute path to the surf folder}/forecasts" \
  --days 5
```

Keep `--archive "{absolute path to the surf folder}/forecasts"` on every profiled fetch, exactly as `week.md` Phase 2: an unattended morning briefing is the natural place to accumulate the forecast archive `/surfing:verify` learns from.

Alert mode fetches only the profiled spots (unprofiled ones were dropped in Phase 0).

A spot that fails does not block the run: `fetch_conditions.py` exits 0 with an `error` field on network or API trouble (never a hard fail). In the default briefing, degrade that spot to a row noting the error and a manual-check link. In alert mode, a spot that could not be fetched simply cannot be evaluated, so it stays silent (it never produces a false ping).

## Phase 3: Per-Spot Verdicts & Threshold Check

Correct each profiled spot's forecast to its own `works_on` profile exactly as `week.md` Phase 3 (SKILL.md Step 4B): judge swell direction against the swell window, size against the working range and ceiling, period against the minimum, shift the session time toward the ideal tide, and cross-check today against the pinned buoy. Personalize when `surfer.yaml` exists (skill level, comfort zone, the fitting board from the quiver, target days, scheduling notes such as "weekday sessions out by 09:00"). Unprofiled spots get the spot-agnostic quality score only, flagged. When a spot's payload carries a `bias` block (a stored `model_bias` from `/surfing:verify`), its swell numbers are already bias-corrected; note it in that spot's row with `bias.note`.

Then evaluate the **swell-alert trigger** (Phase 4B) for each profiled spot over the 5-day window. Both modes need this: the default briefing shows the result as a one-line footer, and alert mode shows only this.

## Phase 4A: The Briefing (default mode)

Compact and scannable. "Tomorrow" is the forecast day whose date is the run date + 1 (the second `marine` day, index 1); key the call on that day. Never print a raw quality score as a verdict; use Go / Worth a check / Skip only.

1. **Headline one-liner:** tomorrow's single best call across the home spots, and the honest rest, e.g. "Tomorrow (Mon Jul 13): one worth-a-check window, La Salvaje dawn on the fish, out by 09:00. Mundaka and Zarautz flat." If nothing is worth it, say so plainly.

2. **Tomorrow table**, one row per home spot:

   | Spot | Verdict | Best window | Swell (ht @ period, dir) | Wind | Why |
   |---|---|---|---|---|---|

   - Label every quantity with the payload's `units` object.
   - State each profiled spot's age inline (e.g. "mundaka.yaml, 4 days old"); add a re-research nudge only when `reresearch_suggested`.
   - Flag every unprofiled spot with exactly `unprofiled - run /surfing:research <spot>`, and note when facing was unknown.
   - A spot whose fetch failed gets a row noting the error and a manual-check link, not a fabricated verdict.

3. **5-day swell watch (footer, one line):** the Phase 4B result. When something triggers, surface it here ("Heads up: Mundaka may turn on Thursday: 1.8 m @ 14 s, S wind"). When nothing triggers, say so in one line ("5-day watch: nothing meets works-on thresholds").

4. **Bottom line** (when `surfer.yaml` exists): one sentence for this surfer, the board that fits and any scheduling constraint honored.

## Phase 4B: The Swell Alert (alert mode, and the briefing footer)

A home spot **triggers** when, on **any day within the next 5 forecast days**, the spot's **works-on profile is about to be met**: all of the gates below hold on that day. Every threshold is read from **that spot's own `works_on` profile**, never a global constant.

The three headline gates (PRD): 

1. **Size:** the day's forecast swell/wave height is at or above the spot's works-on **size minimum** (the "minimum to break" figure in `works_on.swell_size`, e.g. Mundaka's 1.0 m).
2. **Period:** the day's forecast peak period is at or above `works_on.min_period_s`. **When `min_period_s` is null** the profile states no period floor for this break (e.g. La Salvaje, whose profile notes short-period windswell still produces learner waves on the banks), so the period gate is not applied. It is a non-gate here, not a permanent block: size, wind, and direction still guard against junk. A profile that wants to suppress marginal-period pings sets a `min_period_s` on its next `/surfing:research` run, keeping the threshold in the profile rather than a global constant.
3. **Wind:** the day has an **offshore or light-wind window** during surfable light, in the sense of the spot's `works_on.wind` (offshore for this facing, or wind light enough that direction does not matter).

Plus the works-on **direction** window, the same Phase 3 / SKILL Step 4B correction applied to every verdict: the swell direction must be within `works_on.swell_direction`. A swell from outside the window does not arrive at the break regardless of size, so it is not the profile being met. This is not a fourth independent threshold, it is the works-on match itself: the alert fires when the profile is about to be met, and the profile includes its swell window.

**Spots that can never trigger** (they produce no false ping): unprofiled spots (no `works_on` at all), and spots whose fetch errored (nothing to evaluate).

**Output:**

- **Triggered:** one line per triggering spot, **earliest day first** (break ties toward the bigger, cleaner day), in exactly this shape:

  ```
  {Spot} may turn on {Weekday}: {size} @ {period}, {wind-direction} wind
  ```

  For example: `Mundaka may turn on Thursday: 1.8 m @ 14 s, S wind`. Keep it to the crossing day and the crossing numbers; no table, no prose.

- **Nothing triggered:** stay silent. Emit only the single line:

  ```
  No swell alert: nothing meets works-on thresholds in the next 5 days.
  ```

  Automation filters on the phrase `may turn on` (see `docs/AUTOMATION.md`), so the silent line sends no notification.

In `--alert` mode, print **only** the alert output above: no headline, no tomorrow table, no footer. In the default briefing, this result is the one-line footer from Phase 4A item 3.

## Error Handling

- **A spot fails to fetch:** `fetch_conditions.py` exits 0 with an `error` field, never a hard fail. Default briefing: keep the spot as a row noting the failure with a manual-check link. Alert mode: the spot cannot be evaluated, so it stays silent. Manual checks: waves `https://www.windy.com/-Waves-waves`, buoys `https://www.ndbc.noaa.gov` (US) or `https://portus.puertos.es` (Spain), tides `https://www.tide-forecast.com`.
- **A non-US spot has no tide data** (`tides.error`, no `WORLDTIDES_KEY`): the tide gap does not change the alert (it keys on swell, period, and wind). In the default briefing, note the tide gap for that spot with a `https://www.tide-forecast.com` link.
- **A profiled spot has no period floor** (`min_period_s: null`): the period gate is not applied for it, so it can still trigger on a size + wind + direction match (Phase 4B). If its pings feel too eager, add a `min_period_s` on the next `/surfing:research <spot>`.
- **No spots resolved** (no arguments, no `surfer.yaml` home spots): stop and give the setup guidance from Phase 0 rather than briefing nothing.
- **Alert mode with only unprofiled spots:** nothing can trigger; print the ordinary silent line and stop (Phase 0 short-circuit). Alert mode stays quiet; the "profile a home spot" nudge is a default-briefing footer note.
