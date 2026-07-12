#!/usr/bin/env python3
"""Forecast verification arithmetic - the /surfing:verify bias seam.

Compares a surfer's observed sessions against the archived forecast snapshots
for the same days (forecasts/<slug>.jsonl, appended by
`fetch_conditions.py --archive`) and reduces the differences to a per-spot
model bias. /surfing:verify extracts the observed numbers from the freeform
session logs (sessions/<date>-<slug>.md), passes them here as structured JSON,
and writes the returned bias into the spot profile's `model_bias` block, where
`fetch_conditions.py --spot-file` applies it to future forecasts.

Sign convention matches the profile: bias is `observed - forecast`, so a
positive `swell_height_m` means the model under-calls size at this spot and the
offset is added to future forecasts to correct for it.

Observations are given in the same units as the forecast log (the log echoes
its `units`); the height bias is converted to meters for the profile, which is
always metric. Period is unit-neutral seconds.

Tool Contract (CLAUDE.md): a data problem (unreadable log, no overlapping days)
never hard-fails - it exits 0 with a JSON result (possibly zero samples) or an
`error`/`note`. Exit 1 is reserved for invalid CLI arguments (missing log or
observations, malformed observations JSON).
"""

import json
import sys
from typing import Any

import click

FT_PER_M = 3.28084

# Below this magnitude (meters) the model tracks the spot and no size bias is
# reported, so a couple of noisy sessions do not manufacture a correction.
BIAS_DEADBAND_M = 0.1


def load_forecast_log(text: str) -> list[dict[str, Any]]:
    """Parse a forecasts/<slug>.jsonl archive, skipping blank or malformed lines.

    The archive is append-only machine data; a single corrupt line must not
    sink the whole verification, so bad lines are dropped rather than raised.
    """
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("date"):
            records.append(record)
    return records


