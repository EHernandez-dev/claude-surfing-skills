"""Tests for render_report.py.

Unit tests cover the pure seams (tide geometry, path swapping, SVG output);
renderer/CLI tests cover graceful degradation, escaping, self-containment, and
the click contract. Golden-file tests pin the full rendered Dashboard document
and its flat Markdown twin.

Regenerate the dashboard golden files after an INTENTIONAL design change with:
    cd skills/spot-researcher/tools && uv run python -c \
      "import json, render_report; \
       print(render_report.render_dashboard(json.load(open('testdata/data-package.json'))), end='')" \
      > testdata/golden-dashboard-report.html
    cd skills/spot-researcher/tools && uv run python -c \
      "import json, render_report; \
       print(render_report.render_dashboard_markdown(json.load(open('testdata/data-package.json'))), end='')" \
      > testdata/golden-dashboard-report.md

Regenerate the week-planner golden file after an INTENTIONAL design change with:
    cd skills/spot-researcher/tools && uv run python -c \
      "import json, render_report; \
       print(render_report.render_week(json.load(open('testdata/week-data-package.json'))), end='')" \
      > testdata/golden-week-report.html
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import render_report
from render_report import (
    assemble_extremes,
    cli,
    dashboard_output_path,
    parse_hhmm,
    render_dashboard,
    render_dashboard_markdown,
    render_week,
    tide_height_at,
    tide_svg,
    week_output_path,
    week_tide_svg,
)

TOOLS_DIR = Path(__file__).resolve().parent
TESTDATA = TOOLS_DIR / "testdata"
PACKAGE_PATH = TESTDATA / "data-package.json"
GOLDEN_PATH = TESTDATA / "golden-dashboard-report.html"
GOLDEN_MD_PATH = TESTDATA / "golden-dashboard-report.md"
WEEK_PACKAGE_PATH = TESTDATA / "week-data-package.json"
WEEK_GOLDEN_PATH = TESTDATA / "golden-week-report.html"


def load_package() -> dict:
    return json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))


def load_week_package() -> dict:
    return json.loads(WEEK_PACKAGE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pure seams
# ---------------------------------------------------------------------------


class TestParseHhmm:
    def test_on_the_hour(self):
        assert parse_hhmm("08:00") == 8.0

    def test_minutes(self):
        assert parse_hhmm("08:14") == pytest.approx(8 + 14 / 60)

    def test_midnight_and_late(self):
        assert parse_hhmm("00:00") == 0.0
        assert parse_hhmm("23:45") == pytest.approx(23.75)


class TestTideHeightAt:
    EXTREMES = [
        {"t": 2.0, "h": 4.0, "type": "high", "time": "02:00"},
        {"t": 8.0, "h": 1.0, "type": "low", "time": "08:00"},
        {"t": 14.0, "h": 4.0, "type": "high", "time": "14:00"},
    ]

    def test_passes_through_each_extreme(self):
        for e in self.EXTREMES:
            assert tide_height_at(e["t"], self.EXTREMES) == pytest.approx(e["h"])

    def test_midpoint_equals_mean(self):
        # Cosine interpolation at the temporal midpoint equals the mean height
        assert tide_height_at(5.0, self.EXTREMES) == pytest.approx(2.5)
        assert tide_height_at(11.0, self.EXTREMES) == pytest.approx(2.5)

    def test_monotonic_between_extremes(self):
        # Falling limb from the 02:00 high to the 08:00 low
        samples = [tide_height_at(2.0 + 0.5 * i, self.EXTREMES) for i in range(13)]
        assert samples == sorted(samples, reverse=True)

    def test_clamps_outside_span(self):
        assert tide_height_at(-3.0, self.EXTREMES) == pytest.approx(4.0)
        assert tide_height_at(30.0, self.EXTREMES) == pytest.approx(4.0)

    def test_handles_negative_heights(self):
        extremes = [
            {"t": 0.0, "h": -0.4, "type": "low", "time": "00:00"},
            {"t": 6.0, "h": 3.6, "type": "high", "time": "06:00"},
        ]
        # midpoint mean of -0.4 and 3.6 is 1.6
        assert tide_height_at(3.0, extremes) == pytest.approx(1.6)
        # stays within the endpoints
        assert min(tide_height_at(6.0 * i / 20, extremes) for i in range(21)) >= -0.4 - 1e-9


class TestAssembleExtremes:
    DAYS = [
        {"date": "2026-07-10", "events": [
            {"time": "13:37", "height": 4.2, "type": "high"},
            {"time": "19:54", "height": 1.1, "type": "low"},
        ]},
        {"date": "2026-07-11", "events": [
            {"time": "01:58", "height": 4.1, "type": "high"},
            {"time": "08:14", "height": 0.9, "type": "low"},
            {"time": "14:21", "height": 4.3, "type": "high"},
            {"time": "20:38", "height": 1.0, "type": "low"},
        ]},
        {"date": "2026-07-12", "events": [
            {"time": "02:42", "height": 4.2, "type": "high"},
            {"time": "08:58", "height": 0.8, "type": "low"},
        ]},
    ]

    def test_target_events_present_and_sorted(self):
        extremes = assemble_extremes(self.DAYS, "2026-07-11")
        core = [e for e in extremes if 0.0 <= e["t"] <= 24.0]
        assert [e["time"] for e in core] == ["01:58", "08:14", "14:21", "20:38"]
        assert extremes == sorted(extremes, key=lambda e: e["t"])

    def test_uses_adjacent_day_events(self):
        extremes = assemble_extremes(self.DAYS, "2026-07-11")
        # previous day's last event (19:54) lands at 19.9 - 24 = negative t
        assert extremes[0]["time"] == "19:54"
        assert extremes[0]["t"] == pytest.approx(parse_hhmm("19:54") - 24)
        # next day's first event (02:42) lands past 24 h
        assert extremes[-1]["time"] == "02:42"
        assert extremes[-1]["t"] == pytest.approx(parse_hhmm("02:42") + 24)

    def test_synthesizes_pad_when_no_neighbour(self):
        only = [d for d in self.DAYS if d["date"] == "2026-07-11"]
        extremes = assemble_extremes(only, "2026-07-11")
        left, right = extremes[0], extremes[-1]
        # left pad is opposite type of the first real event (a high), 6.21 h before it
        assert left["time"] is None
        assert left["type"] == "low"
        assert left["t"] == pytest.approx(parse_hhmm("01:58") - render_report.SEMIDIURNAL_HALF_CYCLE_H)
        assert left["h"] == pytest.approx(0.9)  # nearest low height reused
        # right pad is opposite type of the last real event (a low), 6.21 h after it
        assert right["time"] is None
        assert right["type"] == "high"
        assert right["t"] == pytest.approx(parse_hhmm("20:38") + render_report.SEMIDIURNAL_HALF_CYCLE_H)
        assert right["h"] == pytest.approx(4.3)  # nearest high height reused

    def test_missing_target_day_returns_empty(self):
        assert assemble_extremes(self.DAYS, "2026-01-01") == []
        assert assemble_extremes(None, "2026-07-11") == []


class TestDashboardOutputPath:
    def _package(self, verdict):
        return {
            "analysis": {"target_day": {"verdict": verdict}},
            "conditions": {"report": {"filenames": {
                "go": "reports/2026-07-11-mundaka-go.md",
                "check": "reports/2026-07-11-mundaka-check.md",
                "skip": "reports/2026-07-11-mundaka-skip.md",
            }}},
        }

    def test_no_verdict_slug_in_stable_name(self):
        # Any verdict yields the same stable dashboard name (no -go/-check/-skip).
        for verdict in ("go", "check", "skip"):
            assert dashboard_output_path(self._package(verdict)) == \
                "reports/2026-07-11-mundaka-dashboard.html"

    def test_missing_verdict_raises(self):
        with pytest.raises((KeyError, TypeError)):
            dashboard_output_path({"analysis": {"target_day": {}}, "conditions": {"report": {"filenames": {}}}})

    def test_missing_filenames_raises(self):
        with pytest.raises((KeyError, TypeError)):
            dashboard_output_path({"analysis": {"target_day": {"verdict": "go"}}, "conditions": {"report": {}}})


class TestTideSvg:
    EXTREMES = [
        {"t": -3.0, "h": 1.1, "type": "low", "time": None},
        {"t": 1.9667, "h": 4.1, "type": "high", "time": "01:58"},
        {"t": 8.2333, "h": 0.9, "type": "low", "time": "08:14"},
        {"t": 14.35, "h": 4.3, "type": "high", "time": "14:21"},
        {"t": 20.6333, "h": 1.0, "type": "low", "time": "20:38"},
        {"t": 26.8, "h": 4.3, "type": "high", "time": None},
    ]
    WINDOWS = [
        {"from": "07:00", "to": "10:30", "label": "Dawn patrol"},
        {"from": "19:00", "to": "21:00", "label": "Evening glass-off"},
    ]
    DAYLIGHT = {"first_light": "06:12", "last_light": "22:23"}

    def test_window_labels_present(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        assert "Dawn patrol" in svg
        assert "Evening glass-off" in svg
        assert svg.count('class="tide-window"') == 2

    def _x_at(self, hour):
        # x-mapping for the cropped dawn..dusk domain of the DAYLIGHT fixture
        t0 = parse_hhmm(self.DAYLIGHT["first_light"])
        t1 = parse_hhmm(self.DAYLIGHT["last_light"])
        return render_report._PAD_L + ((hour - t0) / (t1 - t0)) * render_report._PLOT_W

    def test_window_rect_x_matches_from_time(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        # dawn patrol starts at 07:00, positioned on the cropped domain
        assert f'x="{self._x_at(7.0):.2f}"' in svg

    def test_chart_cropped_to_daylight(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        # night shading is gone: the axis itself spans dawn..dusk now
        assert 'class="tide-night"' not in svg
        # dawn/dusk endpoint times label the axis ends
        assert self.DAYLIGHT["first_light"] in svg
        assert self.DAYLIGHT["last_light"] in svg
        # the curve starts at the left edge (first_light) and ends at the right
        left = render_report._PAD_L
        right = render_report._PAD_L + render_report._PLOT_W
        assert f'points="{left:.2f},' in svg
        assert f' {right:.2f},' in svg

    def test_extreme_markers_labeled_with_unit(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        # 01:58 high is before dawn (06:12) so it is cropped out; daytime
        # extremes remain, labelled with unit.
        assert "High 01:58" not in svg
        assert "Low 08:14 · 0.9 m" in svg
        assert "High 14:21 · 4.3 m" in svg
        # three real daytime extremes; synthetic pads (time=None) never labelled
        assert svg.count('class="tide-dot"') == 3

    def test_negative_heights_do_not_crash(self):
        extremes = [
            {"t": 0.0, "h": -0.5, "type": "low", "time": "00:00"},
            {"t": 6.0, "h": 3.5, "type": "high", "time": "06:00"},
            {"t": 12.0, "h": -0.3, "type": "low", "time": "12:00"},
        ]
        svg = tide_svg(extremes, [], None, "m")
        assert "<svg" in svg and "</svg>" in svg

    HOURS = [
        {"time": "07:00", "swell_height": 1.3, "swell_period_s": 12.0,
         "swell_direction_deg": 315.0, "wind_speed": 8, "wind_direction_deg": 90.0,
         "wind_type": "offshore", "quality": {"score": 7.2, "rating": "good"}},
        {"time": "13:00", "swell_height": 1.5, "swell_period_s": 12.0,
         "swell_direction_deg": 292.0, "wind_speed": 16, "wind_direction_deg": 225.0,
         "wind_type": "cross-shore", "quality": {"score": 5.4, "rating": "fair"}},
        {"time": "17:00", "swell_height": 1.2, "swell_period_s": 11.0,
         "swell_direction_deg": 292.0, "wind_speed": 22, "wind_direction_deg": 247.0,
         "wind_type": "onshore", "quality": {"score": 3.1, "rating": "poor"}},
    ]

    def test_no_strip_without_hours(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        assert "strip-bar" not in svg
        assert (
            f'viewBox="0 0 {render_report._SVG_W:.2f} {render_report._SVG_H:.2f}"' in svg
        )

    def test_strip_renders_bar_and_arrow_per_hour(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", self.HOURS, "m", "km/h")
        assert svg.count('class="strip-bar') == 3
        assert svg.count('class="strip-arrow') == 3
        total = render_report._SVG_H + render_report._STRIP_H
        assert f'viewBox="0 0 {render_report._SVG_W:.2f} {total:.2f}"' in svg

    def test_strip_quality_and_wind_classes(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", self.HOURS, "m", "km/h")
        assert "strip-bar q-go" in svg  # good rating
        assert "strip-bar q-check" in svg  # fair rating
        assert "strip-bar q-skip" in svg  # poor rating
        assert "strip-arrow wind-off" in svg
        assert "strip-arrow wind-on" in svg
        assert "strip-arrow wind-cross" in svg

    def test_strip_bar_aligns_with_hour_x(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", self.HOURS, "m", "km/h")
        cx = self._x_at(7.0)  # 07:00 bar centres on the cropped domain
        col_w = render_report._PLOT_W / 24.0
        bar_w = max(4.0, min(col_w * 0.55, 20.0))
        assert f'x="{cx - bar_w / 2:.2f}"' in svg

    def test_strip_wind_arrow_points_downwind(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", self.HOURS, "m", "km/h")
        # wind from 90 deg blows toward 270; arrow rotated by (90 + 180) % 360
        cx = self._x_at(7.0)
        assert f'rotate(270.00 {cx:.2f} ' in svg

    def test_mid_tide_split_fill_present(self):
        # The two-tone mid-tide split: a high band and a low band, both clipped,
        # plus a dashed mid line. No plain single-tone baseline fill remains.
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        assert 'class="tide-fill-high"' in svg
        assert 'class="tide-fill-low"' in svg
        assert 'class="tide-mid"' in svg
        assert 'class="tide-fill"' not in svg  # old baseline fill is gone

    def test_mid_tide_clip_ids_use_prefix(self):
        # Clip ids carry the id_prefix so many charts coexist in one document.
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", id_prefix="fc3")
        assert 'id="fc3-tide-hi"' in svg
        assert 'clip-path="url(#fc3-tide-hi)"' in svg
        assert 'id="fc3-tide-lo"' in svg
        # a different prefix yields non-colliding ids
        other = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", id_prefix="t")
        assert 'id="t-tide-hi"' in other and 'id="fc3-tide-hi"' not in other

    def test_per_hour_x_axis(self):
        # A light vertical gridline every hour inside the daylight window, with
        # every second (even) hour drawn stronger; the old coarse grid is gone.
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        assert 'class="tide-grid"' not in svg
        assert 'class="tide-hgrid"' in svg
        assert 'class="tide-hgrid tide-hgrid-major"' in svg
        # 06:12-22:23 daylight -> hours 07..22 (endpoints inside 0.6 h skipped),
        # so both odd (minor) and even (major) hour gridlines are present.
        assert svg.count('class="tide-hgrid"') >= 1
        assert svg.count('tide-hgrid-major') >= 1

    def test_strip_row_labels_relabeled_left_with_units(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m", self.HOURS, "m", "km/h")
        assert ">Swell (m)</text>" in svg
        assert ">Period (s)</text>" in svg
        assert ">Wind (km/h)</text>" in svg
        # the crowded right-edge unit caption is gone
        assert 'class="strip-unit"' not in svg


class TestHourlyStripInDashboard:
    def _today_slice(self, out):
        return out[out.index('id="panel-today"'):out.index('id="panel-forecast"')]

    def test_clips_to_daylight_hours(self):
        # Fixture marine day carries 24 hours; the Today chart spans dawn..dusk
        # (06:12-22:23), so only hours 07..22 fall inside it: 16 bars. (The
        # Forecast panel draws its own per-day strips; scope to Today here.)
        today = self._today_slice(render_dashboard(load_package()))
        assert today.count('class="strip-bar') == 16

    def test_strip_absent_without_marine_hours(self):
        pkg = load_package()
        pkg["conditions"]["marine"] = {"days": []}
        # No SVG bars emitted anywhere (the CSS class definitions always exist).
        assert 'class="strip-bar' not in render_dashboard(pkg)


# ---------------------------------------------------------------------------
# Dashboard shell: tab bar, panels, toggle script
# ---------------------------------------------------------------------------


class TestRenderDashboard:
    def test_four_tab_buttons_in_order(self):
        out = render_dashboard(load_package())
        positions = [out.index(f'id="tab-{k}"') for k in ("today", "forecast", "windows", "info")]
        assert positions == sorted(positions)
        assert out.count('class="tab"') == 4
        # human-readable labels, in order
        for label in ("Today", "Forecast", "Windows", "Spot info"):
            assert f">{label}</button>" in out

    def test_four_panels_in_order(self):
        out = render_dashboard(load_package())
        positions = [out.index(f'id="panel-{k}"') for k in ("today", "forecast", "windows", "info")]
        assert positions == sorted(positions)
        assert out.count('class="panel"') == 4

    def test_toggle_script_present(self):
        out = render_dashboard(load_package())
        # the show/hide behaviour and hash-based initial tab
        assert "addEventListener('click'" in out
        assert "hashchange" in out
        assert ".hidden" in out

    def test_today_panel_carries_verdict_tide_and_strip(self):
        out = render_dashboard(load_package())
        today = out[out.index('id="panel-today"'):out.index('id="panel-forecast"')]
        assert "chip-go" in today  # target-day verdict chip
        assert 'class="tide-chart"' in today  # tide curve
        assert 'class="strip-bar' in today  # aligned hourly strip
        assert "tide &amp; session windows" in today

    def test_info_panel_is_placeholder(self):
        out = render_dashboard(load_package())
        rest = out[out.index('id="panel-info"'):]
        assert rest.count('class="placeholder"') == 1  # info only
        assert "later update" in rest
        # the populated Today/Forecast tide charts do not leak into the placeholder
        assert 'class="tide-chart"' not in rest

    def test_map_hero_and_invalidate_on_today(self):
        out = render_dashboard(load_package())
        assert 'id="surf-map"' in out
        assert "window.__surfMap" in out  # exposed for invalidateSize on tab show
        assert "invalidateSize" in out

    def test_missing_target_day_raises(self):
        pkg = load_package()
        del pkg["analysis"]["target_day"]
        with pytest.raises((KeyError, TypeError)):
            render_dashboard(pkg)

    def test_footer_names_twin_by_relationship_not_a_guessed_filename(self):
        # The twin's real path depends on the CLI --out override the renderer
        # never sees, so the footer must not print a specific (possibly wrong)
        # filename; it names the twin by its relationship instead.
        out = render_dashboard(load_package())
        assert "paired Markdown twin is saved alongside" in out
        assert "-dashboard.md" not in out


class TestForecastPanel:
    def _forecast_slice(self, out):
        return out[out.index('id="panel-forecast"'):out.index('id="panel-windows"')]

    def test_week_at_a_glance_overview(self):
        fc = self._forecast_slice(render_dashboard(load_package()))
        assert "Week at a glance" in fc
        # one compressed 7-day overview, one column per day
        assert fc.count('class="tide-chart tide-week"') == 1
        assert fc.count('class="tide-week-day"') == 7
        assert "night hours are not drawn" in fc  # daylight-clipping stated
        # refinements: mid-tide split (dashed mid line + two-tone), weekday
        # labels above each column, hour ticks and per-day high/low tide times
        assert 'class="tide-mid"' in fc
        assert 'class="tide-fill-high"' in fc and 'class="tide-fill-low"' in fc
        assert 'class="tide-week-label"' in fc
        assert 'class="tide-week-tick"' in fc
        assert 'class="tide-week-time"' in fc

    def test_by_day_seven_selector_rows(self):
        fc = self._forecast_slice(render_dashboard(load_package()))
        assert "By day" in fc
        assert fc.count('<button class="fc-crow') == 7
        # verdicts corrected to the spot: go/check/skip all appear in the fixture
        assert "chip-go" in fc and "chip-check" in fc and "chip-skip" in fc
        # compact GO / CHECK / SKIP chip labels (not the long "WORTH A CHECK")
        assert "CHECK" in fc and "WORTH A CHECK" not in fc
        # swell + one-line description from analysis.week rows
        assert "1.2 m NW @ 13 s" in fc
        assert "clean groundswell, light offshore, pushing tide" in fc

    def test_first_day_selected_by_default(self):
        fc = self._forecast_slice(render_dashboard(load_package()))
        assert fc.count('class="fc-detail"') == 7
        # the first row is active; its detail chart is shown (not hidden) while
        # the other six detail blocks are hidden until selected.
        assert '<button class="fc-crow active"' in fc
        assert '<div class="fc-detail" data-day="0">' in fc
        for i in range(1, 7):
            assert f'<div class="fc-detail" data-day="{i}" hidden>' in fc

    def test_detail_charts_carry_strip_and_relabels_with_unique_clips(self):
        fc = self._forecast_slice(render_dashboard(load_package()))
        # every day now carries hourly data, so each detail chart has a strip
        assert fc.count('class="strip-bar') == 7 * 16
        assert ">Swell (m)</text>" in fc
        assert ">Period (s)</text>" in fc
        assert ">Wind (km/h)</text>" in fc
        # unique clip-path ids per day so no url(#..) collision drops a band
        for i in range(7):
            assert f'id="fc{i}-tide-hi"' in fc

    def test_day_selector_toggle_script_present(self):
        out = render_dashboard(load_package())
        assert "panel-forecast" in out
        assert ".fc-crow" in out
        assert "d.hidden" in out  # swaps which detail is shown

    def test_no_tide_data_keeps_rows_and_degrades_chart(self):
        pkg = load_package()
        pkg["conditions"]["tides"] = {"error": "no station", "note": "manual"}
        fc = self._forecast_slice(render_dashboard(pkg))
        assert fc.count('<button class="fc-crow') == 7  # rows unaffected
        assert "tide-week" not in fc  # overview degrades to a note
        assert "No automated tide data" in fc

    def test_empty_week_renders_placeholder(self):
        pkg = load_package()
        pkg["analysis"]["week"] = []
        fc = self._forecast_slice(render_dashboard(pkg))
        assert "No 7-day forecast is available" in fc
        assert "tide-week" not in fc


class TestWindowsPanel:
    def _windows_slice(self, out):
        return out[out.index('id="panel-windows"'):out.index('id="panel-info"')]

    def test_windows_in_date_order_no_rank_badge(self):
        win = self._windows_slice(render_dashboard(load_package()))
        assert "Best windows this week" in win
        # one item per window in the fixture (4)
        assert win.count('class="al-item') == 4
        # rows run in DATE order (ascending), not the fixture's best-first order
        dates = ["Sat 11-07-2026", "Wed 15-07-2026", "Thu 16-07-2026", "Fri 17-07-2026"]
        positions = [win.index(d) for d in dates]
        assert positions == sorted(positions)
        # no numeric rank badge is emitted
        assert 'class="win-num"' not in win

    def test_dates_are_ddmmyyyy(self):
        win = self._windows_slice(render_dashboard(load_package()))
        assert "Sat 11-07-2026" in win  # DD-MM-YYYY, weekday kept
        assert "2026-07-11" not in win  # the ISO form is not shown

    def test_each_window_carries_time_verdict_swell_wind(self):
        win = self._windows_slice(render_dashboard(load_package()))
        assert "07:15-10:00" in win  # recommended time
        assert "Dawn patrol" in win  # window label
        assert "1.5 m NW @ 14 s" in win  # swell
        assert "6 km/h offshore" in win  # wind
        assert "chip-go" in win and "chip-check" in win  # verdict chips

    def test_earliest_day_open_by_default(self):
        win = self._windows_slice(render_dashboard(load_package()))
        # exactly one item open on load, and it is the first (earliest) one
        assert win.count('class="al-item open"') == 1
        assert win.index('class="al-item open"') < win.index('class="al-item"')

    def test_each_day_has_its_own_chart_multi_open_capable(self):
        out = render_dashboard(load_package())
        win = self._windows_slice(out)
        # one pre-rendered tide chart per window (multi-open: each item owns one)
        assert win.count('class="al-chart"') == 4
        assert win.count('class="tide-chart') == 4
        # toggle is independent per row (not the single-select Forecast sweep)
        assert "classList.toggle('open')" in out
        assert win.count('class="al-row"') == 4

    def test_window_shaded_on_its_own_day_chart(self):
        win = self._windows_slice(render_dashboard(load_package()))
        # the recommended window is drawn as a shaded band with its label on the
        # chart (tide_svg shades any windows passed to it)
        assert "tide-window" in win
        assert win.count("Dawn patrol") >= 3  # 3 of the 4 fixture windows use it

    def test_works_on_correction_reason_shown(self):
        win = self._windows_slice(render_dashboard(load_package()))
        # the reasoning that places each window sits below the day (al-why)
        assert 'class="al-why"' in win
        assert "works-on window" in win
        assert "mid-incoming tide" in win
        assert "dropped as outside the window" in win

    def test_no_windows_renders_empty_state(self):
        pkg = load_package()
        pkg["analysis"].pop("windows", None)
        win = self._windows_slice(render_dashboard(pkg))
        assert "No standout session windows" in win
        assert 'class="al-item' not in win

    def test_windows_absent_key_treated_as_empty(self):
        pkg = load_package()
        pkg["analysis"]["windows"] = []
        win = self._windows_slice(render_dashboard(pkg))
        assert "No standout session windows" in win


class TestWeekTideSvg:
    """Focused 7-day tide-geometry tests (like the single-day _x_at tests)."""

    def _days(self):
        return render_report._forecast_view(load_package())["days"]

    def _seg_bounds(self, i, n):
        plot_left = render_report._PAD_L
        plot_right = render_report._SVG_W - render_report._PAD_R
        col_w = (plot_right - plot_left) / n
        gap = render_report._WEEK_DAY_GAP
        return plot_left + i * col_w + gap / 2, plot_left + (i + 1) * col_w - gap / 2

    def test_one_group_per_day_labelled_by_date(self):
        days = self._days()
        svg = week_tide_svg(days, "m")
        assert svg.count('class="tide-week-day"') == len(days)
        for d in days:
            assert f'data-day="{d["date"]}"' in svg

    def test_each_segment_spans_its_daylight_window(self):
        days = self._days()
        svg = week_tide_svg(days, "m")
        n = len(days)
        # shared y scale, replicated from the implementation
        all_h = [e["h"] for d in days for e in d["extremes"]]
        rng = (max(all_h) - min(all_h)) or 1.0
        y_lo, y_hi = min(all_h) - rng * 0.15, max(all_h) + rng * 0.15

        def y(h):
            return render_report._WEEK_PAD_T + (y_hi - h) / (y_hi - y_lo) * render_report._WEEK_PLOT_H

        for i, d in enumerate(days):
            left, right = self._seg_bounds(i, n)
            t0 = parse_hhmm(d["daylight"]["first_light"])
            t1 = parse_hhmm(d["daylight"]["last_light"])
            span = (t1 - t0) or 24.0
            # the day's curve starts at first_light (seg left) and ends at
            # last_light (seg right): x is tied to the daylight window, not the
            # whole day, so night hours are collapsed out.
            y0 = y(tide_height_at(t0, d["extremes"]))
            y1 = y(tide_height_at(t0 + span, d["extremes"]))
            assert f'points="{left:.2f},{y0:.2f}' in svg
            assert f' {right:.2f},{y1:.2f}"' in svg

    def test_night_gap_between_days_is_not_drawn(self):
        days = self._days()
        n = len(days)
        # adjacent day columns leave an undrawn gutter (the collapsed night):
        # each segment ends before the next one begins.
        for i in range(n - 1):
            _, right_i = self._seg_bounds(i, n)
            left_next, _ = self._seg_bounds(i + 1, n)
            assert right_i < left_next

    def test_empty_or_tideless_days_return_empty_string(self):
        assert week_tide_svg([], "m") == ""
        assert week_tide_svg([{"date": "x", "label": "Mon", "extremes": []}], "m") == ""

    def test_missing_daylight_falls_back_to_full_day(self):
        days = self._days()
        for d in days:
            d["daylight"] = None
        svg = week_tide_svg(days, "m")
        assert svg.count('class="tide-week-day"') == len(days)  # still one column per day

    def test_overview_refinements_present(self):
        days = self._days()
        svg = week_tide_svg(days, "m")
        # one dashed mid line across the whole width, shared two-tone clip bands
        assert svg.count('class="tide-mid"') == 1
        assert 'id="wk-tide-hi"' in svg and 'id="wk-tide-lo"' in svg
        assert svg.count('class="tide-fill-high"') == len(days)  # one band per day
        assert svg.count('class="tide-fill-low"') == len(days)
        # weekday labels, hour ticks and per-day high/low tide times
        assert svg.count('class="tide-week-label"') == len(days)
        assert 'class="tide-week-tick"' in svg
        assert 'class="tide-week-time"' in svg

    def test_weekday_label_sits_above_each_column(self):
        days = self._days()
        svg = week_tide_svg(days, "m")
        # the weekday label y is above the plot top (in the top pad), not below
        # the baseline as the old design placed it.
        label_y = render_report._WEEK_PAD_T - 21
        assert f'class="tide-week-label" x=' in svg
        assert f'y="{label_y:.2f}"' in svg

    def test_half_height_viewbox(self):
        svg = week_tide_svg(self._days(), "m")
        # the overview is ~half the single-day chart height
        assert render_report._WEEK_SVG_H == 132.0
        assert f'viewBox="0 0 {render_report._SVG_W:.2f} 132.00"' in svg


# ---------------------------------------------------------------------------
# Renderer degradation, escaping, self-containment
# ---------------------------------------------------------------------------


class TestRenderDegradation:
    def test_no_tides_renders_note_and_windows(self):
        pkg = load_package()
        pkg["conditions"]["tides"] = {"error": "no station", "note": "Check tide-forecast.com manually."}
        out = render_dashboard(pkg)
        assert "tide-forecast.com" in out
        assert "Dawn patrol" in out  # session windows still listed
        assert '<svg class="tide-chart"' not in out  # no SVG curve drawn

    def test_missing_one_liner_omits_sub(self):
        pkg = load_package()
        pkg["analysis"]["target_day"].pop("one_liner")
        out = render_dashboard(pkg)
        assert 'class="overlay"' in out

    def test_missing_windows_still_renders_curve(self):
        pkg = load_package()
        pkg["analysis"]["target_day"].pop("windows")
        out = render_dashboard(pkg)
        assert "tide-curve" in out


class TestRenderEscaping:
    def test_hostile_spot_name_escaped(self):
        pkg = load_package()
        pkg["conditions"]["spot"]["name"] = "<script>alert(1)</script>"
        out = render_dashboard(pkg)
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out

    def test_hostile_one_liner_escaped(self):
        pkg = load_package()
        pkg["analysis"]["target_day"]["one_liner"] = "<img src=x onerror=alert(1)>"
        out = render_dashboard(pkg)
        assert "<img src=x onerror=alert(1)>" not in out
        assert "&lt;img" in out


class TestSelfContainment:
    def test_no_external_script_or_link_tags(self):
        out = render_dashboard(load_package())
        assert "<script src=" not in out
        assert "<link " not in out
        assert "unpkg" not in out

    def test_only_osm_tile_url_is_remote(self):
        # Strip the inlined vendor blocks first: Leaflet's own source carries
        # http strings (the SVG namespace, its banner, CSS bug-tracker
        # comments) that are not resource loads. What matters is that OUR
        # emitted markup adds no remote reference except the OSM tile template.
        out = render_dashboard(load_package())
        ours = out.replace(render_report._read_vendor("leaflet.css"), "")
        ours = ours.replace(render_report._read_vendor("leaflet.js"), "")
        assert ours.count("http") == 1
        assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in ours

    def test_leaflet_inlined(self):
        out = render_dashboard(load_package())
        assert "Leaflet 1.9.4" in out  # from the vendored leaflet.js banner


class TestRenderDeterminism:
    def test_render_is_stable_across_calls(self):
        pkg = load_package()
        assert render_dashboard(pkg) == render_dashboard(pkg)

    def test_both_color_schemes_present(self):
        # Light base palette, an explicit dark palette on [data-theme="dark"],
        # and a prefers-color-scheme fallback for the default "auto" choice.
        out = render_dashboard(load_package())
        assert "--page: #eef1f4" in out  # light base
        assert "prefers-color-scheme: dark" in out
        assert "--page: #0d1520" in out  # dark palette
        assert ':root[data-theme="dark"]' in out  # explicit dark override
        # auto/absent follows the system; explicit light always wins
        assert ':root:not([data-theme="light"]):not([data-theme="dark"])' in out


class TestThemeControl:
    def test_segmented_control_offers_auto_light_dark(self):
        out = render_dashboard(load_package())
        assert 'class="theme-seg"' in out
        assert 'data-theme-choice="auto"' in out
        assert 'data-theme-choice="light"' in out
        assert 'data-theme-choice="dark"' in out

    def test_auto_is_the_default_selection(self):
        # Auto is pre-pressed so a first load with no stored choice matches the
        # page's actual state (data-theme unset => follows the system).
        out = render_dashboard(load_package())
        seg = out[out.index('class="theme-seg"'):]
        seg = seg[: seg.index("</div>")]
        assert 'data-theme-choice="auto" aria-pressed="true"' in seg
        assert 'data-theme-choice="light" aria-pressed="false"' in seg
        assert 'data-theme-choice="dark" aria-pressed="false"' in seg

    def test_light_and_dark_use_sun_and_moon_symbols(self):
        out = render_dashboard(load_package())
        assert "☀️" in out
        assert "\U0001f319" in out  # 🌙

    def test_head_init_prevents_theme_flash(self):
        # An inline head script applies a stored explicit choice before paint.
        out = render_dashboard(load_package())
        assert "surf-theme" in out
        assert out.index("surf-theme") < out.index("<body>")


# ---------------------------------------------------------------------------
# Flat Markdown twin
# ---------------------------------------------------------------------------


class TestDashboardMarkdown:
    def test_stacks_four_sections_in_order(self):
        md = render_dashboard_markdown(load_package())
        positions = [md.index(h) for h in ("## Today", "## Forecast", "## Windows", "## Spot info")]
        assert positions == sorted(positions)

    def test_today_section_carries_verdict_and_conditions(self):
        md = render_dashboard_markdown(load_package())
        today = md[md.index("## Today"):md.index("## Forecast")]
        assert "**Verdict:**" in today and "GO" in today
        assert "Clean 1.2 m NW groundswell" in today  # the one-liner
        # target-day tide events and session windows
        assert "High 14:21 · 4.3 m" in today
        assert "Dawn patrol: 07:00-10:30" in today

    def test_forecast_section_lists_seven_days(self):
        md = render_dashboard_markdown(load_package())
        forecast = md[md.index("## Forecast"):md.index("## Windows")]
        assert forecast.count("\n- **") == 7  # one bullet per week day
        assert "**Saturday 2026-07-11** - \U0001f7e2 GO" in forecast
        assert "1.2 m NW @ 13 s" in forecast  # swell + wind detail

    def test_windows_section_lists_windows_in_date_order(self):
        md = render_dashboard_markdown(load_package())
        windows = md[md.index("## Windows"):md.index("## Spot info")]
        # one bullet per window, in date order with DD-MM-YYYY dates
        assert windows.count("\n- **") == 4
        assert "**Sat 11-07-2026** 07:00-10:30" in windows  # earliest first, when + time
        order = [windows.index(d) for d in
                 ("Sat 11-07-2026", "Wed 15-07-2026", "Thu 16-07-2026", "Fri 17-07-2026")]
        assert order == sorted(order)
        assert "1.2 m NW @ 13 s" in windows  # swell detail
        assert "works-on window" in windows  # reasoning shown

    def test_info_section_is_placeholder(self):
        md = render_dashboard_markdown(load_package())
        assert md.count("later update") == 1  # info only

    def test_empty_windows_section_reads_as_checked_and_absent(self):
        pkg = load_package()
        pkg["analysis"]["windows"] = []
        md = render_dashboard_markdown(pkg)
        windows = md[md.index("## Windows"):md.index("## Spot info")]
        assert "No standout session windows" in windows

    def test_no_tides_falls_back_to_note(self):
        pkg = load_package()
        pkg["conditions"]["tides"] = {"error": "no station", "note": "Check tide-forecast.com manually."}
        md = render_dashboard_markdown(pkg)
        assert "tide-forecast.com" in md

    def test_deterministic_across_calls(self):
        pkg = load_package()
        assert render_dashboard_markdown(pkg) == render_dashboard_markdown(pkg)


# ---------------------------------------------------------------------------
# CLI contract (subprocess, real filesystem)
# ---------------------------------------------------------------------------


class TestCli:
    def test_writes_default_paths_and_prints_json(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"), "--data", str(PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["html_path"] == "reports/2026-07-11-mundaka-dashboard.html"
        assert payload["md_path"] == "reports/2026-07-11-mundaka-dashboard.md"
        html = tmp_path / "reports" / "2026-07-11-mundaka-dashboard.html"
        md = tmp_path / "reports" / "2026-07-11-mundaka-dashboard.md"
        assert html.is_file() and md.is_file()
        assert "<!DOCTYPE html>" in html.read_text(encoding="utf-8")
        assert "## Today" in md.read_text(encoding="utf-8")

    def test_out_override_writes_md_twin_alongside(self, tmp_path):
        out = tmp_path / "custom" / "report.html"
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--data", str(PACKAGE_PATH), "--out", str(out)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["html_path"] == str(out)
        assert out.is_file()
        assert out.with_suffix(".md").is_file()

    def test_broken_package_soft_fails_exit_0(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json")
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"), "--data", str(bad)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "error" in payload
        assert "note" in payload

    def test_missing_required_fields_soft_fails_exit_0(self, tmp_path):
        pkg = load_package()
        pkg["conditions"]["report"]["filenames"] = None
        p = tmp_path / "pkg.json"
        p.write_text(json.dumps(pkg))
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"), "--data", str(p)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "error" in json.loads(result.stdout)

    def test_missing_data_argument_exits_1(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py")],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "error" in json.loads(result.stdout)

    def test_unreadable_vendor_soft_fails(self, tmp_path, monkeypatch):
        # Point the vendor dir at an empty location so inlining raises OSError;
        # the CLI must still exit 0 with the soft-failure JSON.
        runner = CliRunner()
        monkeypatch.setattr(render_report, "VENDOR_DIR", tmp_path / "nope")
        out = tmp_path / "r.html"
        result = runner.invoke(cli, ["--data", str(PACKAGE_PATH), "--out", str(out)])
        assert result.exit_code == 0
        assert "error" in json.loads(result.output)


# ---------------------------------------------------------------------------
# Week planner (--mode week)
# ---------------------------------------------------------------------------


class TestWeekOutputPath:
    def test_default_path_from_week_start(self):
        assert week_output_path(load_week_package()) == "reports/2026-07-12-week.html"

    def test_missing_week_start_raises(self):
        with pytest.raises((KeyError, TypeError)):
            week_output_path({"mode": "week", "week": {}})


class TestRenderWeek:
    def test_all_spot_names_present(self):
        out = render_week(load_week_package())
        assert "Mundaka" in out
        assert "La Salvaje (Sopelana)" in out
        assert "Zarautz" in out

    def test_header_shows_range_and_spot_count(self):
        out = render_week(load_week_package())
        assert "2026-07-12" in out
        assert "2026-07-18" in out
        assert "3 spots" in out

    def test_verdict_chips_present(self):
        out = render_week(load_week_package())
        assert "chip-go" in out
        assert "chip-check" in out
        assert "chip-skip" in out
        assert "GO" in out and "WORTH A CHECK" in out and "SKIP" in out

    def test_unprofiled_flag_exact_text(self):
        out = render_week(load_week_package())
        assert "unprofiled - run /surfing:research zarautz" in out

    def test_verdict_source_shown(self):
        out = render_week(load_week_package())
        assert "works-on profile" in out
        assert "quality score (spot-agnostic)" in out

    def test_reresearch_chip_only_when_suggested(self):
        pkg = load_week_package()
        # No spot in the fixture suggests re-research, so no age chip appears.
        assert "consider re-researching" not in render_week(pkg)
        pkg["spots"][0]["reresearch_suggested"] = True
        pkg["spots"][0]["profile_age_days"] = 40
        out = render_week(pkg)
        assert "consider re-researching" in out
        assert "40" in out

    def test_ranking_order_preserved(self):
        out = render_week(load_week_package())
        # The fixture ranking is best-first: La Salvaje Thu, La Salvaje Wed,
        # Mundaka Thu, Zarautz Fri. Their why-strings must appear in that order.
        whys = [
            "Clean long-period NW at dawn before the sea breeze fills in",
            "Chest-high on the banks with SE offshore, best near the high",
            "First pulse over the rivermouth minimum, offshore on the push",
            "Fun size early before the cross-shore wind, decent quality score",
        ]
        positions = [out.index(w) for w in whys]
        assert positions == sorted(positions)

    def test_best_window_emphasized(self):
        out = render_week(load_week_package())
        assert "rank-best" in out

    def test_weekday_labels_from_dates(self):
        out = render_week(load_week_package())
        # 2026-07-16 is a Thursday (the best window's day).
        assert "Thu" in out

    def test_hostile_spot_name_escaped(self):
        pkg = load_week_package()
        pkg["spots"][0]["name"] = "<script>alert(1)</script>"
        out = render_week(pkg)
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out

    def test_both_color_schemes_present(self):
        out = render_week(load_week_package())
        assert "--page: #eef1f4" in out
        assert "prefers-color-scheme: dark" in out
        assert "--page: #0d1520" in out

    def test_leaflet_inlined_and_no_external_tags(self):
        out = render_week(load_week_package())
        assert "Leaflet 1.9.4" in out
        assert "<script src=" not in out
        assert "<link " not in out

    def test_deterministic_across_calls(self):
        pkg = load_week_package()
        assert render_week(pkg) == render_week(pkg)

    def test_wrong_mode_raises(self):
        pkg = load_week_package()
        pkg["mode"] = "single"
        with pytest.raises((KeyError, ValueError)):
            render_week(pkg)

    def test_missing_spots_raises(self):
        pkg = load_week_package()
        del pkg["spots"]
        with pytest.raises((KeyError, ValueError)):
            render_week(pkg)

    def test_missing_ranking_raises(self):
        pkg = load_week_package()
        del pkg["ranking"]
        with pytest.raises((KeyError, ValueError)):
            render_week(pkg)


class TestWeekCli:
    def test_writes_default_path_and_prints_json(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "week", "--data", str(WEEK_PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["html_path"] == "reports/2026-07-12-week.html"
        assert "md_path" not in payload  # week mode writes HTML only
        written = tmp_path / "reports" / "2026-07-12-week.html"
        assert written.is_file()
        assert "<!DOCTYPE html>" in written.read_text(encoding="utf-8")

    def test_out_override(self, tmp_path):
        out = tmp_path / "custom" / "week.html"
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "week", "--data", str(WEEK_PACKAGE_PATH), "--out", str(out)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["html_path"] == str(out)
        assert out.is_file()

    def test_dashboard_mode_is_default(self, tmp_path):
        # No --mode flag must write the dashboard, not the week planner.
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"), "--data", str(PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["html_path"] == "reports/2026-07-11-mundaka-dashboard.html"

    def test_week_package_missing_fields_soft_fails_exit_0(self, tmp_path):
        pkg = load_week_package()
        del pkg["ranking"]
        p = tmp_path / "pkg.json"
        p.write_text(json.dumps(pkg))
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "week", "--data", str(p)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "error" in payload and "note" in payload

    def test_dashboard_mode_on_week_package_soft_fails_exit_0(self, tmp_path):
        # A week package has no analysis.target_day, so dashboard mode soft-fails.
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "dashboard", "--data", str(WEEK_PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "error" in json.loads(result.stdout)

    def test_invalid_mode_exits_1(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "fortnight", "--data", str(WEEK_PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Golden files
# ---------------------------------------------------------------------------


class TestGolden:
    def test_matches_golden_html(self):
        assert render_dashboard(load_package()) == GOLDEN_PATH.read_text(encoding="utf-8")

    def test_matches_golden_markdown(self):
        assert render_dashboard_markdown(load_package()) == GOLDEN_MD_PATH.read_text(encoding="utf-8")

    def test_week_matches_golden(self):
        assert render_week(load_week_package()) == WEEK_GOLDEN_PATH.read_text(encoding="utf-8")
