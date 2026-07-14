#!/usr/bin/env python3
"""Deterministic HTML surf-report renderer for the spot-researcher skill.

Takes a Phase 5A data-package JSON (the fetch_conditions.py payload plus the
skill's spot_data and analysis) and writes one self-contained tabbed HTML
Dashboard plus a paired flat Markdown twin sharing its basename.

The Dashboard (`--mode dashboard`, the default per-spot mode) is one file with a
four-button tab bar (Today / Forecast / Windows / Spot info), four panels, and a
small inline script that shows one panel at a time on click (no refetch; the
opening tab is chosen by a URL fragment appended when the file is opened). The
Today panel carries the former single-mode content: a Leaflet map hero with the
verdict chip and the Python-generated tide curve with the aligned hourly strip,
both clipped to the target day's daylight. The Forecast panel is interactive: a
Week-at-a-glance 7-day tide overview (each day clipped to its own daylight
window, with the mid-tide two-tone split) above a By-day list of day-selector
rows whose click swaps in that day's full Today-style chart. Windows / Spot info
are placeholders filled by later slices.

`--mode week` renders the separate multi-spot Week planner (unchanged).

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

# Compact chip labels for the space-tight Forecast day-selector rows (GO / CHECK
# / SKIP), where "WORTH A CHECK" would not fit the chip column.
VERDICT_SHORT = {"go": "GO", "check": "CHECK", "skip": "SKIP"}

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

# Hourly strip: a band drawn below the tide plot (shares the 0-24 h x axis) that
# shows per-hour swell, wind, period and quality for the target day's daylight
# hours. Extends the viewBox height only; the tide plot geometry is unchanged.
_STRIP_H = 116.0
_STRIP_BAR_MAX = 42.0  # tallest swell bar, in px above its baseline

# Week (Forecast) "Week at a glance" overview geometry: one compressed,
# daylight-clipped column per forecast day drawn into the same _SVG_W-wide
# viewBox. Night hours between days are collapsed into the gutter between
# columns (never drawn), Windguru style. The x span reuses _PAD_L/_PAD_R so the
# chart lines up with the page. The chart is ~half the single-day height; the
# top pad holds the weekday labels drawn above each column, the bottom pad the
# per-day hour ticks and high/low tide times.
_WEEK_SVG_H = 132.0
_WEEK_PAD_T = 34.0
_WEEK_PAD_B = 34.0
_WEEK_PLOT_H = _WEEK_SVG_H - _WEEK_PAD_T - _WEEK_PAD_B
_WEEK_DAY_GAP = 8.0  # blank gutter each column, i.e. the collapsed night
_WEEK_DAY_SAMPLES = 48  # curve samples across each day's daylight window


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


def dashboard_output_path(package: dict[str, Any]) -> str:
    """Default dashboard output path: reports/<date>-<slug>-dashboard.html.

    Derived from `conditions.report.filenames[verdict]` (e.g.
    reports/2026-07-11-mundaka-go.md) by dropping the `-<verdict>.md` suffix and
    appending `-dashboard.html`, so the name carries the target date and spot
    slug but no verdict slug: one stable dashboard per spot per day that a re-run
    overwrites. Raises KeyError/TypeError when the verdict or filenames are
    missing so the CLI can turn it into the soft-failure JSON.
    """
    verdict = package["analysis"]["target_day"]["verdict"]
    md_path = package["conditions"]["report"]["filenames"][verdict]
    stem = md_path[: -len(".md")] if md_path.endswith(".md") else md_path
    suffix = f"-{verdict}"
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return stem + "-dashboard.html"


def week_output_path(package: dict[str, Any]) -> str:
    """Default week-mode output path: reports/{week.start}-week.html.

    Raises KeyError/TypeError when week.start is missing so the CLI can turn it
    into the soft-failure JSON.
    """
    start = package["week"]["start"]
    if not start:
        raise KeyError("week.start")
    return f"reports/{start}-week.html"


# ---------------------------------------------------------------------------
# Tide-curve SVG (pure: geometry only, colours come from page CSS classes)
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    """SVG coordinate with fixed precision so the golden file stays stable."""
    return f"{v:.2f}"


def _fmt_height(v: float) -> str:
    """Tide height label at tide-table readability (one decimal)."""
    return f"{v:.1f}"


def _fmt_label(v: float) -> str:
    """Compact strip label: whole number when integral, else one decimal."""
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def _tide_y_bounds(heights: list[float]) -> tuple[float, float]:
    """The (y_lo, y_hi) tide-height range with a 15% headroom pad on each side.

    Shared by the single-day (`tide_svg`) and 7-day (`week_tide_svg`) charts so
    both scale their y axis identically; supports negative heights.
    """
    min_h, max_h = min(heights), max(heights)
    pad = ((max_h - min_h) or 1.0) * 0.15
    return min_h - pad, max_h + pad


def _polyline(points: list[tuple[float, float]]) -> str:
    """The SVG polyline `points` attribute string for a sampled curve."""
    return " ".join(f"{_fmt(px)},{_fmt(py)}" for px, py in points)


def _mid_level(heights: list[float]) -> float:
    """The mid-tide reference level: halfway between the lowest and highest."""
    return (min(heights) + max(heights)) / 2


def _mid_clip_defs(
    id_prefix: str, left: float, top: float, width: float, y_mid: float, bottom: float
) -> tuple[str, str, str]:
    """Two clipPaths splitting a plot at the mid-tide line, plus their ids.

    The `high` band is everything above the mid line (top..y_mid), the `low`
    band everything below it (y_mid..bottom). Ids are prefixed by `id_prefix`
    so the many charts in one Dashboard document (Today plus every per-day
    detail chart) never collide, a `url(#..)` reference otherwise resolving to
    the first (possibly hidden) match and dropping a colour band.
    """
    hi, lo = f"{id_prefix}-tide-hi", f"{id_prefix}-tide-lo"
    defs = (
        f'<defs>'
        f'<clipPath id="{hi}"><rect x="{_fmt(left)}" y="{_fmt(top)}" '
        f'width="{_fmt(width)}" height="{_fmt(y_mid - top)}" /></clipPath>'
        f'<clipPath id="{lo}"><rect x="{_fmt(left)}" y="{_fmt(y_mid)}" '
        f'width="{_fmt(width)}" height="{_fmt(bottom - y_mid)}" /></clipPath>'
        f'</defs>'
    )
    return defs, hi, lo


def _mid_band_paths(points: list[tuple[float, float]], y_mid: float, hi_id: str, lo_id: str) -> str:
    """The two-tone fill: the band between the curve and the mid line, drawn
    twice and clipped to the high/low bands so it tints apart above and below.
    """
    band = (
        f"M {_fmt(points[0][0])},{_fmt(y_mid)} "
        + " ".join(f"L {_fmt(px)},{_fmt(py)}" for px, py in points)
        + f" L {_fmt(points[-1][0])},{_fmt(y_mid)} Z"
    )
    return (
        f'<path class="tide-fill-high" clip-path="url(#{hi_id})" d="{band}" />'
        f'<path class="tide-fill-low" clip-path="url(#{lo_id})" d="{band}" />'
    )


def tide_svg(
    extremes: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    daylight_row: dict[str, Any] | None,
    unit_label: str,
    hours: list[dict[str, Any]] | None = None,
    swell_unit: str = "m",
    wind_unit: str = "kn",
    id_prefix: str = "t",
) -> str:
    """Render the tide curve as an inline SVG string.

    Draws night shading outside first_light..last_light, accent-shaded session
    windows with their labels, the cosine curve with a two-tone mid-tide split
    fill (see the Mid-tide split vocab: high-water above the mid line, low-water
    below), a per-hour x-axis (a light gridline every hour, every second hour
    stronger), dotted and labelled tide extremes, and tide-unit y axes. Colours
    are applied by CSS classes (not inline), so one SVG renders correctly in
    both light and dark mode. The y axis is scaled to the data and supports
    negative heights.

    When `hours` is given (per-hour dicts with swell/wind/period/quality, as
    produced by fetch_conditions' marine.days[].hours), an aligned strip is
    drawn below the plot sharing the same x(hour) mapping, so each hour's swell
    bar and wind arrow line up under the tide curve and session bands. The strip
    rows are labelled on the left (Swell / Period / Wind with their units).

    `id_prefix` prefixes the mid-tide split's clipPath ids so many charts (the
    Today chart plus every per-day Forecast detail chart) coexist in one
    document without their `url(#..)` references colliding.
    """
    y_lo, y_hi = _tide_y_bounds([e["h"] for e in extremes])

    # X domain: dawn to evening glass-off (first_light..last_light) when the
    # daylight row is known, so the chart spans only the surfable day instead of
    # midnight-to-midnight; otherwise fall back to the full 0-24 h axis.
    t0, t1 = 0.0, 24.0
    if daylight_row and "first_light" in daylight_row and "last_light" in daylight_row:
        try:
            t0 = parse_hhmm(daylight_row["first_light"])
            t1 = parse_hhmm(daylight_row["last_light"])
        except (KeyError, ValueError):
            t0, t1 = 0.0, 24.0
    cropped = (t0, t1) != (0.0, 24.0)
    span = (t1 - t0) or 24.0

    def x(hour: float) -> float:
        return _PAD_L + ((hour - t0) / span) * _PLOT_W

    def y(height: float) -> float:
        return _PAD_T + (y_hi - height) / (y_hi - y_lo) * _PLOT_H

    # Strip covers only the hours inside the plotted window so bars stay on-chart.
    strip_hours = [
        h for h in (hours or []) if h.get("time") and t0 <= int(h["time"][:2]) <= t1
    ]
    total_h = _SVG_H + _STRIP_H if strip_hours else _SVG_H

    parts: list[str] = [
        f'<svg class="tide-chart" viewBox="0 0 {_fmt(_SVG_W)} {_fmt(total_h)}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Tide height across the day with shaded session windows'
        f'{" and an hourly surf strip" if strip_hours else ""}">'
    ]

    plot_bottom = _PAD_T + _PLOT_H

    # Session windows: accent-shaded bands with a label along the top, clamped to
    # the plotted window so a session running past dawn/dusk stays on-chart.
    for window in windows or []:
        try:
            wx0 = x(parse_hhmm(window["from"]))
            wx1 = x(parse_hhmm(window["to"]))
        except (KeyError, ValueError):
            continue
        wx0 = max(wx0, _PAD_L)
        wx1 = min(wx1, _PAD_L + _PLOT_W)
        if wx1 <= wx0:
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

    # Axes: baseline, then a per-hour x grid (a light vertical gridline every
    # hour, every second hour stronger), evenly spaced y ticks.
    parts.append(
        f'<line class="tide-axis" x1="{_fmt(_PAD_L)}" y1="{_fmt(plot_bottom)}" '
        f'x2="{_fmt(_PAD_L + _PLOT_W)}" y2="{_fmt(plot_bottom)}" />'
    )
    for hour in range(math.ceil(t0), int(t1) + 1):
        # Skip hours hugging a cropped endpoint so their labels don't collide
        # with the dawn/dusk time labels drawn there.
        if cropped and (hour - t0 < 0.6 or t1 - hour < 0.6):
            continue
        tx = x(hour)
        grid_cls = "tide-hgrid tide-hgrid-major" if hour % 2 == 0 else "tide-hgrid"
        parts.append(
            f'<line class="{grid_cls}" x1="{_fmt(tx)}" y1="{_fmt(_PAD_T)}" '
            f'x2="{_fmt(tx)}" y2="{_fmt(plot_bottom)}" />'
        )
        parts.append(
            f'<text class="tide-axis-label" x="{_fmt(tx)}" y="{_fmt(plot_bottom + 16)}" '
            f'text-anchor="middle">{hour:02d}</text>'
        )
    # Dawn / evening glass-off endpoints get their exact times at the axis ends.
    if cropped:
        parts.append(
            f'<text class="tide-axis-label" x="{_fmt(_PAD_L)}" y="{_fmt(plot_bottom + 16)}" '
            f'text-anchor="start">{html.escape(str(daylight_row["first_light"]))}</text>'
        )
        parts.append(
            f'<text class="tide-axis-label" x="{_fmt(_PAD_L + _PLOT_W)}" '
            f'y="{_fmt(plot_bottom + 16)}" '
            f'text-anchor="end">{html.escape(str(daylight_row["last_light"]))}</text>'
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

    # The curve, sampled across the plotted window, with the two-tone mid-tide
    # split fill (high-water above the mid line, low-water below) and a dashed
    # mid-tide reference line spanning the plot.
    y_mid = y(_mid_level([e["h"] for e in extremes]))
    points = []
    for i in range(_SVG_SAMPLES + 1):
        hour = t0 + span * i / _SVG_SAMPLES
        points.append((x(hour), y(tide_height_at(hour, extremes))))
    defs, hi_id, lo_id = _mid_clip_defs(id_prefix, _PAD_L, _PAD_T, _PLOT_W, y_mid, plot_bottom)
    parts.append(defs)
    parts.append(_mid_band_paths(points, y_mid, hi_id, lo_id))
    parts.append(
        f'<line class="tide-mid" x1="{_fmt(_PAD_L)}" y1="{_fmt(y_mid)}" '
        f'x2="{_fmt(_PAD_L + _PLOT_W)}" y2="{_fmt(y_mid)}" />'
    )
    parts.append(f'<polyline class="tide-curve" points="{_polyline(points)}" />')

    # Extremes: only real events inside the plotted window (dawn..dusk), labelled.
    for e in extremes:
        if e["time"] is None or not (t0 <= e["t"] <= t1):
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

    # --- Hourly strip: aligned under the tide plot, same x(hour) mapping -----
    if strip_hours:
        strip_top = _SVG_H
        bar_base = strip_top + 50.0     # swell bars grow upward from here
        period_y = strip_top + 64.0     # period labels just under the baseline
        wind_y = strip_top + 84.0       # wind arrow centres
        wind_lbl_y = strip_top + 104.0  # wind speed labels

        def _num(v: Any) -> float | None:
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        sw_vals = [s for s in (_num(h.get("swell_height")) for h in strip_hours) if s is not None]
        sw_max = max(sw_vals) if sw_vals else 0.0
        col_w = _PLOT_W / 24.0
        bar_w = max(4.0, min(col_w * 0.55, 20.0))
        # Per-hour numeric labels stay legible up to ~16 columns; above that
        # (long summer daylight) show every other hour so text does not collide.
        label_step = 1 if len(strip_hours) <= 16 else 2

        parts.append(
            f'<line class="strip-sep" x1="{_fmt(_PAD_L)}" y1="{_fmt(strip_top + 8)}" '
            f'x2="{_fmt(_PAD_L + _PLOT_W)}" y2="{_fmt(strip_top + 8)}" />'
        )
        # Row labels on the left, stacked and carrying their units, so the
        # crowded right-edge caption is gone (each row reads Swell / Period /
        # Wind with its unit).
        parts.append(
            f'<text class="strip-row-label" x="4.00" y="{_fmt(bar_base - 14)}" '
            f'text-anchor="start">Swell ({html.escape(swell_unit)})</text>'
        )
        parts.append(
            f'<text class="strip-row-label" x="4.00" y="{_fmt(period_y)}" '
            f'text-anchor="start">Period (s)</text>'
        )
        parts.append(
            f'<text class="strip-row-label" x="4.00" y="{_fmt(wind_y + 4)}" '
            f'text-anchor="start">Wind ({html.escape(wind_unit)})</text>'
        )

        for idx, h in enumerate(strip_hours):
            hr = int(h["time"][:2])
            cx = x(hr)  # centre on the exact hour so bars align with tide ticks
            show = idx % label_step == 0
            sh = _num(h.get("swell_height"))
            quality = h.get("quality")
            rating = quality.get("rating") if isinstance(quality, dict) else None
            qcls = {
                "epic": "q-go", "good": "q-go", "fair": "q-check",
                "poor": "q-skip", "flat": "q-flat",
            }.get(rating, "q-none")

            # Swell bar: height encodes swell size, colour encodes quality.
            if sh is not None and sw_max > 0:
                bh = max(2.0, (sh / sw_max) * _STRIP_BAR_MAX)
                parts.append(
                    f'<rect class="strip-bar {qcls}" x="{_fmt(cx - bar_w / 2)}" '
                    f'y="{_fmt(bar_base - bh)}" width="{_fmt(bar_w)}" height="{_fmt(bh)}" rx="1.5" />'
                )
                if show:
                    parts.append(
                        f'<text class="strip-val" x="{_fmt(cx)}" y="{_fmt(bar_base - bh - 4)}" '
                        f'text-anchor="middle">{_fmt_height(sh)}</text>'
                    )

            # Swell period, just below the baseline.
            per = _num(h.get("swell_period_s"))
            if per is not None and show:
                parts.append(
                    f'<text class="strip-sub" x="{_fmt(cx)}" y="{_fmt(period_y)}" '
                    f'text-anchor="middle">{_fmt_label(per)}s</text>'
                )

            # Wind arrow points downwind (from-direction + 180), tinted by type.
            wdeg = _num(h.get("wind_direction_deg"))
            if wdeg is not None:
                angle = (wdeg + 180.0) % 360.0
                arrow = (
                    f'M {_fmt(cx)},{_fmt(wind_y - 7)} L {_fmt(cx)},{_fmt(wind_y + 7)} '
                    f'M {_fmt(cx)},{_fmt(wind_y - 7)} L {_fmt(cx - 3.5)},{_fmt(wind_y - 2)} '
                    f'M {_fmt(cx)},{_fmt(wind_y - 7)} L {_fmt(cx + 3.5)},{_fmt(wind_y - 2)}'
                )
                wtype = h.get("wind_type")
                wcls = (
                    "wind-off" if wtype == "offshore"
                    else "wind-on" if wtype == "onshore"
                    else "wind-cross"
                )
                parts.append(
                    f'<path class="strip-arrow {wcls}" d="{arrow}" '
                    f'transform="rotate({_fmt(angle)} {_fmt(cx)} {_fmt(wind_y)})" />'
                )
            spd = _num(h.get("wind_speed"))
            if spd is not None and show:
                parts.append(
                    f'<text class="strip-sub" x="{_fmt(cx)}" y="{_fmt(wind_lbl_y)}" '
                    f'text-anchor="middle">{_fmt_label(spd)}</text>'
                )

    parts.append("</svg>")
    return "".join(parts)


def week_tide_svg(days: list[dict[str, Any]], unit_label: str) -> str:
    """Render the "Week at a glance" overview: one daylight-clipped column per day.

    `days` is the per-day list resolved by `_forecast_view`: each entry carries
    `date`, `label` (weekday abbreviation), `extremes` (the `assemble_extremes`
    output for that day) and `daylight` (`{first_light, last_light}` or None).
    Every day gets an equal-width column; inside it the curve is sampled only
    across that day's first_light..last_light window (a full 0-24 h fallback when
    daylight is absent), so the night hours between days are collapsed into the
    gutter and never drawn (Windguru style). The y scale is shared across all
    days so heights are comparable, reusing `tide_height_at` for interpolation.

    The overview is ~half the single-day height and carries the same mid-tide
    split as the per-day charts: a dashed mid-tide reference line across the
    width and a two-tone fill (high-water above it, low-water below). Weekday
    labels sit above each column; hour ticks and per-day high/low tide times sit
    below the curve; thin dividers mark the collapsed night between days. The
    same CSS classes as `tide_svg` are reused so one SVG renders in both themes.
    """
    days = [d for d in days if d.get("extremes")]
    if not days:
        return ""

    # A single y scale across every day's extremes keeps columns comparable.
    all_heights = [e["h"] for d in days for e in d["extremes"]]
    y_lo, y_hi = _tide_y_bounds(all_heights)
    min_h, max_h = min(all_heights), max(all_heights)
    mid = _mid_level(all_heights)

    n = len(days)
    plot_left = _PAD_L
    plot_right = _SVG_W - _PAD_R
    plot_w = plot_right - plot_left
    col_w = plot_w / n
    plot_bottom = _WEEK_PAD_T + _WEEK_PLOT_H

    def y(height: float) -> float:
        return _WEEK_PAD_T + (y_hi - height) / (y_hi - y_lo) * _WEEK_PLOT_H

    y_mid = y(mid)

    def day_window(day: dict[str, Any]) -> tuple[float, float]:
        dl = day.get("daylight") or {}
        try:
            return parse_hhmm(dl["first_light"]), parse_hhmm(dl["last_light"])
        except (KeyError, ValueError, TypeError):
            return 0.0, 24.0

    parts: list[str] = [
        f'<svg class="tide-chart tide-week" viewBox="0 0 {_fmt(_SVG_W)} {_fmt(_WEEK_SVG_H)}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Seven-day tide overview, each day clipped to its daylight hours">'
    ]

    # Mid-tide split clip bands, shared by every day's fill (defined once).
    defs, hi_id, lo_id = _mid_clip_defs("wk", plot_left, _WEEK_PAD_T, plot_w, y_mid, plot_bottom)
    parts.append(defs)

    # Shared axis: baseline, low/mid/high height ticks, one unit label, and the
    # dashed mid-tide reference line spanning the whole width.
    parts.append(
        f'<line class="tide-axis" x1="{_fmt(plot_left)}" y1="{_fmt(plot_bottom)}" '
        f'x2="{_fmt(plot_right)}" y2="{_fmt(plot_bottom)}" />'
    )
    for hv in (min_h, mid, max_h):
        parts.append(
            f'<text class="tide-axis-label" x="{_fmt(plot_left - 8)}" y="{_fmt(y(hv) + 3)}" '
            f'text-anchor="end">{_fmt_height(hv)}</text>'
        )
    parts.append(
        f'<text class="tide-axis-label" x="{_fmt(plot_left - 8)}" y="{_fmt(_WEEK_PAD_T - 10)}" '
        f'text-anchor="end">{html.escape(unit_label)}</text>'
    )
    parts.append(
        f'<line class="tide-mid" x1="{_fmt(plot_left)}" y1="{_fmt(y_mid)}" '
        f'x2="{_fmt(plot_right)}" y2="{_fmt(y_mid)}" />'
    )

    for i, day in enumerate(days):
        t0, t1 = day_window(day)
        span = (t1 - t0) or 24.0
        seg_left = plot_left + i * col_w + _WEEK_DAY_GAP / 2
        seg_right = plot_left + (i + 1) * col_w - _WEEK_DAY_GAP / 2
        seg_w = seg_right - seg_left
        extremes = day["extremes"]

        def x(hour: float, seg_left: float = seg_left, seg_w: float = seg_w,
              t0: float = t0, span: float = span) -> float:
            return seg_left + ((hour - t0) / span) * seg_w

        parts.append(
            f'<g class="tide-week-day" data-day="{html.escape(str(day.get("date", "")))}">'
        )

        # Weekday label above the column.
        parts.append(
            f'<text class="tide-week-label" x="{_fmt((seg_left + seg_right) / 2)}" '
            f'y="{_fmt(_WEEK_PAD_T - 21)}" text-anchor="middle">'
            f'{html.escape(str(day.get("label", "")))}</text>'
        )

        # A divider before every column but the first marks the collapsed night.
        if i > 0:
            div_x = plot_left + i * col_w
            parts.append(
                f'<line class="tide-week-divider" x1="{_fmt(div_x)}" y1="{_fmt(_WEEK_PAD_T)}" '
                f'x2="{_fmt(div_x)}" y2="{_fmt(plot_bottom)}" />'
            )

        # Curve + two-tone mid-tide fill, sampled only across this day's daylight.
        points = [
            (x(t0 + span * s / _WEEK_DAY_SAMPLES), y(tide_height_at(t0 + span * s / _WEEK_DAY_SAMPLES, extremes)))
            for s in range(_WEEK_DAY_SAMPLES + 1)
        ]
        parts.append(_mid_band_paths(points, y_mid, hi_id, lo_id))
        parts.append(f'<polyline class="tide-curve" points="{_polyline(points)}" />')

        # Hour ticks every 4 h inside the daylight window, labelled below the axis.
        for hr in range(math.ceil(t0 / 4.0) * 4, int(t1) + 1, 4):
            tx = x(hr)
            parts.append(
                f'<line class="tide-week-tick" x1="{_fmt(tx)}" y1="{_fmt(plot_bottom)}" '
                f'x2="{_fmt(tx)}" y2="{_fmt(plot_bottom + 4)}" />'
            )
            parts.append(
                f'<text class="tide-week-hour" x="{_fmt(tx)}" y="{_fmt(plot_bottom + 14)}" '
                f'text-anchor="middle">{hr:02d}</text>'
            )

        # High/low tide dots with their times (high time above the dot, low below).
        for e in extremes:
            if e["time"] is None or not (t0 <= e["t"] <= t1):
                continue
            ex, ey = x(e["t"]), y(e["h"])
            parts.append(f'<circle class="tide-dot" cx="{_fmt(ex)}" cy="{_fmt(ey)}" r="2.2" />')
            ly = max(_WEEK_PAD_T + 8, ey - 6) if e["type"] == "high" else min(plot_bottom - 3, ey + 12)
            parts.append(
                f'<text class="tide-week-time" x="{_fmt(ex)}" y="{_fmt(ly)}" '
                f'text-anchor="middle">{html.escape(str(e["time"]))}</text>'
            )

        parts.append("</g>")

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


def _verdict_chip(verdict: str, suffix: str | None = None, extra_class: str = "", short: bool = False) -> str:
    """A verdict chip: emoji + label (optionally with an uppercase suffix).

    Colour comes from the `chip-<verdict>` CSS class; an unknown verdict falls
    back to the neutral `check` styling but keeps its own uppercased label. When
    `short` is set the compact label (GO / CHECK / SKIP) is used, for the tight
    Forecast day-selector chips.
    """
    emoji, label = VERDICT_DISPLAY.get(verdict, ("", str(verdict).upper()))
    if short:
        label = VERDICT_SHORT.get(verdict, label)
    text = f"{label} · {suffix}" if suffix else label
    kind = verdict if verdict in VERDICT_DISPLAY else "check"
    classes = f"chip chip-{kind}" + (f" {extra_class}" if extra_class else "")
    return f'<span class="{classes}">{emoji} {html.escape(text)}</span>'


# The dark palette, declared once and spliced into both dark selectors below
# (the explicit [data-theme="dark"] override and the prefers-color-scheme
# fallback) so the two can never drift. Kept as a sentinel-replaced constant
# rather than an f-string so the surrounding CSS stays readable as plain CSS.
_DARK_VARS = (
    "--page: #0d1520; --card: #14202e; --ink: #e8eef7; --muted: #93a4b7;\n"
    "  --border: #1e2c3d; --accent: #4d9be6; --night: rgba(0, 0, 0, 0.34);\n"
    "  --go: #16c86a; --check: #f2b62c; --skip: #e2543f;\n"
    "  --tide-high: #4d9be6; --tide-low: #2fd39a;\n"
    "  --overlay-bg: rgba(6, 12, 20, 0.86); --overlay-ink: #f4f7fb;\n"
    "  --warn-bg: #2a1a16; --warn-border: #4a2c22; --warn-ink: #f2b8a4;"
)

PAGE_CSS = """
:root {
  --page: #eef1f4; --card: #ffffff; --ink: #1a2431; --muted: #5c6b7a;
  --border: #dbe2ea; --accent: #2b7cd3; --night: rgba(28, 42, 60, 0.09);
  --go: #16a35d; --check: #e8a41d; --skip: #d84a35;
  --tide-high: #2b7cd3; --tide-low: #1fa971;
  --overlay-bg: rgba(12, 20, 31, 0.82); --overlay-ink: #f4f7fb;
  --warn-bg: #fbecdf; --warn-border: #e6b8a6; --warn-ink: #8a3a25;
}
/* Dark palette. Applied when the reader explicitly picks dark
   (data-theme="dark"), and, for the default "auto" choice (data-theme unset or
   "auto"), when the system prefers dark. Explicit "light" always wins. */
