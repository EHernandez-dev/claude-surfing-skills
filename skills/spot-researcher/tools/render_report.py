#!/usr/bin/env python3
"""Deterministic HTML surf-report renderer for the spot-researcher skill.

Takes a Phase 5A data-package JSON (the fetch_conditions.py payload plus the
skill's spot_data and analysis) and writes one self-contained HTML file next
to the Markdown report, sharing its basename with a .html extension.

Design is fixed by the prototype verdict (an A/C hybrid on C's light palette):
a Leaflet map hero, a Python-generated tide curve with night shading and
shaded session windows, a week-at-a-glance table, webcam cards, always-visible
hazards, and a safety footer. Dark mode is carried via prefers-color-scheme.

Determinism is a hard requirement (golden-file tests compare byte-for-byte):
there are no wall-clock reads anywhere in the output path. Weekday names come
from the dates already in the package via datetime.date.weekday, which is pure.

Self-containment: Leaflet's JS and CSS are read from vendor/leaflet (resolved
relative to this file, never the CWD) and inlined. The only remote reference
in the output is the OpenStreetMap raster tile URL template. Every string that
comes from the data package is HTML-escaped before it reaches the output.

Tool Contract (CLAUDE.md): a network or data failure never hard-fails. A bad
or unreadable package exits 0 with a JSON {"error", "note"} pointing back to
the canonical Markdown report. Exit 1 is reserved for invalid CLI arguments
(a missing --data).
"""

import html
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import click

# Leaflet is vendored next to this file; resolve against __file__ so the CLI
# works from any working directory (it runs from the surf folder, not here).
VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "leaflet"

# Half a semidiurnal tide cycle, in hours (a ~12.42 h cycle). When a tide day
# has no neighbour on one side we pad a synthetic opposite-type extreme this
# far past the edge event so the cosine curve stays honest at 00:00 and 24:00.
SEMIDIURNAL_HALF_CYCLE_H = 6.21

# Verdict emoji + label for chips (both the hero and the week rows).
VERDICT_DISPLAY = {
    "go": ("\U0001f7e2", "GO"),
    "check": ("\U0001f7e1", "WORTH A CHECK"),
    "skip": ("\U0001f534", "SKIP"),
}

WEEKDAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Tide-curve SVG geometry (a fixed viewBox keeps the golden file stable; the
# CSS scales it responsively). Margins leave room for the axes and labels.
_SVG_W = 960.0
_SVG_H = 300.0
_PAD_L = 52.0
_PAD_R = 18.0
_PAD_T = 26.0
_PAD_B = 34.0
_PLOT_W = _SVG_W - _PAD_L - _PAD_R
_PLOT_H = _SVG_H - _PAD_T - _PAD_B
# Curve resolution: one sample every 0.2 h across the 24 h axis.
_SVG_SAMPLES = 120


# ---------------------------------------------------------------------------
# Pure seams (unit-tested, no I/O, no wall-clock)
# ---------------------------------------------------------------------------


def parse_hhmm(s: str) -> float:
    """Decimal hours from an 'HH:MM' string (e.g. '08:14' -> 8.2333...)."""
    hh, mm = s.split(":")
    return int(hh) + int(mm) / 60.0


def _opposite(tide_type: str) -> str:
    """The other tide phase (high <-> low)."""
    return "low" if tide_type == "high" else "high"


def assemble_extremes(tides_days: list[dict[str, Any]], target_date: str) -> list[dict[str, Any]]:
    """Tide extremes bracketing the target day, in decimal hours from its midnight.

    Returns a list of {t, h, type, time} sorted by t. `t` is hours relative to
    the target day's 00:00 and may be negative (a late event the day before) or
    above 24 (an early event the next day). The target day's own events are
    always included; the previous day's last event and the next day's first
    event are added when those days exist in `tides_days`, which keeps the
    curve honest at the 00:00 and 24:00 edges.

    When no adjacent event exists on a side, a synthetic opposite-type extreme
    is padded SEMIDIURNAL_HALF_CYCLE_H hours beyond the edge event, reusing the
    height of the nearest same-type real event (a plausible stand-in for the
    unseen neighbouring peak/trough). Synthetic entries carry time=None so the
    renderer does not label them. Returns [] when the target day is absent.
    """
    by_date = {d.get("date"): d.get("events", []) for d in (tides_days or [])}
    target_events = by_date.get(target_date)
    if not target_events:
        return []

    def as_extreme(event: dict[str, Any], offset_h: float = 0.0) -> dict[str, Any]:
        return {
            "t": parse_hhmm(event["time"]) + offset_h,
            "h": float(event["height"]),
            "type": event["type"],
            "time": event["time"],
        }

    core = sorted((as_extreme(e) for e in target_events), key=lambda e: e["t"])
    first_edge, last_edge = core[0], core[-1]

    iso = date.fromisoformat(target_date)
    prev_events = by_date.get((iso - timedelta(days=1)).isoformat())
    next_events = by_date.get((iso + timedelta(days=1)).isoformat())

    if prev_events:
        last_prev = max(prev_events, key=lambda e: parse_hhmm(e["time"]))
        left = as_extreme(last_prev, offset_h=-24.0)
    else:
        pad_type = _opposite(first_edge["type"])
        same = next((e for e in core if e["type"] == pad_type), first_edge)
        left = {"t": first_edge["t"] - SEMIDIURNAL_HALF_CYCLE_H, "h": same["h"], "type": pad_type, "time": None}

    if next_events:
        first_next = min(next_events, key=lambda e: parse_hhmm(e["time"]))
        right = as_extreme(first_next, offset_h=24.0)
    else:
        pad_type = _opposite(last_edge["type"])
        same = next((e for e in reversed(core) if e["type"] == pad_type), last_edge)
        right = {"t": last_edge["t"] + SEMIDIURNAL_HALF_CYCLE_H, "h": same["h"], "type": pad_type, "time": None}

    return [left, *core, right]


def tide_height_at(t: float, extremes: list[dict[str, Any]]) -> float:
    """Cosine-interpolated tide height at decimal hour `t`.

    Uses the prototype's formula between the two bracketing extremes:
    prev.h + (next.h - prev.h) * (1 - cos(pi * frac)) / 2. Outside the extreme
    span the nearest endpoint height is held flat.
    """
    if t <= extremes[0]["t"]:
        return extremes[0]["h"]
    if t >= extremes[-1]["t"]:
        return extremes[-1]["h"]
    for prev, nxt in zip(extremes, extremes[1:]):
        if prev["t"] <= t <= nxt["t"]:
            span = nxt["t"] - prev["t"]
            frac = 0.0 if span == 0 else (t - prev["t"]) / span
            return prev["h"] + (nxt["h"] - prev["h"]) * (1 - math.cos(math.pi * frac)) / 2
    return extremes[-1]["h"]


def html_output_path(package: dict[str, Any]) -> str:
    """Default output path: the verdict's Markdown report with .html swapped in.

    The filename is `conditions.report.filenames[verdict]` where `verdict` is
    `analysis.target_day.verdict`. Raises KeyError/TypeError when either piece
    is missing so the CLI can turn it into the soft-failure JSON.
    """
    verdict = package["analysis"]["target_day"]["verdict"]
    md_path = package["conditions"]["report"]["filenames"][verdict]
    if md_path.endswith(".md"):
        return md_path[: -len(".md")] + ".html"
    return md_path + ".html"


# ---------------------------------------------------------------------------
# Tide-curve SVG (pure: geometry only, colours come from page CSS classes)
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    """SVG coordinate with fixed precision so the golden file stays stable."""
    return f"{v:.2f}"


def _fmt_height(v: float) -> str:
    """Tide height label at tide-table readability (one decimal)."""
    return f"{v:.1f}"


def tide_svg(
    extremes: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    daylight_row: dict[str, Any] | None,
    unit_label: str,
) -> str:
    """Render the tide curve as an inline SVG string.

    Draws night shading outside first_light..last_light, accent-shaded session
    windows with their labels, the cosine curve with a soft fill, dotted and
    labelled tide extremes, and 0-24 h / tide-unit axes. Colours are applied by
    CSS classes (not inline), so one SVG renders correctly in both light and
    dark mode. The y axis is scaled to the data and supports negative heights.
    """
    heights = [e["h"] for e in extremes]
    min_h, max_h = min(heights), max(heights)
    rng = max_h - min_h or 1.0
    pad = rng * 0.15
    y_lo, y_hi = min_h - pad, max_h + pad

    def x(hour: float) -> float:
        return _PAD_L + (hour / 24.0) * _PLOT_W

    def y(height: float) -> float:
        return _PAD_T + (y_hi - height) / (y_hi - y_lo) * _PLOT_H

    parts: list[str] = [
        f'<svg class="tide-chart" viewBox="0 0 {_fmt(_SVG_W)} {_fmt(_SVG_H)}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Tide height across the day with shaded session windows">'
    ]

    plot_bottom = _PAD_T + _PLOT_H

    # Night shading: everything outside first_light..last_light of the target day.
    if daylight_row and "first_light" in daylight_row and "last_light" in daylight_row:
        first_h = parse_hhmm(daylight_row["first_light"])
        last_h = parse_hhmm(daylight_row["last_light"])
        parts.append(
            f'<rect class="tide-night" x="{_fmt(x(0))}" y="{_fmt(_PAD_T)}" '
            f'width="{_fmt(x(first_h) - x(0))}" height="{_fmt(_PLOT_H)}" />'
        )
        parts.append(
            f'<rect class="tide-night" x="{_fmt(x(last_h))}" y="{_fmt(_PAD_T)}" '
            f'width="{_fmt(x(24) - x(last_h))}" height="{_fmt(_PLOT_H)}" />'
        )

    # Session windows: accent-shaded bands with a label along the top.
    for window in windows or []:
        try:
            wx0 = x(parse_hhmm(window["from"]))
            wx1 = x(parse_hhmm(window["to"]))
        except (KeyError, ValueError):
            continue
        parts.append(
            f'<rect class="tide-window" x="{_fmt(wx0)}" y="{_fmt(_PAD_T)}" '
            f'width="{_fmt(wx1 - wx0)}" height="{_fmt(_PLOT_H)}" />'
        )
        label = window.get("label")
        if label:
            parts.append(
                f'<text class="tide-window-label" x="{_fmt((wx0 + wx1) / 2)}" '
                f'y="{_fmt(_PAD_T + 13)}" text-anchor="middle">{html.escape(str(label))}</text>'
            )

    # Axes: baseline plus 3-hourly x ticks and evenly spaced y ticks.
    parts.append(
        f'<line class="tide-axis" x1="{_fmt(_PAD_L)}" y1="{_fmt(plot_bottom)}" '
        f'x2="{_fmt(_PAD_L + _PLOT_W)}" y2="{_fmt(plot_bottom)}" />'
    )
    for hour in range(0, 25, 3):
        tx = x(hour)
        parts.append(
            f'<line class="tide-grid" x1="{_fmt(tx)}" y1="{_fmt(_PAD_T)}" '
            f'x2="{_fmt(tx)}" y2="{_fmt(plot_bottom)}" />'
        )
        parts.append(
            f'<text class="tide-axis-label" x="{_fmt(tx)}" y="{_fmt(plot_bottom + 16)}" '
            f'text-anchor="middle">{hour:02d}</text>'
        )
    for i in range(5):
        hv = y_lo + (y_hi - y_lo) * i / 4
        ty = y(hv)
        parts.append(
            f'<text class="tide-axis-label" x="{_fmt(_PAD_L - 8)}" y="{_fmt(ty + 3)}" '
            f'text-anchor="end">{_fmt_height(hv)}</text>'
        )
    parts.append(
        f'<text class="tide-axis-label" x="{_fmt(_PAD_L - 8)}" y="{_fmt(_PAD_T - 10)}" '
        f'text-anchor="end">{html.escape(unit_label)}</text>'
    )

    # The curve, sampled across the 24 h axis, plus a soft fill to the baseline.
    points = []
    for i in range(_SVG_SAMPLES + 1):
        hour = 24.0 * i / _SVG_SAMPLES
        points.append((x(hour), y(tide_height_at(hour, extremes))))
    line = " ".join(f"{_fmt(px)},{_fmt(py)}" for px, py in points)
    fill = (
        f"M {_fmt(points[0][0])},{_fmt(plot_bottom)} "
        + " ".join(f"L {_fmt(px)},{_fmt(py)}" for px, py in points)
        + f" L {_fmt(points[-1][0])},{_fmt(plot_bottom)} Z"
    )
    parts.append(f'<path class="tide-fill" d="{fill}" />')
    parts.append(f'<polyline class="tide-curve" points="{line}" />')

    # Extremes: only real target-day events (within the 0-24 h window, labelled).
    for e in extremes:
        if e["time"] is None or not (0.0 <= e["t"] <= 24.0):
            continue
        ex, ey = x(e["t"]), y(e["h"])
        parts.append(f'<circle class="tide-dot" cx="{_fmt(ex)}" cy="{_fmt(ey)}" r="3.5" />')
        label = f'{e["type"].capitalize()} {e["time"]} · {_fmt_height(e["h"])} {unit_label}'
        if ex < _PAD_L + 46:
            anchor, lx = "start", ex
        elif ex > _PAD_L + _PLOT_W - 46:
            anchor, lx = "end", ex
        else:
            anchor, lx = "middle", ex
        ly = ey - 9 if e["type"] == "high" else ey + 16
        parts.append(
            f'<text class="tide-extreme-label" x="{_fmt(lx)}" y="{_fmt(ly)}" '
            f'text-anchor="{anchor}">{html.escape(label)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


def _read_vendor(name: str) -> str:
    """Read a vendored Leaflet asset for inlining. Raises OSError when missing
    so the CLI degrades to the soft-failure JSON (exit 0)."""
    return (VENDOR_DIR / name).read_text(encoding="utf-8")


def _weekday(iso: str, table: list[str]) -> str:
    return table[date.fromisoformat(iso).weekday()]


def _verdict_chip(verdict: str, suffix: str | None = None, extra_class: str = "") -> str:
    """A verdict chip: emoji + label (optionally with an uppercase suffix).

    Colour comes from the `chip-<verdict>` CSS class; an unknown verdict falls
    back to the neutral `check` styling but keeps its own uppercased label.
    """
    emoji, label = VERDICT_DISPLAY.get(verdict, ("", str(verdict).upper()))
    text = f"{label} · {suffix}" if suffix else label
    kind = verdict if verdict in VERDICT_DISPLAY else "check"
    classes = f"chip chip-{kind}" + (f" {extra_class}" if extra_class else "")
    return f'<span class="{classes}">{emoji} {html.escape(text)}</span>'


PAGE_CSS = """
:root {
  --page: #eef1f4; --card: #ffffff; --ink: #1a2431; --muted: #5c6b7a;
  --border: #dbe2ea; --accent: #2b7cd3; --night: rgba(28, 42, 60, 0.09);
  --go: #16a35d; --check: #e8a41d; --skip: #d84a35;
  --overlay-bg: rgba(12, 20, 31, 0.82); --overlay-ink: #f4f7fb;
  --warn-bg: #fbecdf; --warn-border: #e6b8a6; --warn-ink: #8a3a25;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0d1520; --card: #14202e; --ink: #e8eef7; --muted: #93a4b7;
    --border: #1e2c3d; --accent: #4d9be6; --night: rgba(0, 0, 0, 0.34);
    --go: #16c86a; --check: #f2b62c; --skip: #e2543f;
    --overlay-bg: rgba(6, 12, 20, 0.86); --overlay-ink: #f4f7fb;
    --warn-bg: #2a1a16; --warn-border: #4a2c22; --warn-ink: #f2b8a4;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
.wrap { max-width: 1000px; margin: 0 auto; padding: 0 20px 48px; }
.hero { position: relative; height: 46vh; min-height: 320px; }
#surf-map { position: absolute; inset: 0; background: #cfd8e0; }
.overlay {
  position: absolute; left: 20px; bottom: 20px; max-width: 560px; z-index: 500;
  background: var(--overlay-bg); color: var(--overlay-ink);
  padding: 16px 20px; border-radius: 14px; backdrop-filter: blur(3px);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
}
.overlay h1 { margin: 8px 0 4px; font-size: 1.7rem; }
.overlay .sub { margin: 0; color: #cdd8e4; font-size: 0.98rem; }
.chip {
  display: inline-block; font-weight: 700; font-size: 0.82rem; letter-spacing: 0.04em;
  padding: 4px 11px; border-radius: 999px; color: #fff; white-space: nowrap;
}
.chip-go { background: var(--go); } .chip-check { background: var(--check); }
.chip-skip { background: var(--skip); }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 16px;
  padding: 22px 24px; margin-top: 24px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.card h2 { margin: 0 0 4px; font-size: 1.2rem; }
.card .sub { margin: 0 0 14px; color: var(--muted); font-size: 0.9rem; }
.tide-chart { width: 100%; height: auto; display: block; }
.tide-night { fill: var(--night); }
.tide-window { fill: var(--accent); opacity: 0.22; }
.tide-window-label { fill: var(--muted); font-size: 11px; font-weight: 600; }
.tide-grid { stroke: var(--border); stroke-width: 1; }
.tide-axis { stroke: var(--border); stroke-width: 1.5; }
.tide-axis-label { fill: var(--muted); font-size: 11px; }
.tide-fill { fill: var(--accent); opacity: 0.14; }
.tide-curve { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-linejoin: round; }
.tide-dot { fill: var(--accent); }
.tide-extreme-label { fill: var(--ink); font-size: 11px; font-weight: 600; }
.tide-note { color: var(--muted); }
.windows-list { list-style: none; padding: 0; margin: 12px 0 0; }
.windows-list li { padding: 6px 0; border-top: 1px solid var(--border); }
.week-row {
  display: flex; align-items: center; gap: 14px; padding: 11px 0;
  border-top: 1px solid var(--border);
}
.week-row:first-of-type { border-top: none; }
.week-day { width: 74px; font-weight: 700; flex: none; }
.week-chip { width: 148px; flex: none; }
.week-detail { color: var(--muted); font-size: 0.92rem; }
.chip.week-verdict { font-size: 0.72rem; padding: 3px 9px; }
.webcams { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; margin-top: 14px; }
.webcam {
  display: block; padding: 16px; border: 1px solid var(--border); border-radius: 12px;
  text-decoration: none; color: var(--ink); background: var(--page);
}
.webcam:hover { border-color: var(--accent); }
.webcam .name { font-weight: 700; }
.webcam .tag { font-size: 0.78rem; color: var(--muted); margin-top: 4px; }
.hazards-card { border-color: var(--warn-border); background: var(--warn-bg); }
.hazards-card h2, .hazards-card li { color: var(--warn-ink); }
.hazards-list { margin: 8px 0 0; padding-left: 20px; }
.hazards-list li { padding: 3px 0; }
.footer { margin-top: 28px; color: var(--muted); font-size: 0.86rem; text-align: center; }
.footer p { margin: 4px 0; }
@media (max-width: 800px) {
  .week-row { flex-wrap: wrap; }
  .week-chip { width: auto; }
  .week-detail { flex-basis: 100%; }
}
"""

# The map init is a few inline lines. Literal {z}/{x}/{y} in the tile template
# must survive verbatim, so lat/lon are substituted via sentinels (not .format
# or an f-string, which would choke on the braces).
MAP_JS = """(function () {
  var lat = __LAT__, lon = __LON__;
  var map = L.map('surf-map', { scrollWheelZoom: false }).setView([lat, lon], 14);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '\\u00a9 OpenStreetMap'
  }).addTo(map);
  L.circleMarker([lat, lon], {
    radius: 9, weight: 2, color: '#ffffff', fillColor: '#2b7cd3', fillOpacity: 0.9
  }).addTo(map);
})();"""


def render(package: dict[str, Any]) -> str:
    """Render the full self-contained HTML document for a data package.

    Reads Leaflet's vendored CSS/JS for inlining (raises OSError when missing,
    which the CLI turns into a soft failure). Every value drawn from the
    package is HTML-escaped; the only remote reference emitted is the OSM tile
    URL template inside the inlined map init.
    """
    conditions = package.get("conditions", {})
    analysis = package.get("analysis", {})
    spot_data = package.get("spot_data", {})

    target = analysis["target_day"]
    verdict = target["verdict"]
    target_date = target.get("date") or conditions.get("report", {}).get("target_date")

    spot = conditions.get("spot", {})
    spot_name = spot.get("name", "This spot")
    coords = spot.get("coordinates") or [0.0, 0.0]
    lat, lon = float(coords[0]), float(coords[1])

    units = conditions.get("units", {})
    tide_unit = units.get("tide_height", "m")

    leaflet_css = _read_vendor("leaflet.css")
    leaflet_js = _read_vendor("leaflet.js")

    esc = html.escape

    def attr(value: str) -> str:
        return html.escape(str(value), quote=True)

    # --- Map hero -----------------------------------------------------------
    weekday_upper = _weekday(target_date, WEEKDAY_FULL).upper() if target_date else ""
    hero_chip = _verdict_chip(verdict, weekday_upper or None)
    one_liner = target.get("one_liner")
    sub_html = f'<p class="sub">{esc(str(one_liner))}</p>' if one_liner else ""
    hero = (
        '<section class="hero">'
        '<div id="surf-map"></div>'
        f'<div class="overlay">{hero_chip}'
        f'<h1>{esc(str(spot_name))}</h1>{sub_html}</div>'
        "</section>"
    )

    # --- Tide curve + session windows --------------------------------------
    tides = conditions.get("tides", {})
    tide_days = tides.get("days")
    extremes = assemble_extremes(tide_days, target_date) if (tide_days and target_date) else []
    windows = target.get("windows") or []

    daylight_row = None
    for row in conditions.get("daylight", {}).get("days", []):
        if row.get("date") == target_date and "first_light" in row:
            daylight_row = row
            break

    tide_day_label = _weekday(target_date, WEEKDAY_FULL) if target_date else "Target day"
    source = tides.get("source")
    datum = tides.get("datum")
    # The datum belongs in the title (spec: "Saturday tide & session windows
    # (chart datum)"); CD gets its human-readable name, other datums keep
    # their code. Source attribution stays in the sub-paragraph.
    datum_phrase = ""
    if datum:
        datum_phrase = " (chart datum)" if datum == "CD" else f" ({datum})"
    tide_sub = f'<p class="sub">{esc(str(source))}</p>' if source else ""

    if extremes:
        tide_body = tide_svg(extremes, windows, daylight_row, tide_unit)
    else:
        note = tides.get("note") or "No automated tide data for this spot."
        items = "".join(
            f'<li>{esc(str(w.get("label", "Session")))}: '
            f'{esc(str(w.get("from", "?")))}–{esc(str(w.get("to", "?")))}</li>'
            for w in windows
        )
        windows_html = f'<ul class="windows-list">{items}</ul>' if items else ""
        tide_body = f'<p class="tide-note">{esc(str(note))}</p>{windows_html}'

    tide_section = (
        f'<section class="card"><h2>{esc(tide_day_label)} tide &amp; session windows'
        f"{esc(datum_phrase)}</h2>"
        f"{tide_sub}{tide_body}</section>"
    )

    # --- Week at a glance ---------------------------------------------------
    week = analysis.get("week") or []
    week_section = ""
    if week:
        rows = []
        for entry in week:
            d = entry.get("date")
            day_label = f"{_weekday(d, WEEKDAY_ABBR)} {date.fromisoformat(d).day}" if d else "?"
            chip = _verdict_chip(entry.get("verdict", "check"), extra_class="week-verdict")
            detail_bits = [str(entry[k]) for k in ("swell", "wind") if entry.get(k)]
            detail = " · ".join(detail_bits)
            if entry.get("why"):
                detail = f"{detail} - {entry['why']}" if detail else str(entry["why"])
            rows.append(
                f'<div class="week-row"><div class="week-day">{esc(day_label)}</div>'
                f'<div class="week-chip">{chip}</div>'
                f'<div class="week-detail">{esc(detail)}</div></div>'
            )
        week_section = (
            '<section class="card"><h2>Week at a glance</h2>' + "".join(rows) + "</section>"
        )

    # --- Webcams ------------------------------------------------------------
    webcams = spot_data.get("webcams") or []
    webcam_section = ""
    if webcams:
        cards = []
        for cam in webcams:
            url = cam.get("url", "")
            tag = "Free" if cam.get("free") else "Paywalled"
            cards.append(
                f'<a class="webcam" href="{attr(url)}" target="_blank" rel="noopener noreferrer">'
                f'<div class="name">{esc(str(cam.get("name", "Webcam")))}</div>'
                f'<div class="tag">{esc(tag)}</div></a>'
            )
        webcam_section = (
            '<section class="card"><h2>Webcams</h2>'
            f'<div class="webcams">{"".join(cards)}</div></section>'
        )

    # --- Hazards (always visible; never silently empty) ---------------------
    hazards = spot_data.get("hazards") or []
    if hazards:
        haz_items = "".join(f"<li>{esc(str(h))}</li>" for h in hazards)
        haz_body = f'<ul class="hazards-list">{haz_items}</ul>'
    else:
        haz_body = (
            "<p>No hazards documented for this spot. "
            "Verify from the beach before paddling out.</p>"
        )
    hazards_section = f'<section class="card hazards-card"><h2>Hazards</h2>{haz_body}</section>'

    # --- Footer -------------------------------------------------------------
    md_name = ""
    filenames = conditions.get("report", {}).get("filenames")
    if isinstance(filenames, dict) and verdict in filenames:
        md_name = Path(filenames[verdict]).name
    md_line = f'<p>Full report: {esc(md_name)}</p>' if md_name else ""
    footer = (
        '<footer class="footer">'
        "<p>AI-generated report. Verify conditions on-site; if in doubt, don't paddle out.</p>"
        f"{md_line}</footer>"
    )

    map_js = MAP_JS.replace("__LAT__", json.dumps(lat)).replace("__LON__", json.dumps(lon))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(str(spot_name))} surf report</title>\n"
        f"<style>{leaflet_css}</style>\n"
        f"<style>{PAGE_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{hero}\n"
        '<main class="wrap">\n'
        f"{tide_section}\n"
        f"{week_section}\n"
        f"{webcam_section}\n"
        f"{hazards_section}\n"
        f"{footer}\n"
        "</main>\n"
        f"<script>{leaflet_js}</script>\n"
        f"<script>{map_js}</script>\n"
        "</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SOFT_FAIL_NOTE = (
    "The Markdown report remains the canonical output and can be opened directly. "
    "The HTML view is an optional convenience."
)


