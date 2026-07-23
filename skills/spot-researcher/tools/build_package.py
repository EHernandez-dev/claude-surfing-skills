#!/usr/bin/env python3
"""Assemble the draft dashboard data package, deterministically.

Sibling of fetch_conditions.py with no network code: it reads the fetch
payload plus the spot profile YAML and writes a complete, render-ready data
package (conditions + spot_data + analysis + gaps). Fast mode renders this
draft as-is; normal mode's agent edits only the judgment layer (one-liners,
why strings, verdict overrides, window re-ranking).

Draft verdicts are true Verdicts (see docs/adr/0007): the quality-rating band
maps to go/check/skip, then the machine-readable works-on fields correct it.
Period below min_period_s is a hard skip; swell direction outside a +/-45
degree arc around the profile's compass token(s) demotes one step. The prose
works-on fields are never consulted, which is why every draft one_liner ends
with the "computed call, no analyst pass" tag.

Exit contract (see CLAUDE.md Tool Contract): exit 1 only for invalid CLI
arguments; unreadable or malformed input files exit 0 with {"error", "note"}.
"""

import json
import re
import sys
from datetime import date
from typing import Any

import click
import yaml

from fetch_conditions import BLOCK_GRID_HOURS, VERDICT_SLUGS, report_filename

# 16-point compass rose, 22.5 degrees apart, N = 0.
COMPASS_ROSE = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]
COMPASS_BEARINGS = {token: i * 22.5 for i, token in enumerate(COMPASS_ROSE)}

# Rating bands come from fetch_conditions.rate_block; this is the one place
# they map to verdicts.
VERDICT_BY_RATING = {"epic": "go", "good": "go", "fair": "check", "poor": "skip", "flat": "skip"}

DIRECTION_ARC_DEG = 45.0

DRAFT_TAG = "Computed call, no analyst pass."


def compass_bearing(token: Any) -> float | None:
    """Bearing in degrees for a 16-point compass token, or None."""
    if not isinstance(token, str):
        return None
    return COMPASS_BEARINGS.get(token.strip().upper())


def direction_tokens(value: Any) -> list[float]:
    """Bearings for every compass token found in a works-on direction value.

    The field is free prose ("NW", "S-SW", "W to NW"), so this scans for
    standalone rose tokens, longest first, and returns their bearings in
    order of appearance. No tokens means the value is unparseable.
    """
    if value is None:
        return []
    alternation = "|".join(sorted(COMPASS_ROSE, key=len, reverse=True))
    tokens = re.findall(rf"\b({alternation})\b", str(value).upper())
    return [COMPASS_BEARINGS[t] for t in tokens]