:root[data-theme="dark"] {
  __DARK_VARS__
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]):not([data-theme="dark"]) {
    __DARK_VARS__
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
.wrap { max-width: none; margin: 0; padding: 22px 4% 48px; }
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
.tide-fill-high { fill: var(--tide-high); opacity: 0.28; }
.tide-fill-low { fill: var(--tide-low); opacity: 0.28; }
.tide-mid { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 4 4; opacity: 0.55; }
.tide-hgrid { stroke: var(--border); stroke-width: 0.5; opacity: 0.4; }
.tide-hgrid-major { stroke-width: 0.9; opacity: 0.8; }
.tide-curve { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-linejoin: round; }
.tide-dot { fill: var(--accent); }
.tide-extreme-label { fill: var(--ink); font-size: 11px; font-weight: 600; }
.strip-sep { stroke: var(--border); stroke-width: 1; }
.strip-row-label { fill: var(--muted); font-size: 11px; font-weight: 600; }
.strip-val { fill: var(--ink); font-size: 10px; font-weight: 600; }
.strip-sub { fill: var(--muted); font-size: 10px; }
.strip-unit { fill: var(--muted); font-size: 9px; }
.strip-bar.q-go { fill: var(--go); }
.strip-bar.q-check { fill: var(--check); }
.strip-bar.q-skip { fill: var(--skip); }
.strip-bar.q-flat { fill: var(--muted); opacity: 0.5; }
.strip-bar.q-none { fill: var(--accent); }
.strip-arrow { fill: none; stroke-width: 1.6; stroke-linecap: round; }
.strip-arrow.wind-off { stroke: var(--go); }
.strip-arrow.wind-on { stroke: var(--skip); }
.strip-arrow.wind-cross { stroke: var(--muted); }
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
""".replace("__DARK_VARS__", _DARK_VARS)

# Week-planner-only styling, appended after PAGE_CSS. Reuses the palette, chip
# styles, cards, and dark mode above; adds the ranking rows and per-spot cards.
WEEK_CSS = """
.hero-week .overlay { max-width: 640px; }
.hero-week .overlay .range { margin: 8px 0 2px; font-size: 1.5rem; font-weight: 700; }
.hero-week .overlay .count { margin: 0; color: #cdd8e4; font-size: 0.98rem; }
.rank-row {
  display: grid; grid-template-columns: 40px 92px 1fr auto; gap: 6px 14px;
  align-items: baseline; padding: 12px 0; border-top: 1px solid var(--border);
}
.rank-row:first-of-type { border-top: none; }
.rank-num { font-weight: 800; font-size: 1.1rem; color: var(--muted); }
.rank-when { font-weight: 700; }
.rank-spot { font-weight: 700; }
.rank-window { color: var(--muted); font-size: 0.92rem; }
.rank-detail { grid-column: 3 / 5; color: var(--muted); font-size: 0.92rem; margin-top: 2px; }
.rank-why { grid-column: 3 / 5; font-size: 0.92rem; margin-top: 2px; }
.rank-best {
  background: var(--card); border: 1px solid var(--accent); border-radius: 12px;
  padding: 14px 16px; margin: 4px 0; border-top: 1px solid var(--accent);
}
.rank-best .rank-num { color: var(--accent); }
.spot-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.spot-head h2 { margin: 0; }
.spot-source { margin: 2px 0 10px; color: var(--muted); font-size: 0.82rem; }
.flag {
  display: inline-block; font-weight: 700; font-size: 0.74rem; letter-spacing: 0.02em;
  padding: 3px 9px; border-radius: 999px; white-space: nowrap;
  background: var(--warn-bg); color: var(--warn-ink); border: 1px solid var(--warn-border);
}
.flag-soft { background: var(--page); color: var(--muted); border-color: var(--border); }
.day-row {
  display: flex; align-items: baseline; gap: 12px; padding: 9px 0;
  border-top: 1px solid var(--border); flex-wrap: wrap;
}
.day-row:first-of-type { border-top: none; }
.day-date { width: 78px; font-weight: 700; flex: none; }
.day-chip { width: 148px; flex: none; }
.day-time { width: 56px; flex: none; color: var(--muted); font-size: 0.9rem; }
.day-detail { color: var(--muted); font-size: 0.92rem; flex: 1 1 240px; }
@media (max-width: 800px) {
  .rank-row { grid-template-columns: 34px 1fr; }
  .rank-window, .rank-detail, .rank-why { grid-column: 1 / 3; }
  .day-row { flex-wrap: wrap; }
  .day-chip, .day-time { width: auto; }
  .day-detail { flex-basis: 100%; }
}
"""

# Dashboard-only styling, appended after PAGE_CSS. Adds the sticky tab bar and
# the show/hide panel rule; reuses the palette, cards, chips, tide/strip styles,
# and dark mode above. The hero and tide section inside the Today panel render
# byte-identically to the former single mode.
DASHBOARD_CSS = """
.tabbar {
  position: sticky; top: 0; z-index: 1000; display: flex; gap: 4px;
  padding: 8px 4%; background: var(--card); border-bottom: 1px solid var(--border);
  overflow-x: auto;
}
.tab {
  font: inherit; font-weight: 700; font-size: 0.92rem; color: var(--muted);
  background: transparent; border: none; border-radius: 10px; padding: 9px 16px;
  cursor: pointer; white-space: nowrap;
}
.tab:hover { color: var(--ink); background: var(--page); }
.tab[aria-selected="true"] { color: #fff; background: var(--accent); }
.theme-seg {
  display: inline-flex; margin-left: auto; align-self: center; flex: none;
  background: var(--page); border: 1px solid var(--border);
  border-radius: 999px; padding: 3px; gap: 2px;
}
.theme-seg button {
  font: inherit; font-size: 0.82rem; font-weight: 700; line-height: 1; cursor: pointer;
  border: none; background: transparent; color: var(--muted);
  padding: 6px 12px; border-radius: 999px; white-space: nowrap;
}
.theme-seg button:hover { color: var(--ink); }
.theme-seg button[aria-pressed="true"] { color: #fff; background: var(--accent); }
.panel[hidden] { display: none; }
.placeholder { color: var(--muted); }
.tide-week { margin-top: 6px; }
.tide-week .tide-curve { stroke-width: 1.3; }
.tide-week-divider { stroke: var(--border); stroke-width: 0.5; opacity: 0.6; }
.tide-week-label { fill: var(--ink); font-size: 12px; font-weight: 700; }
.tide-week-tick { stroke: var(--border); stroke-width: 1; }
.tide-week-hour { fill: var(--muted); font-size: 9px; }
.tide-week-time { fill: var(--ink); font-size: 8.5px; font-weight: 600; }
.fc-crows { margin: 4px 0 16px; }
.fc-crow {
  display: flex; align-items: center; gap: 14px; width: 100%; font: inherit; cursor: pointer;
  text-align: left; background: transparent; color: var(--ink); border: none;
  border-top: 1px solid var(--border); padding: 11px 8px;
}
.fc-crow:first-of-type { border-top: none; }
.fc-crow:hover { background: var(--page); }
.fc-crow.active { background: var(--page); box-shadow: inset 3px 0 0 var(--accent); }
.fc-crow .d { font-weight: 800; width: 62px; flex: none; }
.fc-crow .chipcol { width: 118px; flex: none; }
.fc-crow .sw { width: 128px; flex: none; color: var(--muted); font-size: 0.88rem; }
.fc-crow .desc { flex: 1; color: var(--muted); font-size: 0.9rem; }
.fc-detail[hidden] { display: none; }
.fc-detail h3 { margin: 6px 0 8px; font-size: 1.05rem; }
@media (max-width: 760px) {
  .fc-crow { flex-wrap: wrap; }
  .fc-crow .sw, .fc-crow .desc { width: auto; flex-basis: 100%; }
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
  window.__surfMap = map;
})();"""

# Week map: one marker per spot with a name + best-verdict popup, auto-fit to
# the group. Spots are injected as a JSON array via a sentinel (the same brace-
# safe substitution trick MAP_JS uses for the literal {z}/{x}/{y} tile template).
WEEK_MAP_JS = """(function () {
  var spots = __SPOTS__;
  var map = L.map('surf-map', { scrollWheelZoom: false });
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '\\u00a9 OpenStreetMap'
  }).addTo(map);
  var markers = spots.map(function (s) {
    return L.circleMarker([s.lat, s.lon], {
      radius: 9, weight: 2, color: '#ffffff', fillColor: '#2b7cd3', fillOpacity: 0.9
    }).addTo(map).bindPopup('<strong>' + s.name + '</strong><br>' + s.best);
  });
  if (markers.length === 1) {
    map.setView([spots[0].lat, spots[0].lon], 11);
  } else if (markers.length > 1) {
    map.fitBounds(L.featureGroup(markers).getBounds().pad(0.2));
  } else {
    map.setView([0, 0], 2);
  }
})();"""


# Panel toggle: all four panels are in the DOM; clicking a tab (or arriving with
# a #today/#forecast/#windows/#info fragment) shows one and hides the rest. No
# data is fetched on click. When the Today panel becomes visible its Leaflet map
# is re-measured, so a dashboard opened straight onto another tab still draws the
# map correctly once Today is selected.
TOGGLE_JS = """(function () {
  var panels = ['today', 'forecast', 'windows', 'info'];
  function show(name) {
    if (panels.indexOf(name) < 0) { name = 'today'; }
    panels.forEach(function (p) {
      var panel = document.getElementById('panel-' + p);
      var tab = document.getElementById('tab-' + p);
      if (panel) { panel.hidden = (p !== name); }
      if (tab) { tab.setAttribute('aria-selected', p === name ? 'true' : 'false'); }
    });
    if (name === 'today' && window.__surfMap) { window.__surfMap.invalidateSize(); }
  }
  function fromHash() {
    show((window.location.hash || '').replace('#', '') || 'today');
  }
  document.querySelectorAll('.tab[data-panel]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      var name = tab.getAttribute('data-panel');
      window.location.hash = name;
      show(name);
    });
  });
  window.addEventListener('hashchange', fromHash);
  fromHash();
})();"""

# Runs inline in <head>, before the body paints, so an explicit light/dark
# choice never flashes the system palette first. An "auto"/absent choice leaves
# data-theme unset and lets the prefers-color-scheme media query decide.
THEME_INIT_JS = (
    "(function(){try{var v=localStorage.getItem('surf-theme');"
    "if(v==='light'||v==='dark'){document.documentElement.setAttribute('data-theme',v);}"
    "}catch(e){}})();"
)

# Wires the tab-bar theme segmented control (Auto / sun / moon). The chosen
# value is written to <html data-theme> and remembered in localStorage;
# "auto" clears the override so the page follows the system again.
THEME_JS = """(function () {
  var KEY = 'surf-theme';
  var root = document.documentElement;
  var seg = document.querySelector('.theme-seg');
  if (!seg) { return; }
  function choice() {
    var v;
    try { v = localStorage.getItem(KEY); } catch (e) { v = null; }
    return (v === 'light' || v === 'dark') ? v : 'auto';
  }
  function apply(v) {
    try { localStorage.setItem(KEY, v); } catch (e) {}
    if (v === 'light' || v === 'dark') { root.setAttribute('data-theme', v); }
    else { root.removeAttribute('data-theme'); }
    seg.querySelectorAll('button[data-theme-choice]').forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-theme-choice') === v ? 'true' : 'false');
    });
  }
  seg.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-theme-choice]');
    if (b) { apply(b.getAttribute('data-theme-choice')); }
  });
  apply(choice());
})();"""

# Forecast day selector: every day's detail chart is already in the DOM (one
# .fc-detail per day, all but the first hidden). Clicking a .fc-crow row shows
# that day's chart and hides the rest, and marks the row active. Scoped to the
# Forecast panel so it never touches the Today tab; no data is fetched on click.
FORECAST_JS = """(function () {
  var panel = document.getElementById('panel-forecast');
  if (!panel) { return; }
  panel.addEventListener('click', function (e) {
    var btn = e.target.closest('.fc-crow');
    if (!btn || !panel.contains(btn)) { return; }
    var day = btn.getAttribute('data-day');
    panel.querySelectorAll('.fc-crow').forEach(function (b) {
      b.classList.toggle('active', b === btn);
    });
    panel.querySelectorAll('.fc-detail').forEach(function (d) {
      d.hidden = (d.getAttribute('data-day') !== day);
    });
  });
})();"""

# The four dashboard tabs, in fixed display order.
DASHBOARD_TABS = [
    ("today", "Today"),
    ("forecast", "Forecast"),
    ("windows", "Windows"),
    ("info", "Spot info"),
]

# Placeholder copy for the panels this slice does not yet populate; later slices
# replace each body. Today and Forecast are populated; Windows and Spot info are
# still placeholders. Kept in sync with the Markdown twin's placeholders.
PANEL_PLACEHOLDERS = {
    "windows": ("Windows", "The ranked session windows for this week land here in a later update."),
    "info": (
        "Spot info",
        "The works-on profile, hazards, webcams, and community notes land here in a later update.",
    ),
}


def _swell_wind(entry: dict[str, Any]) -> str:
    """The "swell · wind" display join for a day or ranking entry; parts optional."""
    return " · ".join(str(entry[k]) for k in ("swell", "wind") if entry.get(k))


def _entry_detail(entry: dict[str, Any]) -> str:
    """The "swell · wind - why" display line shared by week day rows; parts optional."""
    detail = _swell_wind(entry)
    if entry.get("why"):
        detail = f"{detail} - {entry['why']}" if detail else str(entry["why"])
    return detail


def _target_day_view(package: dict[str, Any]) -> dict[str, Any]:
    """Resolve the shared Today values used by both the HTML and Markdown twins.

    Pulls the target day, verdict, spot identity, tide extremes/windows, the
    daylight row, and the display units out of the package once so the HTML
    Today panel and the flat Markdown twin never drift. Raises KeyError when
    `analysis.target_day` is absent so the CLI degrades to the soft-failure JSON.
    """
    conditions = package.get("conditions", {})
    analysis = package.get("analysis", {})

    target = analysis["target_day"]
    verdict = target["verdict"]
    target_date = target.get("date") or conditions.get("report", {}).get("target_date")

    spot = conditions.get("spot", {})
    units = conditions.get("units", {})
    tides = conditions.get("tides", {})
    tide_days = tides.get("days")

    daylight_row = None
    for row in conditions.get("daylight", {}).get("days", []):
        if row.get("date") == target_date and "first_light" in row:
            daylight_row = row
            break

    target_hours: list[dict[str, Any]] = []
    for md in conditions.get("marine", {}).get("days", []):
        if md.get("date") == target_date:
            target_hours = md.get("hours") or []
            break

    datum = tides.get("datum")
    # The datum belongs in the section title (spec: "Saturday tide & session
    # windows (chart datum)"); CD gets its human-readable name, other datums
    # keep their code. Source attribution stays in the sub-paragraph.
    datum_phrase = ""
    if datum:
        datum_phrase = " (chart datum)" if datum == "CD" else f" ({datum})"

    coords = spot.get("coordinates") or [0.0, 0.0]
    return {
        "target": target,
        "verdict": verdict,
        "target_date": target_date,
        "spot_name": spot.get("name", "This spot"),
        "lat": float(coords[0]),
        "lon": float(coords[1]),
        "one_liner": target.get("one_liner"),
        "windows": target.get("windows") or [],
        "tide_events": _target_day_events(tide_days, target_date),
        "extremes": assemble_extremes(tide_days, target_date) if (tide_days and target_date) else [],
        "daylight_row": daylight_row,
        "target_hours": target_hours,
        "tide_source": tides.get("source"),
        "tide_note": tides.get("note"),
        "datum_phrase": datum_phrase,
        "day_label": _weekday(target_date, WEEKDAY_FULL) if target_date else "Target day",
        "tide_unit": units.get("tide_height", "m"),
        "swell_unit": units.get("wave_height", "m"),
        "wind_unit": units.get("wind_speed", "kn"),
    }


def _target_day_events(tide_days: list[dict[str, Any]] | None, target_date: str | None) -> list[dict[str, Any]]:
    """The target day's own tide events (for the Markdown twin's tide list)."""
    for d in tide_days or []:
        if d.get("date") == target_date:
            return d.get("events") or []
    return []


def _forecast_view(package: dict[str, Any]) -> dict[str, Any]:
    """Resolve the shared Forecast values used by both the HTML and Markdown twins.

    Pairs each `analysis.week` row with that day's assembled tide extremes,
    daylight window and hourly marine data so the interactive Forecast panel and
    its flat Markdown twin never drift. Every input already lives in the payload:
    `analysis.week` (per-day swell/wind/verdict, already corrected to the spot's
    works-on profile), `conditions.tides.days` (per-day extremes),
    `conditions.daylight.days` (per-day first/last light) and
    `conditions.marine.days[].hours` (per-day hourly strip data, emitted for all
    seven days by `build_marine_days`). No `fetch_conditions.py` change is
    required. Only the target day carries session windows (they are computed for
    the target day), so per-day detail charts shade windows on that day alone.
    """
    conditions = package.get("conditions", {})
    analysis = package.get("analysis", {})
    units = conditions.get("units", {})
    tides = conditions.get("tides", {})
    tide_days = tides.get("days")

    daylight_by_date = {
        row["date"]: row
        for row in conditions.get("daylight", {}).get("days", [])
        if row.get("date") and "first_light" in row and "last_light" in row
    }
    hours_by_date = {
        md["date"]: (md.get("hours") or [])
        for md in conditions.get("marine", {}).get("days", [])
        if md.get("date")
    }

    target = analysis.get("target_day") or {}
    target_date = target.get("date") or conditions.get("report", {}).get("target_date")
    target_windows = target.get("windows") or []

    week = analysis.get("week") or []
    days = []
    for entry in week:
        d = entry.get("date")
        is_target = bool(d) and d == target_date
        days.append({
            "date": d,
            "label": _weekday(d, WEEKDAY_ABBR) if d else "",
            "extremes": assemble_extremes(tide_days, d) if (tide_days and d) else [],
            "daylight": daylight_by_date.get(d),
            "hours": hours_by_date.get(d, []),
            "windows": target_windows if is_target else [],
            "entry": entry,
        })
    return {
        "week": week,
        "days": days,
        "tide_unit": units.get("tide_height", "m"),
        "swell_unit": units.get("wave_height", "m"),
        "wind_unit": units.get("wind_speed", "kn"),
        "tide_source": tides.get("source"),
        "has_tides": any(day["extremes"] for day in days),
    }


def _today_panel_html(view: dict[str, Any]) -> tuple[str, str]:
    """Build the Today panel body (hero + tide/session card) and its map JS.

    This is the former single-mode content, limited to the target day: the
    Leaflet map hero with the verdict chip, and the tide curve with the aligned
    hourly strip clipped to daylight. Returns (panel_html, map_js).
    """
    esc = html.escape

    # --- Map hero -----------------------------------------------------------
    weekday_upper = _weekday(view["target_date"], WEEKDAY_FULL).upper() if view["target_date"] else ""
    hero_chip = _verdict_chip(view["verdict"], weekday_upper or None)
    one_liner = view["one_liner"]
    sub_html = f'<p class="sub">{esc(str(one_liner))}</p>' if one_liner else ""
    hero = (
        '<section class="hero">'
        '<div id="surf-map"></div>'
        f'<div class="overlay">{hero_chip}'
        f'<h1>{esc(str(view["spot_name"]))}</h1>{sub_html}</div>'
        "</section>"
    )

    # --- Tide curve + session windows --------------------------------------
    tide_sub = f'<p class="sub">{esc(str(view["tide_source"]))}</p>' if view["tide_source"] else ""
    if view["extremes"]:
        tide_body = tide_svg(
            view["extremes"], view["windows"], view["daylight_row"], view["tide_unit"],
            view["target_hours"], view["swell_unit"], view["wind_unit"],
        )
    else:
        note = view["tide_note"] or "No automated tide data for this spot."
        items = "".join(
            f'<li>{esc(str(w.get("label", "Session")))}: '
            f'{esc(str(w.get("from", "?")))}–{esc(str(w.get("to", "?")))}</li>'
            for w in view["windows"]
        )
        windows_html = f'<ul class="windows-list">{items}</ul>' if items else ""
        tide_body = f'<p class="tide-note">{esc(str(note))}</p>{windows_html}'

    tide_section = (
        f'<section class="card"><h2>{esc(view["day_label"])} tide &amp; session windows'
        f"{esc(view['datum_phrase'])}</h2>"
        f"{tide_sub}{tide_body}</section>"
    )

    panel = f'{hero}\n<main class="wrap">\n{tide_section}\n</main>'
    map_js = MAP_JS.replace("__LAT__", json.dumps(view["lat"])).replace("__LON__", json.dumps(view["lon"]))
    return panel, map_js


def _forecast_panel_html(view: dict[str, Any]) -> str:
    """Build the interactive Forecast panel: a week overview plus a by-day drilldown.

    Takes the resolved `_forecast_view` dict (mirroring how `_today_panel_html`
    takes the resolved `_target_day_view`), so the caller resolves once. Two
    stacked cards:

    - **Week at a glance**: the compressed 7-day tide overview (`week_tide_svg`),
      each day a daylight-clipped column with the mid-tide split.
    - **By day**: a list of day-selector rows (weekday, works-on-corrected GO /
      CHECK / SKIP chip, swell, the day's one-line description). Clicking a row
      swaps in that day's full Today-style tide chart below, in the same card;
      the first day is selected by default. All charts are pre-rendered into the
      document, so the toggle (see FORECAST_JS) never refetches.
    """
    esc = html.escape

    if not view["week"]:
        return (
            '<main class="wrap"><section class="card"><h2>Forecast</h2>'
            '<p class="placeholder">No 7-day forecast is available for this spot.</p>'
            "</section></main>"
        )

    # --- Week at a glance ---------------------------------------------------
    if view["has_tides"]:
        source = f"{view['tide_source']}. " if view["tide_source"] else ""
        overview = (
            '<section class="card"><h2>Week at a glance</h2>'
            f'<p class="sub">{esc(source)}Each day clipped to its daylight hours '
            "(first light to last light); night hours are not drawn.</p>"
            f'{week_tide_svg(view["days"], view["tide_unit"])}</section>'
        )
    else:
        overview = (
            '<section class="card"><h2>Week at a glance</h2>'
            '<p class="tide-note">No automated tide data for this spot.</p></section>'
        )

    # --- By day: selector rows + pre-rendered per-day detail charts ---------
    rows, details = [], []
    for i, day in enumerate(view["days"]):
        entry = day["entry"]
        d = day["date"]
        verdict = entry.get("verdict", "check")
        short_label = f"{day['label']} {date.fromisoformat(d).day}" if d else "?"
        full_label = f"{_weekday(d, WEEKDAY_FULL)} {d}" if d else "This day"
        chip = _verdict_chip(verdict, extra_class="week-verdict", short=True)
        swell = str(entry.get("swell", ""))
        why = str(entry.get("why") or "")
        active = " active" if i == 0 else ""
        rows.append(
            f'<button class="fc-crow{active}" type="button" data-day="{i}">'
            f'<span class="d">{esc(short_label)}</span>'
            f'<span class="chipcol">{chip}</span>'
            f'<span class="sw">{esc(swell)}</span>'
            f'<span class="desc">{esc(why)}</span></button>'
        )

        if day["extremes"]:
            chart = tide_svg(
                day["extremes"], day["windows"], day["daylight"], view["tide_unit"],
                day["hours"], view["swell_unit"], view["wind_unit"], id_prefix=f"fc{i}",
            )
        else:
            chart = '<p class="tide-note">No tide data for this day.</p>'
        hidden = "" if i == 0 else " hidden"
        details.append(
            f'<div class="fc-detail" data-day="{i}"{hidden}>'
            f'<h3>{esc(full_label)} tide &amp; session</h3>{chart}</div>'
        )

    by_day = (
        '<section class="card"><h2>By day</h2>'
        '<p class="sub">Verdicts corrected to this spot\'s works-on profile. '
        "Pick a day for its full tide chart and hourly strip.</p>"
        f'<div class="fc-crows">{"".join(rows)}</div>'
        f'<div class="fc-details">{"".join(details)}</div></section>'
    )

    return f'<main class="wrap">\n{overview}\n{by_day}\n</main>'


def render_dashboard(package: dict[str, Any]) -> str:
    """Render the self-contained tabbed Dashboard HTML document for a package.

    One file: a four-button tab bar (Today / Forecast / Windows / Spot info),
    four panels, and an inline toggle script that shows one panel at a time (the
    opening tab is chosen by a URL fragment; default Today). The Today and
    Forecast panels are populated; Windows and Spot info are still placeholders.
    Reads Leaflet's vendored CSS/JS for inlining (raises OSError when missing,
    which the CLI
    turns into a soft failure). Every value drawn from the package is
    HTML-escaped; the only remote reference emitted is the OSM tile URL template
    inside the inlined map init.
    """
    view = _target_day_view(package)
    esc = html.escape

    leaflet_css = _read_vendor("leaflet.css")
    leaflet_js = _read_vendor("leaflet.js")

    today_body, map_js = _today_panel_html(view)
    forecast_body = _forecast_panel_html(_forecast_view(package))

    # --- Tab bar (fixed order) ---------------------------------------------
    tab_buttons = "".join(
        f'<button class="tab" id="tab-{key}" role="tab" data-panel="{key}" '
        f'aria-controls="panel-{key}" aria-selected="{"true" if key == "today" else "false"}">'
        f"{esc(label)}</button>"
        for key, label in DASHBOARD_TABS
    )
    # Theme control docked at the right end of the bar: Auto (follow system,
    # the default), plus explicit sun/moon overrides. Grouped separately from
    # the tabs so it stays out of the tablist's tab sequence.
    theme_control = (
        '<div class="theme-seg" role="group" aria-label="Colour theme">'
        '<button type="button" data-theme-choice="auto" aria-pressed="true" title="Match system">Auto</button>'
        '<button type="button" data-theme-choice="light" aria-pressed="false" '
        'aria-label="Light theme" title="Light">☀️</button>'
        '<button type="button" data-theme-choice="dark" aria-pressed="false" '
        'aria-label="Dark theme" title="Dark">\U0001f319</button>'
        "</div>"
    )
    tabbar = (
        '<nav class="tabbar" role="tablist" aria-label="Dashboard views">'
        f"{tab_buttons}{theme_control}</nav>"
    )

    # --- Panels (Today + Forecast populated; Windows / Spot info placeholders) --
    # Populated bodies are looked up by tab key; any tab without one falls back
    # to its placeholder card, so render_dashboard stays tab-agnostic.
    populated = {"today": today_body, "forecast": forecast_body}
    panels = []
    for key, _label in DASHBOARD_TABS:
        body = populated.get(key)
        if body is None:
            heading, copy = PANEL_PLACEHOLDERS[key]
            body = (
                f'<main class="wrap"><section class="card"><h2>{esc(heading)}</h2>'
                f'<p class="placeholder">{esc(copy)}</p></section></main>'
            )
        hidden = "" if key == "today" else " hidden"
        panels.append(
            f'<section class="panel" id="panel-{key}" role="tabpanel" '
            f'aria-labelledby="tab-{key}"{hidden}>\n{body}\n</section>'
        )
    panels_html = "\n".join(panels)

    # --- Footer (shared, below the panels) ---------------------------------
    # The twin's exact path depends on the CLI's --out override, which the
    # renderer does not see, so name it by its relationship rather than guessing.
    footer = (
        '<main class="wrap"><footer class="footer">'
        "<p>AI-generated dashboard. Verify conditions on-site; if in doubt, don't paddle out.</p>"
        "<p>A paired Markdown twin is saved alongside this file (same name, .md).</p>"
        "</footer></main>"
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(str(view['spot_name']))} surf dashboard</title>\n"
        f"<style>{leaflet_css}</style>\n"
        f"<style>{PAGE_CSS}{DASHBOARD_CSS}</style>\n"
        f"<script>{THEME_INIT_JS}</script>\n"
        "</head>\n<body>\n"
        f"{tabbar}\n"
        f"{panels_html}\n"
        f"{footer}\n"
        f"<script>{leaflet_js}</script>\n"
        f"<script>{map_js}</script>\n"
        f"<script>{TOGGLE_JS}</script>\n"
        f"<script>{THEME_JS}</script>\n"
        f"<script>{FORECAST_JS}</script>\n"
        "</body>\n</html>\n"
    )


def render_dashboard_markdown(package: dict[str, Any]) -> str:
    """Render the flat Markdown twin of the Dashboard (no tabs; sections stacked).

    Markdown has no tabs, so the four views stack: Today and Forecast (populated)
    then Windows / Spot info placeholders. The Today and Forecast sections mirror
    their HTML panels from the same resolved values (`_target_day_view` /
    `_forecast_view`), so the twin never drifts from the page. Raises KeyError
    when `analysis.target_day` is absent (soft-failed by the CLI).
    """
    view = _target_day_view(package)
    emoji, label = VERDICT_DISPLAY.get(view["verdict"], ("", str(view["verdict"]).upper()))

    date_suffix = f" ({view['target_date']})" if view["target_date"] else ""
    lines = [
        f"# {view['spot_name']} - Surf Dashboard{date_suffix}",
        "",
        "> AI-generated dashboard. Verify conditions on-site; if in doubt, don't paddle out.",
        "",
        f"## Today - {view['day_label']}{date_suffix}",
        "",
        f"**Verdict:** {emoji} {label}".rstrip(),
        "",
    ]
    if view["one_liner"]:
        lines += [str(view["one_liner"]), ""]

    lines += [f"### Tide & session windows{view['datum_phrase']}", ""]
    if view["tide_source"]:
        lines += [f"Source: {view['tide_source']}.", ""]

    tide_unit = view["tide_unit"]
    if view["tide_events"]:
        for e in view["tide_events"]:
            kind = str(e.get("type", "")).capitalize()
            time = e.get("time", "?")
            height = e.get("height")
            height_str = f" · {_fmt_height(float(height))} {tide_unit}" if height is not None else ""
            lines.append(f"- {kind} {time}{height_str}")
        lines.append("")
    else:
        lines += [view["tide_note"] or "No automated tide data for this spot.", ""]

    if view["windows"]:
        lines.append("**Session windows:**")
        for w in view["windows"]:
            label_w = w.get("label", "Session")
            lines.append(f"- {label_w}: {w.get('from', '?')}-{w.get('to', '?')}")
        lines.append("")

    # Forecast: this spot's next 7 days, mirroring the HTML Forecast panel rows.
    forecast = _forecast_view(package)
    lines += ["## Forecast", ""]
    if forecast["week"]:
        for entry in forecast["week"]:
            d = entry.get("date")
            weekday = _weekday(d, WEEKDAY_FULL) if d else "?"
            v_emoji, v_label = VERDICT_DISPLAY.get(
                entry.get("verdict"), ("", str(entry.get("verdict", "")).upper())
            )
            head = f"{weekday} {d}".strip()
            verdict_str = f"{v_emoji} {v_label}".strip()
            detail = _entry_detail(entry)
            line = f"- **{head}** - {verdict_str}"
            if detail:
                line += f" - {detail}"
            lines.append(line)
        lines.append("")
    else:
        lines += ["_No 7-day forecast is available for this spot._", ""]

    for key, _ in DASHBOARD_TABS:
        if key not in PANEL_PLACEHOLDERS:
            continue
        heading, copy = PANEL_PLACEHOLDERS[key]
        lines += [f"## {heading}", "", f"_{copy}_", ""]

    return "\n".join(lines).rstrip() + "\n"


def _best_verdict(days: list[dict[str, Any]]) -> str:
    """The strongest verdict across a spot's days (go > check > skip).

    Used for the map popup so a spot reads by its best day of the week. An
    empty or unknown-verdict day list falls back to skip.
    """
    order = {"go": 3, "check": 2, "skip": 1}
    best = max((d.get("verdict", "skip") for d in days or []), key=lambda v: order.get(v, 0), default="skip")
    return best if best in VERDICT_DISPLAY else "skip"


def render_week(package: dict[str, Any]) -> str:
    """Render the self-contained HTML week planner for a week data package.

    Validates the week shape (mode == "week" with spots and ranking) and raises
    ValueError otherwise so the CLI degrades to the soft-failure JSON. Reuses
    the single-mode palette, chip styles, dark mode, and inlined Leaflet; adds
    WEEK_CSS for the ranking rows and per-spot cards. Every value drawn from the
    package is HTML-escaped; the only remote reference emitted is the OSM tile
    URL template inside the inlined map init. Determinism is preserved: no
    wall-clock reads, ranking order is emitted exactly as given.
    """
    if package.get("mode") != "week":
        raise ValueError('week mode requires a package with "mode": "week"')
    spots = package.get("spots")
    ranking = package.get("ranking")
    if not isinstance(spots, list) or ranking is None:
        raise ValueError("week package must carry a spots list and a ranking list")

    week = package.get("week") or {}
    start = week.get("start", "")
    end = week.get("end", "")

    leaflet_css = _read_vendor("leaflet.css")
    leaflet_js = _read_vendor("leaflet.js")

    esc = html.escape

    slug_to_name = {s.get("slug"): s.get("name", s.get("slug", "Spot")) for s in spots}

    # --- Map hero -----------------------------------------------------------
    spot_count = len(spots)
    count_label = f"{spot_count} spot" + ("" if spot_count == 1 else "s")
    hero = (
        '<section class="hero hero-week"><div id="surf-map"></div>'
        '<div class="overlay"><span class="chip chip-go">WEEK PLANNER</span>'
        f'<p class="range">{esc(str(start))} to {esc(str(end))}</p>'
        f'<p class="count">{esc(count_label)}</p></div></section>'
    )

    # --- Best windows this week (ranking, order preserved) ------------------
    rank_rows = []
    for i, entry in enumerate(ranking):
        d = entry.get("date")
        when = f"{_weekday(d, WEEKDAY_ABBR)} {esc(d)}" if d else "?"
        spot_name = slug_to_name.get(entry.get("spot_slug"), entry.get("spot_slug", "Spot"))
        window = entry.get("window") or {}
        win_bits = []
        if window.get("from") or window.get("to"):
            win_bits.append(f'{esc(str(window.get("from", "?")))}-{esc(str(window.get("to", "?")))}')
        if window.get("label"):
            win_bits.append(esc(str(window["label"])))
        win_text = " · ".join(win_bits)
        chip = _verdict_chip(entry.get("verdict", "check"))
        detail = esc(_swell_wind(entry))
        why = f'<div class="rank-why">{esc(str(entry["why"]))}</div>' if entry.get("why") else ""
        row_class = "rank-row rank-best" if i == 0 else "rank-row"
        rank_rows.append(
            f'<div class="{row_class}">'
            f'<div class="rank-num">{i + 1}</div>'
            f'<div class="rank-when">{when}</div>'
            f'<div class="rank-spot">{esc(str(spot_name))}</div>'
            f'<div class="day-chip">{chip}</div>'
            f'<div class="rank-window">{win_text}</div>'
            f'<div class="rank-detail">{detail}</div>'
            f"{why}</div>"
        )
    if rank_rows:
        ranking_body = "".join(rank_rows)
    else:
        ranking_body = '<p class="tide-note">No standout windows ranked this week.</p>'
    ranking_section = (
        '<section class="card"><h2>Best windows this week</h2>' + ranking_body + "</section>"
    )

    # --- Per-spot cards (package order) -------------------------------------
    spot_sections = []
    for spot in spots:
        name = spot.get("name", spot.get("slug", "Spot"))
        slug = spot.get("slug", "")
        flags = []
        if not spot.get("profiled"):
            flags.append(f'<span class="flag">unprofiled - run /surfing:research {esc(str(slug))}</span>')
        elif spot.get("reresearch_suggested"):
            age = spot.get("profile_age_days")
            age_text = f"profile is {age} days old, consider re-researching" if age is not None \
                else "profile may be stale, consider re-researching"
            flags.append(f'<span class="flag flag-soft">{esc(age_text)}</span>')
        flags_html = "".join(flags)

        source = spot.get("verdict_source")
        source_html = f'<p class="spot-source">Verdicts from: {esc(str(source))}</p>' if source else ""

        day_rows = []
        for day in spot.get("days") or []:
            d = day.get("date")
            day_label = f"{_weekday(d, WEEKDAY_ABBR)} {date.fromisoformat(d).day}" if d else "?"
            chip = _verdict_chip(day.get("verdict", "check"))
            best_time = day.get("best_time")
            time_html = f'<div class="day-time">{esc(str(best_time))}</div>' if best_time else '<div class="day-time"></div>'
            detail = _entry_detail(day)
            day_rows.append(
                f'<div class="day-row"><div class="day-date">{esc(day_label)}</div>'
                f'<div class="day-chip">{chip}</div>'
                f"{time_html}"
                f'<div class="day-detail">{esc(detail)}</div></div>'
            )
        spot_sections.append(
            '<section class="card">'
            f'<div class="spot-head"><h2>{esc(str(name))}</h2>{flags_html}</div>'
            f"{source_html}{''.join(day_rows)}</section>"
        )
    spots_html = "\n".join(spot_sections)

    # --- Footer (consistent with single mode) -------------------------------
    footer = (
        '<footer class="footer">'
        "<p>AI-generated report. Verify conditions on-site; if in doubt, don't paddle out.</p>"
        "</footer>"
    )

    # --- Map markers (one per spot, best-verdict popup) ---------------------
    markers = []
    for spot in spots:
        coords = spot.get("coordinates") or [0.0, 0.0]
        emoji, label = VERDICT_DISPLAY.get(_best_verdict(spot.get("days")), ("", ""))
        # Popup strings are concatenated into HTML inside the inlined map JS, so
        # escape them here. This also strips any '<' that could otherwise break
        # out of the surrounding <script> tag from within the JSON literal.
        markers.append({
            "lat": float(coords[0]),
            "lon": float(coords[1]),
            "name": esc(str(spot.get("name", spot.get("slug", "Spot")))),
            "best": esc(f"{emoji} {label}".strip()),
        })
    map_js = WEEK_MAP_JS.replace("__SPOTS__", json.dumps(markers))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Surf week planner {esc(str(start))} to {esc(str(end))}</title>\n"
        f"<style>{leaflet_css}</style>\n"
        f"<style>{PAGE_CSS}{WEEK_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{hero}\n"
        '<main class="wrap">\n'
        f"{ranking_section}\n"
        f"{spots_html}\n"
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
    "The data package and any Markdown twin remain readable directly. "
    "The HTML view is an optional convenience."
)


def _write_file(path: Path, text: str) -> None:
    """Write text, creating the parent directory. Raises OSError on failure."""
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@click.command()
@click.option("--data", default=None, help="Path to the run's data-package JSON (Phase 5A shape). Required.")
@click.option(
    "--mode",
    type=click.Choice(["dashboard", "week"]),
    default="dashboard",
    help="dashboard (default) renders one spot's tabbed Dashboard (HTML + a flat Markdown twin); "
    "week renders a multi-spot week planner.",
)
@click.option(
    "--out",
    default=None,
    help="Override the output HTML path. Defaults to reports/{date}-{slug}-dashboard.html "
    "(dashboard) or reports/{week.start}-week.html (week). In dashboard mode the Markdown "
    "twin is written next to it with a .md extension.",
)
def cli(data: str | None, mode: str, out: str | None):
    """Render a self-contained HTML surf dashboard, print {"html_path": ...}.

    In dashboard mode the paired Markdown twin is written next to the HTML and
    its path echoed as "md_path"; week mode writes and echoes the HTML only.
    """
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
        if mode == "week":
            out_path = out or week_output_path(package)
            html_doc = render_week(package)
            md_doc = None
        else:
            out_path = out or dashboard_output_path(package)
            html_doc = render_dashboard(package)
            md_doc = render_dashboard_markdown(package)
    except (KeyError, TypeError, ValueError, OSError) as e:
        click.echo(json.dumps({"error": f"Cannot render report: {e}", "note": SOFT_FAIL_NOTE}))
        sys.exit(0)

    result: dict[str, str] = {"html_path": out_path}
    try:
        _write_file(Path(out_path), html_doc)
        if md_doc is not None:
            md_path = str(Path(out_path).with_suffix(".md"))
            _write_file(Path(md_path), md_doc)
            result["md_path"] = md_path
    except OSError as e:
        click.echo(json.dumps({"error": f"Cannot write output to {out_path}: {e}", "note": SOFT_FAIL_NOTE}))
        sys.exit(0)

    click.echo(json.dumps(result))


if __name__ == "__main__":
    cli()
