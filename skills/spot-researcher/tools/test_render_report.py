"""Tests for render_report.py.

Unit tests cover the pure seams (tide geometry, path swapping, SVG output);
renderer/CLI tests cover graceful degradation, escaping, self-containment, and
the click contract. A golden-file test pins the full rendered document.

Regenerate the golden file after an INTENTIONAL design change with:
    cd skills/spot-researcher/tools && uv run python -c \
      "import json, render_report; \
       print(render_report.render(json.load(open('testdata/data-package.json'))), end='')" \
      > testdata/golden-report.html

Regenerate the week-planner golden file after an INTENTIONAL design change with:
    cd skills/spot-researcher/tools && uv run python -c \
      "import json, render_report; \
       print(render_report.render_week(json.load(open('testdata/week-data-package.json'))), end='')" \
      > testdata/golden-week-report.html
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import render_report
from render_report import (
    assemble_extremes,
    cli,
    html_output_path,
    parse_hhmm,
    render,
    render_week,
    tide_height_at,
    tide_svg,
    week_output_path,
)

TOOLS_DIR = Path(__file__).resolve().parent
TESTDATA = TOOLS_DIR / "testdata"
PACKAGE_PATH = TESTDATA / "data-package.json"
GOLDEN_PATH = TESTDATA / "golden-report.html"
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


class TestHtmlOutputPath:
    def _package(self, verdict):
        return {
            "analysis": {"target_day": {"verdict": verdict}},
            "conditions": {"report": {"filenames": {
                "go": "reports/2026-07-11-mundaka-go.md",
                "check": "reports/2026-07-11-mundaka-check.md",
                "skip": "reports/2026-07-11-mundaka-skip.md",
            }}},
        }

    def test_go_check_skip_variants(self):
        assert html_output_path(self._package("go")) == "reports/2026-07-11-mundaka-go.html"
        assert html_output_path(self._package("check")) == "reports/2026-07-11-mundaka-check.html"
        assert html_output_path(self._package("skip")) == "reports/2026-07-11-mundaka-skip.html"

    def test_missing_verdict_raises(self):
        with pytest.raises((KeyError, TypeError)):
            html_output_path({"analysis": {"target_day": {}}, "conditions": {"report": {"filenames": {}}}})

    def test_missing_filenames_raises(self):
        with pytest.raises((KeyError, TypeError)):
            html_output_path({"analysis": {"target_day": {"verdict": "go"}}, "conditions": {"report": {}}})


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

    def test_window_rect_x_matches_from_time(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        # dawn patrol starts at 07:00; compute its expected x with the module geometry
        expected_x = render_report._PAD_L + (7.0 / 24.0) * render_report._PLOT_W
        assert f'x="{expected_x:.2f}"' in svg

    def test_night_shading_matches_daylight(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        assert svg.count('class="tide-night"') == 2

        def x_at(hour):
            return render_report._PAD_L + (hour / 24.0) * render_report._PLOT_W

        first_light = parse_hhmm(self.DAYLIGHT["first_light"])  # 06:12
        last_light = parse_hhmm(self.DAYLIGHT["last_light"])  # 22:23
        # pre-dawn rect spans midnight to first light
        assert (
            f'class="tide-night" x="{x_at(0):.2f}" y="{render_report._PAD_T:.2f}" '
            f'width="{x_at(first_light) - x_at(0):.2f}"'
        ) in svg
        # post-dusk rect spans last light to midnight
        assert (
            f'class="tide-night" x="{x_at(last_light):.2f}" y="{render_report._PAD_T:.2f}" '
            f'width="{x_at(24) - x_at(last_light):.2f}"'
        ) in svg

    def test_extreme_markers_labeled_with_unit(self):
        svg = tide_svg(self.EXTREMES, self.WINDOWS, self.DAYLIGHT, "m")
        assert "High 01:58 · 4.1 m" in svg
        assert "Low 08:14 · 0.9 m" in svg
        # synthetic pads (time=None) are never labelled
        assert svg.count('class="tide-dot"') == 4

    def test_negative_heights_do_not_crash(self):
        extremes = [
            {"t": 0.0, "h": -0.5, "type": "low", "time": "00:00"},
            {"t": 6.0, "h": 3.5, "type": "high", "time": "06:00"},
            {"t": 12.0, "h": -0.3, "type": "low", "time": "12:00"},
        ]
        svg = tide_svg(extremes, [], None, "m")
        assert "<svg" in svg and "</svg>" in svg


# ---------------------------------------------------------------------------
# Renderer degradation, escaping, self-containment
# ---------------------------------------------------------------------------


class TestRenderDegradation:
    def test_full_package_has_all_sections_in_order(self):
        out = render(load_package())
        i_hero = out.index('class="hero"')
        i_tide = out.index("tide &amp; session windows")
        i_week = out.index("Week at a glance")
        i_cam = out.index(">Webcams<")
        i_haz = out.index(">Hazards<")
        i_foot = out.index('class="footer"')
        assert i_hero < i_tide < i_week < i_cam < i_haz < i_foot

    def test_no_tides_renders_note_and_windows(self):
        pkg = load_package()
        pkg["conditions"]["tides"] = {"error": "no station", "note": "Check tide-forecast.com manually."}
        out = render(pkg)
        assert "tide-forecast.com" in out
        assert "Dawn patrol" in out  # session windows still listed
        assert '<svg class="tide-chart"' not in out  # no SVG curve drawn

    def test_no_webcams_omits_section(self):
        pkg = load_package()
        pkg["spot_data"]["webcams"] = []
        out = render(pkg)
        assert ">Webcams<" not in out

    def test_empty_hazards_keeps_explicit_safety_line(self):
        pkg = load_package()
        pkg["spot_data"]["hazards"] = []
        out = render(pkg)
        assert ">Hazards<" in out
        assert "No hazards documented for this spot" in out

    def test_no_week_omits_section(self):
        pkg = load_package()
        pkg["analysis"]["week"] = []
        out = render(pkg)
        assert "Week at a glance" not in out

    def test_missing_one_liner_omits_sub(self):
        pkg = load_package()
        pkg["analysis"]["target_day"].pop("one_liner")
        out = render(pkg)
        assert 'class="overlay"' in out

    def test_missing_windows_still_renders_curve(self):
        pkg = load_package()
        pkg["analysis"]["target_day"].pop("windows")
        out = render(pkg)
        assert "tide-curve" in out


class TestRenderEscaping:
    def test_hostile_spot_name_escaped(self):
        pkg = load_package()
        pkg["conditions"]["spot"]["name"] = "<script>alert(1)</script>"
        out = render(pkg)
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out

    def test_hostile_hazard_escaped(self):
        pkg = load_package()
        pkg["spot_data"]["hazards"] = ["<img src=x onerror=alert(1)>"]
        out = render(pkg)
        assert "<img src=x onerror=alert(1)>" not in out
        assert "&lt;img" in out

    def test_hostile_webcam_url_attribute_escaped(self):
        pkg = load_package()
        pkg["spot_data"]["webcams"] = [{"name": "cam", "url": '"><script>x</script>', "free": True}]
        out = render(pkg)
        assert '"><script>x</script>' not in out


class TestSelfContainment:
    def _webcamless(self):
        pkg = load_package()
        pkg["spot_data"]["webcams"] = []
        return render(pkg)

    def test_no_external_script_or_link_tags(self):
        out = self._webcamless()
        assert "<script src=" not in out
        assert "<link " not in out
        assert "unpkg" not in out

    def test_only_osm_tile_url_is_remote(self):
        # Strip the inlined vendor blocks first: Leaflet's own source carries
        # http strings (the SVG namespace, its banner, CSS bug-tracker
        # comments) that are not resource loads. What matters is that OUR
        # emitted markup adds no remote reference except the OSM tile template.
        out = self._webcamless()
        ours = out.replace(render_report._read_vendor("leaflet.css"), "")
        ours = ours.replace(render_report._read_vendor("leaflet.js"), "")
        assert ours.count("http") == 1
        assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in ours

    def test_leaflet_inlined(self):
        out = self._webcamless()
        assert "Leaflet 1.9.4" in out  # from the vendored leaflet.js banner


class TestRenderDeterminism:
    def test_render_is_stable_across_calls(self):
        pkg = load_package()
        assert render(pkg) == render(pkg)

    def test_both_color_schemes_present(self):
        # Light base palette plus the prefers-color-scheme dark block; the page
        # never sets a data-theme attribute, so no such selectors belong here.
        out = render(load_package())
        assert "--page: #eef1f4" in out  # light base
        assert "prefers-color-scheme: dark" in out
        assert "--page: #0d1520" in out  # dark palette inside the media query
        assert "data-theme" not in out


# ---------------------------------------------------------------------------
# CLI contract (subprocess, real filesystem)
# ---------------------------------------------------------------------------


class TestCli:
    def test_writes_default_path_and_prints_json(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"), "--data", str(PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["html_path"] == "reports/2026-07-11-mundaka-go.html"
        written = tmp_path / "reports" / "2026-07-11-mundaka-go.html"
        assert written.is_file()
        assert "<!DOCTYPE html>" in written.read_text(encoding="utf-8")

    def test_out_override(self, tmp_path):
        out = tmp_path / "custom" / "report.html"
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--data", str(PACKAGE_PATH), "--out", str(out)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["html_path"] == str(out)
        assert out.is_file()

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

    def test_single_mode_default_unchanged(self, tmp_path):
        # --mode single (the default) must still write the single-report path.
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "single", "--data", str(PACKAGE_PATH)],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["html_path"] == "reports/2026-07-11-mundaka-go.html"

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

    def test_single_mode_on_week_package_soft_fails_exit_0(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "render_report.py"),
             "--mode", "single", "--data", str(WEEK_PACKAGE_PATH)],
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
# Golden file
# ---------------------------------------------------------------------------


class TestGolden:
    def test_matches_golden(self):
        assert render(load_package()) == GOLDEN_PATH.read_text(encoding="utf-8")

    def test_week_matches_golden(self):
        assert render_week(load_week_package()) == WEEK_GOLDEN_PATH.read_text(encoding="utf-8")