def _angular_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def draft_verdict(window: dict[str, Any], works_on: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Verdict for one surf_windows entry, corrected against works_on.

    Returns (verdict, reasons): reasons name each correction applied, for the
    why strings. Period below min_period_s is a hard skip (exact arithmetic);
    direction outside the arc demotes one step (token matching is fuzzy, so
    it nudges rather than overrules).
    """
    rating = window.get("rating")
    verdict = VERDICT_BY_RATING.get(rating)
    if verdict is None:
        return "skip", ["no quality score for the day"]

    reasons: list[str] = []
    works_on = works_on or {}

    min_period = works_on.get("min_period_s")
    period = window.get("swell_period_s")
    if isinstance(min_period, (int, float)) and isinstance(period, (int, float)) and period < min_period:
        verdict = "skip"
        reasons.append(f"period {_fmt(period)} s below the {_fmt(min_period)} s minimum")

    window_bearings = direction_tokens(works_on.get("swell_direction"))
    swell_bearing = compass_bearing(window.get("swell_direction"))
    if window_bearings and swell_bearing is not None:
        if min(_angular_distance(swell_bearing, b) for b in window_bearings) > DIRECTION_ARC_DEG:
            if verdict != "skip":
                verdict = {"go": "check", "check": "skip"}[verdict]
            reasons.append(
                f"swell {window['swell_direction']} outside the "
                f"{works_on['swell_direction']} window"
            )

    return verdict, reasons


def _fmt(v: Any) -> str:
    """Number formatted for display, trailing .0 trimmed."""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def swell_string(
    height: Any, direction: Any, period: Any, units: dict[str, Any]
) -> str | None:
    """Display-ready swell summary, e.g. "1.2 m NW @ 13 s"."""
    parts = []
    if height is not None:
        parts.append(f"{_fmt(height)} {units.get('wave_height', 'm')}")
    if direction:
        parts.append(str(direction))
    if period is not None:
        parts.append(f"@ {_fmt(period)} s")
    return " ".join(parts) if parts else None


# When a date is missing from the daylight days, assume light until early evening.
DEFAULT_LAST_LIGHT = "21:00"


def window_span(best_time: str, last_light: str) -> tuple[str, str]:
    """(from, to) for the best block: block end, clipped to last light.

    best_time may sit inside its block (build_surf_windows clamps the 05:00
    block to first light), so the block start is the latest grid hour at or
    before it.
    """
    hour = int(best_time[:2])
    start = max((h for h in BLOCK_GRID_HOURS if h <= hour), default=hour)
    end = f"{min(start + 3, 23):02d}:00"
    return best_time, min(end, last_light)


def window_label(from_time: str) -> str:
    hour = int(from_time[:2])
    if hour < 9:
        return "Dawn patrol"
    if hour < 12:
        return "Morning"
    if hour < 15:
        return "Midday"
    if hour < 18:
        return "Afternoon"
    return "Evening"


def _why(window: dict[str, Any], reasons: list[str]) -> str:
    # "conditions", not "rating": the quality score's rating word is never a
    # user-facing term (CONTEXT.md), and these strings land on the page.
    rating = window.get("rating")
    parts = []
    if rating in VERDICT_BY_RATING:
        parts.append(f"{rating} conditions at the {window.get('best_time', '?')} block")
    parts.extend(reasons)
    return "; ".join(parts)


def _build_week(
    marine_days: list[dict[str, Any]],
    windows_by_date: dict[str, dict[str, Any]],
    works_on: dict[str, Any] | None,
    units: dict[str, Any],
) -> list[dict[str, Any]]:
    """One draft row per forecast day; window-less days are honest skips."""
    rows = []
    for day in marine_days:
        window = windows_by_date.get(day.get("date"))
        if window:
            verdict, reasons = draft_verdict(window, works_on)
            rows.append(
                {
                    "date": day["date"],
                    "verdict": verdict,
                    "swell": swell_string(
                        window.get("swell_height"),
                        window.get("swell_direction"),
                        window.get("swell_period_s"),
                        units,
                    ),
                    "wind": window.get("wind"),
                    "why": _why(window, reasons),
                }
            )
        else:
            summary = day.get("summary") or {}
            rows.append(
                {
                    "date": day.get("date"),
                    "verdict": "skip",
                    "swell": swell_string(
                        summary.get("swell_height_max"),
                        summary.get("swell_direction_dominant"),
                        summary.get("swell_period_max_s"),
                        units,
                    ),
                    "wind": None,
                    "why": "no daylight forecast blocks",
                }
            )
    return rows


def _day_window(
    window: dict[str, Any], last_light_by_date: dict[str, str]
) -> dict[str, Any]:
    last_light = last_light_by_date.get(window["date"], DEFAULT_LAST_LIGHT)
    start, end = window_span(window["best_time"], last_light)
    return {"from": start, "to": end, "label": window_label(start)}


def _build_target_day(
    target_date: str,
    week_rows: list[dict[str, Any]],
    windows_by_date: dict[str, dict[str, Any]],
    last_light_by_date: dict[str, str],
) -> dict[str, Any]:
    row = next(r for r in week_rows if r["date"] == target_date)
    window = windows_by_date.get(target_date)
    windows = []
    if row["verdict"] != "skip" and window and window.get("best_time"):
        windows.append(_day_window(window, last_light_by_date))
    head = ", ".join(s for s in (row["swell"], row["wind"]) if s)
    core = "; ".join(s for s in (head, row["why"]) if s)
    one_liner = (f"{core.rstrip('.')}. " if core else "") + DRAFT_TAG
    return {
        "date": target_date,
        "verdict": row["verdict"],
        "one_liner": one_liner,
        "windows": windows,
    }


def _build_windows(
    week_rows: list[dict[str, Any]],
    windows_by_date: dict[str, dict[str, Any]],
    last_light_by_date: dict[str, str],
) -> list[dict[str, Any]]:
    """Ranked go/check windows, best score first; skip days rank nothing."""
    scored = []
    for row in week_rows:
        window = windows_by_date.get(row["date"])
        if row["verdict"] == "skip" or not window or not window.get("best_time"):
            continue
        entry = {
            "date": row["date"],
            "window": _day_window(window, last_light_by_date),
            "verdict": row["verdict"],
            "swell": row["swell"],
            "wind": row["wind"],
            "why": row["why"],
        }
        scored.append((window.get("score") or 0, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored]


# spot profile YAML -> spot_data.profile, per commands/dashboard.md Phase 2.
# Prose-derived fields (crowd, access, rentals, ...) are agent work and stay
# absent in a draft; the notes prose rides along verbatim as description.
WORKS_ON_FIELDS = [
    ("swell_direction", "ideal_swell_direction"),
    ("swell_size", "ideal_swell_size"),
    ("min_period_s", "ideal_period_s"),
    ("wind", "ideal_wind"),
    ("tide", "ideal_tide"),
    ("season", "best_season"),
]
BREAK_FIELDS = [
    ("type", "break_type"),
    ("bottom", "bottom"),
    ("direction", "wave_direction"),
    ("ability", "ability_level"),
]


def build_spot_data(profile: dict[str, Any]) -> dict[str, Any]:
    grid: dict[str, Any] = {}
    works_on = profile.get("works_on") or {}
    for src, dst in WORKS_ON_FIELDS:
        if works_on.get(src) is not None:
            grid[dst] = works_on[src]
    break_info = profile.get("break") or {}
    for src, dst in BREAK_FIELDS:
        if break_info.get(src) is not None:
            grid[dst] = break_info[src]
    if profile.get("notes"):
        grid["description"] = profile["notes"]

    spot_data: dict[str, Any] = {"profile": grid}
    for key in ("peaks", "hazards", "webcams"):
        if profile.get(key):
            spot_data[key] = profile[key]
    spot_data["community_notes"] = []
    return spot_data


def _error(message: str, note: str) -> dict[str, str]:
    return {"error": message, "note": note}


def build_package(
    payload: dict[str, Any],
    profile: dict[str, Any] | None,
    surfer: dict[str, Any] | None = None,
    target_day: str | None = None,
) -> dict[str, Any]:
    """Draft data package from a fetch payload (+ spot/surfer profiles).

    Returns the package, or an {"error", "note"} dict when the payload cannot
    carry a draft (no forecast days, or no surf_windows to derive verdicts
    from).
    """
    marine = payload.get("marine") or {}
    marine_days = marine.get("days") or []
    if not marine_days:
        return _error(
            "payload has no marine forecast days",
            "re-run fetch_conditions.py, check its gaps output, or assemble the package by hand",
        )
    if "surf_windows" not in payload:
        return _error(
            "payload has no surf_windows, so draft verdicts cannot be derived",
            "re-fetch with --facing (or a spot profile that carries facing_deg), "
            "or assemble the package by hand",
        )

    report = payload.get("report") or {}
    target_date = target_day or report.get("target_date") or marine_days[0].get("date")
    if target_date not in [d.get("date") for d in marine_days]:
        return _error(
            f"target day {target_date} is outside the forecast window",
            "pass a --target-day within the fetched days, or re-fetch with more days",
        )

    # Keep the report block consistent with the day the analysis keys to: the
    # renderer derives the dashboard filename from these entries.
    if target_date != report.get("target_date") or not report.get("filenames"):
        spot_name = (payload.get("spot") or {}).get("name") or report.get("spot_slug") or "spot"
        report["target_date"] = target_date
        report["filenames"] = {
            verdict: f"reports/{report_filename(target_date, spot_name, verdict)}"
            for verdict in VERDICT_SLUGS
        }

    units = payload.get("units") or {}
    works_on = (profile or {}).get("works_on")
    windows_by_date = {
        w["date"]: w for w in (payload.get("surf_windows") or []) if w.get("date")
    }
    last_light_by_date = {
        d["date"]: d.get("last_light", DEFAULT_LAST_LIGHT)
        for d in (payload.get("daylight") or {}).get("days", [])
        if d.get("date")
    }

    week_rows = _build_week(marine_days, windows_by_date, works_on, units)
    package: dict[str, Any] = {
        "conditions": payload,
        "analysis": {
            "target_day": _build_target_day(
                target_date, week_rows, windows_by_date, last_light_by_date
            ),
            "week": week_rows,
            "windows": _build_windows(week_rows, windows_by_date, last_light_by_date),
        },
        "gaps": payload.get("gaps", []),
    }
    if profile:
        package["spot_data"] = build_spot_data(profile)
    if surfer:
        package["surfer_profile"] = surfer
    return package


def _load_yaml(path: str, label: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{label} is not a YAML mapping: {path}")
    return data


@click.command()
@click.option("--payload", "payload_path", required=True, help="Path to the fetch_conditions.py payload JSON.")
@click.option("--spot-file", default=None, help="Path to the spot profile YAML (spots/<slug>.yaml).")
@click.option("--surfer-file", default=None, help="Path to surfer.yaml; passed through as surfer_profile.")
@click.option("--target-day", default=None, help="Target day (YYYY-MM-DD). Defaults to the payload's report.target_date.")
@click.option("--output", default=None, help="Write the package JSON here and echo {\"package_path\": ...}; without it the package prints to stdout.")
def cli(payload_path: str, spot_file: str | None, surfer_file: str | None, target_day: str | None, output: str | None) -> None:
    """Assemble the draft dashboard data package from a fetch payload."""
    try:
        if target_day is not None:
            date.fromisoformat(target_day)
    except ValueError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)

    try:
        with open(payload_path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError(f"payload is not a JSON object: {payload_path}")
        profile = _load_yaml(spot_file, "spot file") if spot_file else None
        surfer = _load_yaml(surfer_file, "surfer file") if surfer_file else None
    except (OSError, ValueError, yaml.YAMLError) as e:
        click.echo(
            json.dumps(
                {
                    "error": str(e),
                    "note": "could not read the inputs; check the paths, or assemble the package by hand",
                }
            )
        )
        return

    package = build_package(payload, profile, surfer=surfer, target_day=target_day)
    if "error" in package:
        click.echo(json.dumps(package, ensure_ascii=False))
        return

    if output:
        try:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(package, f, indent=2, ensure_ascii=False)
        except OSError as e:
            click.echo(
                json.dumps(
                    {
                        "error": f"could not write the package: {e}",
                        "note": "check the --output path, or rerun without --output and capture stdout",
                    }
                )
            )
            return
        click.echo(
            json.dumps(
                {
                    "package_path": output,
                    "target_day": package["analysis"]["target_day"]["date"],
                    "verdict": package["analysis"]["target_day"]["verdict"],
                }
            )
        )
    else:
        click.echo(json.dumps(package, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