@click.command()
@click.option("--data", default=None, help="Path to the run's data-package JSON (Phase 5A shape). Required.")
@click.option(
    "--out",
    default=None,
    help="Override the output HTML path. Defaults to the verdict's Markdown report with a .html extension.",
)
def cli(data: str | None, out: str | None):
    """Render a self-contained HTML surf report and print {"html_path": ...}."""
    # A missing --data is an invalid argument (exit 1), per the Tool Contract.
    if not data:
        click.echo(json.dumps({"error": "--data is required (path to the data-package JSON)"}))
        sys.exit(1)

    try:
        with open(data, encoding="utf-8") as f:
            package = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        click.echo(json.dumps({"error": f"Cannot read data package {data}: {e}", "note": SOFT_FAIL_NOTE}))
        sys.exit(0)

    try:
        out_path = out or html_output_path(package)
        html_doc = render(package)
    except (KeyError, TypeError, ValueError, OSError) as e:
        click.echo(json.dumps({"error": f"Cannot render report: {e}", "note": SOFT_FAIL_NOTE}))
        sys.exit(0)

    try:
        path = Path(out_path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_doc, encoding="utf-8")
    except OSError as e:
        click.echo(json.dumps({"error": f"Cannot write HTML to {out_path}: {e}", "note": SOFT_FAIL_NOTE}))
        sys.exit(0)

    click.echo(json.dumps({"html_path": out_path}))


if __name__ == "__main__":
    cli()
