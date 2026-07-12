---
name: verify
description: Close the forecast loop for a spot - compare your session logs against archived forecasts and store the learned model bias
---

# Forecast Verification

Learn how the model misses one of your spots. This command compares the surfer's own session logs (`sessions/<date>-<slug>.md`) against the forecast snapshots archived for those days (`forecasts/<slug>.jsonl`), reduces the differences to a per-spot **model bias** (for example "under-calls size by ~0.3 m"), and writes it into the spot profile. Once stored, `/surfing:conditions`, `/surfing:week`, and `/surfing:briefing` apply that bias automatically and say so.

This is the read-and-learn half of the loop. The forecast half is `fetch_conditions.py --archive`, which the daily commands run to build `forecasts/<slug>.jsonl` (see `docs/AUTOMATION.md`). Verification needs both a forecast archive and at least one session log for the same day to have anything to compare.

If the user named a spot (e.g. `/surfing:verify Mundaka`), verify that spot. Otherwise ask which spot to verify (or offer the spots that have both a profile and a forecast archive).

## Phase 0: Surf Folder Check

Everything here is relative to the working directory (the surf folder). Resolve the spot slug (slugify the name, same slug used for reports and profiles) and gather the three inputs:

1. **Spot profile** `spots/<slug>.yaml` - the bias is stored here, so the spot must be profiled. If there is no profile, stop and tell the user to run `/surfing:research <spot>` first; there is nowhere to store a bias yet.
2. **Forecast archive** `forecasts/<slug>.jsonl` - the archived forecasts to judge against. If it is missing or empty, stop and explain: the archive is built by the daily commands (`fetch_conditions.py --archive`), so run `/surfing:conditions <spot>` (or `/surfing:week` / `/surfing:briefing`) on a few days first, then come back. Nothing to verify against yet is a normal early state, not an error.
3. **Session logs** `sessions/<date>-<slug>.md` - the surfer's own records for this spot (the slug at the end of the filename matches). List `sessions/` and pick every file whose slug matches. If there are none, stop and point the user at `skills/spot-researcher/assets/session-log-template.md` to log a session first.

## Phase 1: Extract Observed Conditions

Read each matching session log and pull the **observed** side of its "Conditions Encountered" table (the "As experienced" column), plus the session date (from the `Date:` field, which also matches the filename):

- **Swell height** - a single number. Convert a range to its midpoint ("3-4 ft" -> 3.5), and keep the unit the surfer wrote in.
- **Swell period** (seconds) when the log records it; omit it for that session when it does not.
- Ignore wind, tide, and water-temp for the bias arithmetic (the loop corrects swell size and period, the model's systematic misses at a spot); you may still mention them to the user.

**Units:** the forecast archive stores each snapshot's `units`. Read that unit (metric `m` is the default). Convert every observed height into the **same unit as the archive** before passing it on, so the arithmetic compares like with like. `verify_forecast.py` converts the final height bias to meters for the profile itself.

Assemble the observations as a JSON array, one object per session, dates in `YYYY-MM-DD`:

```json
[
  {"date": "2026-07-08", "swell_height": 1.3, "swell_period_s": 12.0},
  {"date": "2026-06-29", "swell_height": 0.8}
]
```

## Phase 2: Compute the Bias

Run the arithmetic seam (deterministic; the model does not do the sums):

```bash
cd ${CLAUDE_PLUGIN_ROOT}/skills/spot-researcher/tools && uv run python verify_forecast.py \
  --forecast-log "{absolute path to forecasts/<slug>.jsonl}" \
  --observations '{the JSON array from Phase 1}'
```

Pass the JSON array inline via `--observations`, or write it to a temp file and pass `--observations-file {path}` if it is large. Paths must be absolute: the `cd` moves out of the surf folder.

The tool prints JSON: `samples` (session/forecast pairs it matched, freshest forecast per day), `bias` (`swell_height_m` in meters and `swell_period_s` in seconds, each `observed - forecast`, or null when no pair carried it), a human `note`, `unmatched_sessions` (session dates with no archived forecast), and the per-session `matched` detail. It never hard-fails on a data problem (exit 0 with `error`/`note` on an unreadable log).

## Phase 3: Write the Bias to the Spot Profile

- **`samples` is 0** (no session date overlaps the archive, or the log is empty): do not touch the profile. Tell the user what is missing (usually: sessions predate the archive, so keep logging and let the archive accumulate) and list any `unmatched_sessions`.
- **`samples` is 1 or more:** update the `model_bias` block in `spots/<slug>.yaml`, preserving the rest of the profile (schema in `assets/spot-profile-template.yaml`):

  ```yaml
  model_bias:
    swell_height_m: {bias.swell_height_m}      # omit the block entirely if this is null
    swell_period_s: {bias.swell_period_s}      # omit this line when the tool returned null
    samples: {samples}
    last_verified: {today's date}
    note: {the tool's note}
  ```

  Drop `swell_height_m` and `swell_period_s` lines that came back null. If the bias rounds to 0.0 on both (the note says the model "tracks the forecast"), still write the block with `samples` and `last_verified` so the profile records that it was verified and found unbiased.

## Phase 4: Report

Tell the user, in a few lines:

1. **What was compared:** how many sessions matched archived forecasts (and note any `unmatched_sessions` and why, e.g. logged before the archive started).
2. **The bias found:** the tool's `note` in plain language ("At Mundaka the model under-calls size by ~0.3 m across 3 sessions").
3. **What changes now:** `/surfing:conditions`, `/surfing:week`, and `/surfing:briefing` will apply this bias to future forecasts for this spot and note it in their reasoning. The more sessions you log, the sharper it gets.

## Error Handling

- **No profile / no archive / no sessions:** stop with the specific next step (Phase 0). None of these is a failure; they are the loop not being ready yet.
- **`verify_forecast.py` soft-fails** (unreadable log, exit 0 with `error`/`note`): surface the note and do not write the profile.
- **Bias looks extreme** (e.g. several meters from one noisy session): mention that a single session is a weak signal; the bias sharpens as more sessions accumulate. Do not refuse to write it, but say the sample size.
- **Observed values missing from a log** (surfer left the swell blank): skip that session for the arithmetic and say which ones were unusable.
