# E2E: Forecast verification loop at Mundaka (2026-07-12)

Demonstrates the verification loop end to end (issue #10): `fetch_conditions.py
--archive` builds a forecast archive, the surfer logs a session, `/surfing:verify`
compares the two and stores a per-spot model bias, and a subsequent conditions
fetch applies that bias so the numbers match what the surfer actually gets.

**Setup (in a surf folder derived from `surf-folder/`):**

- `spots/mundaka.yaml` - Mundaka spot profile (from the 2026-07-08 research run), no `model_bias` yet
- `surfer.yaml` - intermediate surfer, metric units
- `sessions/2026-07-12-mundaka.md` - one session log the surfer wrote after paddling out
- `forecasts/mundaka.jsonl` - the forecast archive, built by the `--archive` run below

---

## Step 1: Archive the forecast (the forecast side of the loop)

A normal conditions fetch, with `--archive` pointed at the surf folder's `forecasts/`:

```bash
cd skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/mundaka.yaml" \
  --surfer-file "$SURF_FOLDER/surfer.yaml" \
  --archive "$SURF_FOLDER/forecasts" \
  --days 7
```

Payload echoed `archive: {path: forecasts/mundaka.jsonl, appended: 7}` - one JSONL snapshot per forecast day. The first lines:

```json
{"archived_on": "2026-07-12", "spot_slug": "mundaka", "date": "2026-07-12", "lead_days": 0, "swell_height": 0.6, "swell_period_s": 8.1, "swell_direction": "NW", ...}
{"archived_on": "2026-07-12", "spot_slug": "mundaka", "date": "2026-07-13", "lead_days": 1, "swell_height": 0.7, "swell_period_s": 6.95, "swell_direction": "WNW", ...}
```

The model called **0.6 m @ 8.1 s NW** for 2026-07-12. (Summer at Mundaka is small - the point is the miss, not the size.)

## Step 2: The surfer logs the session

`sessions/2026-07-12-mundaka.md`, the "As experienced" swell line:

> Swell: 1.0-1.2 m @ 10s NW  (forecast said ~0.6 m @ 8s)

The surfer got noticeably more size and period than the model forecast - a systematic miss the loop can learn.

## Step 3: `/surfing:verify Mundaka`

The command reads the session log, extracts the observed swell (midpoint 1.1 m @ 10 s, already metric like the archive), and runs the arithmetic seam:

```bash
cd skills/spot-researcher/tools && uv run python verify_forecast.py \
  --forecast-log "$SURF_FOLDER/forecasts/mundaka.jsonl" \
  --observations '[{"date":"2026-07-12","swell_height":1.1,"swell_period_s":10.0}]'
```

Output:

```json
{
  "spot_slug": "mundaka",
  "samples": 1,
  "bias": {"swell_height_m": 0.5, "swell_period_s": 1.9},
  "note": "model under-calls size by ~0.5 m",
  "matched": [{"date": "2026-07-12", "archived_on": "2026-07-12",
               "forecast": {"swell_height": 0.6, ...}, "diff": {"swell_height": 0.5, "swell_period_s": 1.9}}]
}
```

The freshest forecast for 2026-07-12 (archived that morning) is compared against the session: observed - forecast = **+0.5 m** and **+1.9 s**. Positive means the model under-calls, so the offset is added to future forecasts.

## Step 4: Write the bias to the profile

`/surfing:verify` writes the `model_bias` block into `spots/mundaka.yaml`:

```yaml
model_bias:
  swell_height_m: 0.5
  swell_period_s: 1.9
  samples: 1
  last_verified: 2026-07-12
  note: model under-calls size by ~0.5 m
```

## Step 5: The next conditions check applies it

A later `/surfing:conditions Mundaka` (or `week` / `briefing`) fetch, now that the profile carries the bias:

```bash
cd skills/spot-researcher/tools && uv run python fetch_conditions.py \
  --spot-file "$SURF_FOLDER/spots/mundaka.yaml" --days 2
```

The payload's `bias` block reports `applied: true, swell_height: 0.5 m, note: model under-calls size by ~0.5 m`, and day 0's swell summary now reads **1.1 m @ 10.0 s** instead of the raw 0.6 m @ 8.1 s - the correction folded straight into the numbers, the block quality, and the surf windows before the report ever sees them. The command surfaces `bias.note` so the correction is visible, never silent.

---

## What this increment proves

- `--archive` appends one JSONL snapshot per forecast day per spot to `forecasts/<slug>.jsonl` (append-only machine data, outside `spots/`). The snapshot stores the **raw** model forecast even when a bias is already applied to the report, so the loop always judges the model's own prediction and never un-learns its correction.
- `/surfing:verify` compares session logs to the archived forecasts (freshest snapshot per day) and reduces the difference to a per-spot model bias, written to the spot profile.
- `conditions` / `week` / `briefing` apply the stored bias to future forecasts and note it in their reasoning.
- The arithmetic is deterministic and tested at the CLI seam (`test_verify_forecast.py`); archiving and bias application are tested at the `fetch_conditions.py` seam (`test_fetch_conditions.py`).

**Caveat:** one session is a weak signal (the bias echoes `samples: 1`). It sharpens as the surfer logs more sessions and the archive accumulates - the daily briefing keeps the archive current unattended (`docs/AUTOMATION.md`).