def freshest_by_date(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each forecast day to its freshest snapshot.

    A day is forecast many times as it approaches (a new snapshot each run); the
    fairest comparison against what the surfer actually got is the most recent
    forecast for that day, so ties on `archived_on` break toward the shorter
    lead time.
    """
    best: dict[str, dict[str, Any]] = {}
    for record in records:
        day = record["date"]
        current = best.get(day)
        if current is None or _fresher(record, current):
            best[day] = record
    return best


def _fresher(candidate: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    """True when `candidate` is a fresher forecast than `incumbent` for its day."""
    cand_key = candidate.get("archived_on") or ""
    inc_key = incumbent.get("archived_on") or ""
    if cand_key != inc_key:
        return cand_key > inc_key
    cand_lead = candidate.get("lead_days")
    inc_lead = incumbent.get("lead_days")
    if cand_lead is None or inc_lead is None:
        return False
    return cand_lead < inc_lead


def match_sessions(
    observations: list[dict[str, Any]], by_date: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pair each observation with the freshest forecast for its date.

    Returns (matched, unmatched_dates). A matched entry carries the observed
    values, the forecast snapshot, the signed diffs (observed - forecast), and
    the forecast's `archived_on`. An observation whose date has no archived
    forecast falls into `unmatched_dates`.
    """
    matched = []
    unmatched = []
    for obs in observations:
        obs_date = obs.get("date")
        forecast = by_date.get(obs_date) if obs_date else None
        if forecast is None:
            if obs_date:
                unmatched.append(obs_date)
            continue
        diff: dict[str, float] = {}
        for key in ("swell_height", "swell_period_s"):
            o, f = obs.get(key), forecast.get(key)
            if isinstance(o, (int, float)) and isinstance(f, (int, float)):
                diff[key] = o - f
        matched.append(
            {
                "date": obs_date,
                "archived_on": forecast.get("archived_on"),
                "observed": obs,
                "forecast": forecast,
                "diff": diff,
            }
        )
    return matched, unmatched


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def height_to_m(value: float, unit_label: str) -> float:
    """Convert a height in the forecast log's units to meters for the profile."""
    return value / FT_PER_M if unit_label == "ft" else value


def bias_note(height_bias_m: float | None) -> str:
    """Human phrasing of the size bias for the profile's `note` (always metric)."""
    if height_bias_m is None:
        return "no overlapping size samples to compare"
    if abs(height_bias_m) < BIAS_DEADBAND_M:
        return f"size tracks the forecast (within ~{BIAS_DEADBAND_M} m)"
    verb = "under-calls" if height_bias_m > 0 else "over-calls"
    return f"model {verb} size by ~{abs(round(height_bias_m, 1))} m"


def compute_bias(matched: list[dict[str, Any]], height_unit: str) -> dict[str, Any]:
    """Reduce matched observation/forecast pairs to a per-spot model bias.

    The height bias is the mean signed size error converted to meters (the
    profile is metric) and zeroed inside the deadband; the period bias is the
    mean signed period error in seconds. Each is None when no pair carried that
    measurement.
    """
    height_diffs = [m["diff"]["swell_height"] for m in matched if "swell_height" in m["diff"]]
    period_diffs = [m["diff"]["swell_period_s"] for m in matched if "swell_period_s" in m["diff"]]

    mean_height = _mean(height_diffs)
    height_bias_m = None
    if mean_height is not None:
        height_bias_m = height_to_m(mean_height, height_unit)
        if abs(height_bias_m) < BIAS_DEADBAND_M:
            height_bias_m = 0.0
        height_bias_m = round(height_bias_m, 2)

    mean_period = _mean(period_diffs)
    period_bias_s = round(mean_period, 2) if mean_period is not None else None

    return {
        "swell_height_m": height_bias_m,
        "swell_period_s": period_bias_s,
        "height_samples": len(height_diffs),
        "period_samples": len(period_diffs),
        "note": bias_note(height_bias_m),
    }


def _parse_observations(raw: str) -> list[dict[str, Any]]:
    """Parse the observations JSON, raising ValueError on anything but a list of
    objects so the CLI can report it as an invalid argument."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"observations is not valid JSON: {e}") from e
    if not isinstance(data, list) or not all(isinstance(o, dict) for o in data):
        raise ValueError("observations must be a JSON array of objects")
    return data


@click.command()
@click.option(
    "--forecast-log",
    default=None,
    help="Path to the spot's forecast archive (forecasts/<slug>.jsonl from fetch_conditions.py --archive).",
)
@click.option(
    "--observations",
    default=None,
    help='Observed sessions as a JSON array, e.g. \'[{"date":"2026-07-12","swell_height":1.4,"swell_period_s":13}]\', '
    "in the same units as the forecast log. Overrides --observations-file.",
)
@click.option("--observations-file", default=None, help="Path to a JSON file with the observed sessions array.")
@click.option("--spot-slug", default=None, help="Spot slug echoed in the result (defaults to the log's spot_slug).")
def cli(forecast_log: str | None, observations: str | None, observations_file: str | None, spot_slug: str | None):
    """Compute a spot's model bias from session observations vs archived forecasts."""
    if not forecast_log:
        click.echo(json.dumps({"error": "--forecast-log is required (forecasts/<slug>.jsonl)"}))
        sys.exit(1)

    raw_observations = observations
    if raw_observations is None and observations_file:
        try:
            with open(observations_file, encoding="utf-8") as f:
                raw_observations = f.read()
        except OSError as e:
            click.echo(json.dumps({"error": f"Cannot read observations file {observations_file}: {e}"}))
            sys.exit(1)
    if raw_observations is None:
        click.echo(json.dumps({"error": "Pass observed sessions via --observations or --observations-file"}))
        sys.exit(1)

    try:
        observed = _parse_observations(raw_observations)
    except ValueError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)

    try:
        with open(forecast_log, encoding="utf-8") as f:
            log_text = f.read()
    except OSError as e:
        click.echo(
            json.dumps(
                {
                    "error": f"Cannot read forecast log {forecast_log}: {e}",
                    "note": "Run the spot through /surfing:conditions (or week/briefing) so "
                    "fetch_conditions.py --archive builds forecasts/<slug>.jsonl first.",
                }
            )
        )
        sys.exit(0)

    records = load_forecast_log(log_text)
    by_date = freshest_by_date(records)
    height_unit = "m"
    if records:
        height_unit = records[0].get("units", {}).get("wave_height", "m")
    slug = spot_slug or (records[0].get("spot_slug") if records else None)

    matched, unmatched = match_sessions(observed, by_date)
    bias = compute_bias(matched, height_unit)

    result = {
        "spot_slug": slug,
        "samples": len(matched),
        "bias": {"swell_height_m": bias["swell_height_m"], "swell_period_s": bias["swell_period_s"]},
        "note": bias["note"],
        "height_samples": bias["height_samples"],
        "period_samples": bias["period_samples"],
        "matched": matched,
        "unmatched_sessions": unmatched,
        "units": records[0].get("units") if records else None,
    }
    if not records:
        result["note"] = "forecast log is empty - nothing to compare against yet"
    elif not matched:
        result["note"] = "no session dates overlap the archived forecasts yet"
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
