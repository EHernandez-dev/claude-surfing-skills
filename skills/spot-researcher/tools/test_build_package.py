"""Tests for build_package.py.

Unit tests cover the verdict core (rating bands, works-on demotions, the
direction arc) and the display-string formatting; builder tests cover the
analysis block, spot_data mapping, and package assembly; CLI tests cover the
click contract (exit 0 with error JSON on file problems, exit 1 only for bad
arguments).
"""

import json

import pytest
import yaml
from click.testing import CliRunner

from build_package import (
    build_package,
    cli,
    compass_bearing,
    direction_tokens,
    draft_verdict,
    swell_string,
    window_label,
    window_span,
)


def make_window(
    date: str = "2026-07-20",
    best_time: str = "08:00",
    rating: str = "good",
    score: float = 6.5,
    swell_height: float = 1.2,
    swell_period_s: float | None = 13.0,
    swell_direction: str | None = "NW",
    wind: str | None = "12 km/h S (offshore)",
) -> dict:
    return {
        "date": date,
        "best_time": best_time,
        "rating": rating,
        "score": score,
        "swell_height": swell_height,
        "swell_period_s": swell_period_s,
        "swell_direction": swell_direction,
        "wind": wind,
    }


WORKS_ON = {
    "swell_direction": "NW",
    "swell_size": "1.0-1.5 m minimum to break",
    "min_period_s": 12,
    "wind": "S-SW offshore",
    "tide": "low to mid",
    "season": "October-March",
}


class TestCompassBearing:
    def test_cardinals(self):
        assert compass_bearing("N") == 0
        assert compass_bearing("E") == 90
        assert compass_bearing("SW") == 225

    def test_intermediates(self):
        assert compass_bearing("NW") == 315
        assert compass_bearing("WNW") == 292.5

    def test_unknown_token_is_none(self):
        assert compass_bearing("NNWW") is None
        assert compass_bearing("") is None


class TestDirectionTokens:
    def test_single_token(self):
        assert direction_tokens("NW") == [315]

    def test_range_with_dash(self):
        assert direction_tokens("S-SW") == [180, 225]

    def test_prose_range(self):
        assert direction_tokens("W to NW") == [270, 315]

    def test_prose_without_tokens_is_empty(self):
        assert direction_tokens("offshore") == []

    def test_none_is_empty(self):
        assert direction_tokens(None) == []

    def test_longest_token_wins(self):
        assert direction_tokens("WNW") == [292.5]


class TestDraftVerdictRatingBands:
    @pytest.mark.parametrize(
        "rating,expected",
        [("epic", "go"), ("good", "go"), ("fair", "check"), ("poor", "skip"), ("flat", "skip")],
    )
    def test_rating_maps_to_verdict(self, rating, expected):
        verdict, _ = draft_verdict(make_window(rating=rating), None)
        assert verdict == expected

    def test_missing_rating_is_skip(self):
        verdict, reasons = draft_verdict(make_window(rating=None), None)
        assert verdict == "skip"
        assert any("quality score" in r for r in reasons)

    def test_no_works_on_leaves_mapping_alone(self):
        verdict, reasons = draft_verdict(make_window(rating="good", swell_direction="S"), None)
        assert verdict == "go"
        assert reasons == []


class TestDraftVerdictPeriod:
    def test_below_minimum_is_hard_skip(self):
        verdict, reasons = draft_verdict(
            make_window(rating="epic", swell_period_s=8.0), WORKS_ON
        )
        assert verdict == "skip"
        assert any("8" in r and "12" in r for r in reasons)

    def test_at_minimum_passes(self):
        verdict, _ = draft_verdict(make_window(rating="good", swell_period_s=12.0), WORKS_ON)
        assert verdict == "go"

    def test_missing_period_does_not_demote(self):
        verdict, _ = draft_verdict(make_window(rating="good", swell_period_s=None), WORKS_ON)
        assert verdict == "go"

    def test_non_numeric_minimum_is_ignored(self):
        works_on = dict(WORKS_ON, min_period_s="long")
        verdict, _ = draft_verdict(make_window(rating="good", swell_period_s=8.0), works_on)
        assert verdict == "go"


class TestDraftVerdictDirection:
    def test_outside_arc_demotes_one_step(self):
        verdict, reasons = draft_verdict(
            make_window(rating="good", swell_direction="S"), WORKS_ON
        )
        assert verdict == "check"
        assert any("S" in r and "NW" in r for r in reasons)

    def test_fair_demotes_to_skip(self):
        verdict, _ = draft_verdict(make_window(rating="fair", swell_direction="S"), WORKS_ON)
        assert verdict == "skip"

    def test_inside_arc_passes(self):
        verdict, _ = draft_verdict(make_window(rating="good", swell_direction="WNW"), WORKS_ON)
        assert verdict == "go"

    def test_boundary_of_arc_is_inside(self):
        # W is exactly 45 degrees from NW: still inside the arc.
        verdict, _ = draft_verdict(make_window(rating="good", swell_direction="W"), WORKS_ON)
        assert verdict == "go"

    def test_multi_token_window_uses_nearest(self):
        works_on = dict(WORKS_ON, swell_direction="W to NW")
        verdict, _ = draft_verdict(make_window(rating="good", swell_direction="N"), works_on)
        assert verdict == "go"  # N is 45 from NW

    def test_unparseable_window_does_not_demote(self):
        works_on = dict(WORKS_ON, swell_direction="whatever the storm sends")
        verdict, _ = draft_verdict(make_window(rating="good", swell_direction="S"), works_on)
        assert verdict == "go"

    def test_missing_swell_direction_does_not_demote(self):
        verdict, _ = draft_verdict(make_window(rating="good", swell_direction=None), WORKS_ON)
        assert verdict == "go"

    def test_period_skip_and_direction_stays_skip(self):
        verdict, reasons = draft_verdict(
            make_window(rating="epic", swell_period_s=8.0, swell_direction="S"), WORKS_ON
        )
        assert verdict == "skip"
        assert len(reasons) == 2


METRIC_UNITS = {
    "system": "metric",
    "wave_height": "m",
    "tide_height": "m",
    "wind_speed": "km/h",
    "temperature": "°C",
}


class TestSwellString:
    def test_full_string(self):
        assert swell_string(1.2, "NW", 13.0, METRIC_UNITS) == "1.2 m NW @ 13 s"

    def test_trims_trailing_zero(self):
        assert swell_string(2.0, "W", 9.5, METRIC_UNITS) == "2 m W @ 9.5 s"

    def test_imperial_label(self):
        units = dict(METRIC_UNITS, system="imperial", wave_height="ft")
        assert swell_string(4.0, "NW", 13.0, units) == "4 ft NW @ 13 s"

    def test_missing_direction(self):
        assert swell_string(1.2, None, 13.0, METRIC_UNITS) == "1.2 m @ 13 s"

    def test_missing_period(self):
        assert swell_string(1.2, "NW", None, METRIC_UNITS) == "1.2 m NW"

    def test_all_missing_is_none(self):
        assert swell_string(None, None, None, METRIC_UNITS) is None


class TestWindowSpan:
    def test_block_start_to_block_end(self):
        assert window_span("08:00", "21:30") == ("08:00", "11:00")

    def test_clamped_best_time_keeps_block_end(self):
        # 06:12 is the 05:00 block clamped to first light; the block still ends 08:00.
        assert window_span("06:12", "21:30") == ("06:12", "08:00")

    def test_end_clipped_to_last_light(self):
        assert window_span("20:00", "21:30") == ("20:00", "21:30")


class TestWindowLabel:
    @pytest.mark.parametrize(
        "time,label",
        [
            ("06:12", "Dawn patrol"),
            ("08:00", "Dawn patrol"),
            ("09:00", "Morning"),
            ("12:30", "Midday"),
            ("15:00", "Afternoon"),
            ("18:00", "Evening"),
            ("20:00", "Evening"),
        ],
    )
    def test_time_of_day_labels(self, time, label):
        assert window_label(time) == label


# ---------------------------------------------------------------------------
# Package assembly
# ---------------------------------------------------------------------------

OMIT = object()


def make_payload(windows=OMIT, dates=None, target_date=OMIT, filenames=OMIT):
    if windows is OMIT:
        windows = [
            make_window(date="2026-07-20", rating="good", score=6.5),
            make_window(
                date="2026-07-21", best_time="20:00", rating="fair", score=4.2,
                swell_height=0.9, swell_period_s=10.0, swell_direction="W",
                wind="18 km/h NW (onshore)",
            ),
            make_window(
                date="2026-07-22", rating="poor", score=2.1,
                swell_height=0.4, swell_period_s=7.0, swell_direction="W",
            ),
        ]
    if dates is None:
        dates = [w["date"] for w in (windows or [])] or ["2026-07-20"]
        if "2026-07-23" not in dates:
            dates = dates + ["2026-07-23"]  # a day with no surf window
    if target_date is OMIT:
        target_date = dates[0]
    if filenames is OMIT:
        filenames = {
            v: f"reports/{target_date}-testville-{v}.md" for v in ("go", "check", "skip")
        }
    payload = {
        "spot": {"name": "Testville", "coordinates": [43.4, -2.7], "facing_deg": 315},
        "units": dict(METRIC_UNITS),
        "report": {
            "directory": "reports",
            "target_date": target_date,
            "spot_slug": "testville",
            "filenames": filenames,
        },
        "marine": {
            "days": [
                {
                    "date": d,
                    "summary": {
                        "wave_height_max": 1.5,
                        "swell_height_max": 1.3,
                        "swell_period_max_s": 10.0,
                        "swell_direction_dominant": "W",
                    },
                    "blocks": [],
                    "hours": [],
                }
                for d in dates
            ]
        },
        "daylight": {
            "days": [
                {"date": d, "first_light": "06:30", "last_light": "21:30"} for d in dates
            ]
        },
        "buoy": {"error": "no station nearby"},
        "tides": {"days": []},
        "sea_temperature": {},
        "weather": {},
        "gaps": ["buoy: no station nearby"],
    }
    if windows is not None:
        payload["surf_windows"] = windows
    return payload


PROFILE = {
    "name": "Testville",
    "region": "Testland",
    "works_on": dict(WORKS_ON),
    "break": {"type": "beach", "bottom": "sand", "direction": "left", "ability": "intermediate"},
    "peaks": [{"name": "Main peak", "character": "walls", "suits": "all"}],
    "hazards": ["rips by the jetty"],
    "webcams": [{"name": "cam", "url": "https://example.com/cam", "free": True}],
    "notes": "Tide-windowed; best on the push.",
}


class TestBuildPackageShape:
    def test_conditions_is_payload_verbatim(self):
        payload = make_payload()
        package = build_package(payload, PROFILE)
        assert package["conditions"] is payload

    def test_gaps_copied_from_payload(self):
        package = build_package(make_payload(), PROFILE)
        assert package["gaps"] == ["buoy: no station nearby"]

    def test_surfer_profile_passthrough(self):
        surfer = {"skill": "intermediate"}
        package = build_package(make_payload(), PROFILE, surfer=surfer)
        assert package["surfer_profile"] == surfer

    def test_no_surfer_no_key(self):
        assert "surfer_profile" not in build_package(make_payload(), PROFILE)

    def test_no_profile_no_spot_data(self):
        assert "spot_data" not in build_package(make_payload(), None)

    def test_missing_surf_windows_is_error(self):
        package = build_package(make_payload(windows=None), PROFILE)
        assert "error" in package
        assert "note" in package

    def test_no_marine_days_is_error(self):
        payload = make_payload()
        payload["marine"] = {"error": "api down"}
        package = build_package(payload, PROFILE)
        assert "error" in package
        assert "note" in package


class TestBuildPackageWeek:
    def test_one_row_per_forecast_day(self):
        package = build_package(make_payload(), PROFILE)
        assert [row["date"] for row in package["analysis"]["week"]] == [
            "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
        ]

    def test_row_fields_are_display_ready(self):
        row = build_package(make_payload(), PROFILE)["analysis"]["week"][0]
        assert row["verdict"] == "go"
        assert row["swell"] == "1.2 m NW @ 13 s"
        assert row["wind"] == "12 km/h S (offshore)"
        assert "good" in row["why"]

    def test_why_never_says_rating(self):
        # "rating" is not a user-facing term (CONTEXT.md, Quality score entry).
        for row in build_package(make_payload(), PROFILE)["analysis"]["week"]:
            assert "rating" not in row["why"]

    def test_demotion_reason_lands_in_why(self):
        # 2026-07-21: fair rating, W swell inside the arc but 10 s below the 12 s minimum.
        row = build_package(make_payload(), PROFILE)["analysis"]["week"][1]
        assert row["verdict"] == "skip"
        assert "minimum" in row["why"]

    def test_day_without_window_is_skip_from_summary(self):
        row = build_package(make_payload(), PROFILE)["analysis"]["week"][3]
        assert row["date"] == "2026-07-23"
        assert row["verdict"] == "skip"
        assert row["swell"] == "1.3 m W @ 10 s"
        assert row["wind"] is None
        assert "no daylight" in row["why"]


class TestBuildPackageTargetDay:
    def test_defaults_to_report_target_date(self):
        target = build_package(make_payload(), PROFILE)["analysis"]["target_day"]
        assert target["date"] == "2026-07-20"
        assert target["verdict"] == "go"

    def test_one_liner_ends_with_draft_tag(self):
        target = build_package(make_payload(), PROFILE)["analysis"]["target_day"]
        assert target["one_liner"].endswith("Computed call, no analyst pass.")
        assert "1.2 m NW @ 13 s" in target["one_liner"]

    def test_go_day_has_clipped_window(self):
        target = build_package(make_payload(), PROFILE)["analysis"]["target_day"]
        assert target["windows"] == [{"from": "08:00", "to": "11:00", "label": "Dawn patrol"}]

    def test_skip_day_has_no_windows(self):
        package = build_package(make_payload(), PROFILE, target_day="2026-07-22")
        assert package["analysis"]["target_day"]["windows"] == []

    def test_target_day_override_rewrites_report(self):
        package = build_package(make_payload(), PROFILE, target_day="2026-07-21")
        assert package["analysis"]["target_day"]["date"] == "2026-07-21"
        report = package["conditions"]["report"]
        assert report["target_date"] == "2026-07-21"
        assert report["filenames"]["go"] == "reports/2026-07-21-testville-go.md"

    def test_target_day_outside_window_is_error(self):
        package = build_package(make_payload(), PROFILE, target_day="2026-08-01")
        assert "error" in package
        assert "note" in package

    def test_missing_filenames_are_filled(self):
        payload = make_payload(filenames=None)
        package = build_package(payload, PROFILE)
        assert package["conditions"]["report"]["filenames"]["check"] == (
            "reports/2026-07-20-testville-check.md"
        )


class TestBuildPackageWindows:
    def test_only_go_and_check_days_ranked_by_score(self):
        payload = make_payload()
        # Make 2026-07-21 a clean check day (period at minimum) so it ranks.
        payload["surf_windows"][1]["swell_period_s"] = 12.0
        windows = build_package(payload, PROFILE)["analysis"]["windows"]
        assert [w["date"] for w in windows] == ["2026-07-20", "2026-07-21"]
        assert windows[0]["verdict"] == "go"
        assert windows[1]["verdict"] == "check"

    def test_entry_shape(self):
        entry = build_package(make_payload(), PROFILE)["analysis"]["windows"][0]
        assert entry["window"] == {"from": "08:00", "to": "11:00", "label": "Dawn patrol"}
        assert entry["swell"] == "1.2 m NW @ 13 s"
        assert entry["wind"] == "12 km/h S (offshore)"
        assert "good" in entry["why"]

    def test_all_skip_week_is_empty(self):
        payload = make_payload(
            windows=[make_window(rating="poor", score=2.0)], dates=["2026-07-20"]
        )
        assert build_package(payload, PROFILE)["analysis"]["windows"] == []


class TestSpotData:
    def test_profile_grid_mapping(self):
        profile = build_package(make_payload(), PROFILE)["spot_data"]["profile"]
        assert profile["ideal_swell_direction"] == "NW"
        assert profile["ideal_swell_size"] == "1.0-1.5 m minimum to break"
        assert profile["ideal_period_s"] == 12
        assert profile["ideal_wind"] == "S-SW offshore"
        assert profile["ideal_tide"] == "low to mid"
        assert profile["best_season"] == "October-March"
        assert profile["break_type"] == "beach"
        assert profile["bottom"] == "sand"
        assert profile["wave_direction"] == "left"
        assert profile["ability_level"] == "intermediate"

    def test_notes_carried_verbatim_as_description(self):
        profile = build_package(make_payload(), PROFILE)["spot_data"]["profile"]
        assert profile["description"] == "Tide-windowed; best on the push."

    def test_absent_fields_are_omitted(self):
        spot = {"name": "Bare", "works_on": {"swell_direction": "NW", "min_period_s": None}}
        profile = build_package(make_payload(), spot)["spot_data"]["profile"]
        assert profile == {"ideal_swell_direction": "NW"}

    def test_lists_verbatim_and_community_notes_empty(self):
        spot_data = build_package(make_payload(), PROFILE)["spot_data"]
        assert spot_data["peaks"] == PROFILE["peaks"]
        assert spot_data["hazards"] == PROFILE["hazards"]
        assert spot_data["webcams"] == PROFILE["webcams"]
        assert spot_data["community_notes"] == []

    def test_prose_derived_cards_are_absent(self):
        spot_data = build_package(make_payload(), PROFILE)["spot_data"]
        for key in ("access", "lifeguards", "rentals", "food", "nearby_spots", "water_quality"):
            assert key not in spot_data


class TestDraftRendersAsIs:
    """The draft package must satisfy render_report.py without any agent edit."""

    def test_dashboard_renders_with_draft_tag(self):
        import render_report

        package = build_package(make_payload(), PROFILE)
        html = render_report.render_dashboard(package)
        assert "Computed call, no analyst pass." in html
        md = render_report.render_dashboard_markdown(package)
        assert "Computed call, no analyst pass." in md

    def test_output_path_derives_from_draft(self):
        from render_report import dashboard_output_path

        package = build_package(make_payload(), PROFILE)
        assert dashboard_output_path(package) == "reports/2026-07-20-testville-dashboard.html"


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def write_inputs(tmp_path, payload=None, profile=PROFILE):
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload or make_payload()), encoding="utf-8")
    spot_path = tmp_path / "spot.yaml"
    spot_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return payload_path, spot_path


class TestCli:
    def test_happy_path_writes_output(self, tmp_path):
        payload_path, spot_path = write_inputs(tmp_path)
        out = tmp_path / "package.json"
        result = CliRunner().invoke(
            cli,
            ["--payload", str(payload_path), "--spot-file", str(spot_path), "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
        echo = json.loads(result.output)
        assert echo["package_path"] == str(out)
        assert echo["verdict"] == "go"
        package = json.loads(out.read_text(encoding="utf-8"))
        assert package["analysis"]["target_day"]["verdict"] == "go"
        assert package["spot_data"]["profile"]["ideal_swell_direction"] == "NW"

    def test_no_output_prints_package(self, tmp_path):
        payload_path, spot_path = write_inputs(tmp_path)
        result = CliRunner().invoke(
            cli, ["--payload", str(payload_path), "--spot-file", str(spot_path)]
        )
        assert result.exit_code == 0, result.output
        package = json.loads(result.output)
        assert package["analysis"]["target_day"]["verdict"] == "go"

    def test_no_spot_file_builds_uncorrected_draft(self, tmp_path):
        payload_path, _ = write_inputs(tmp_path)
        result = CliRunner().invoke(cli, ["--payload", str(payload_path)])
        assert result.exit_code == 0, result.output
        package = json.loads(result.output)
        assert "spot_data" not in package
        # 2026-07-21 keeps its rating-only check verdict without works-on demotions.
        assert package["analysis"]["week"][1]["verdict"] == "check"

    def test_missing_payload_file_is_soft_error(self, tmp_path):
        result = CliRunner().invoke(cli, ["--payload", str(tmp_path / "nope.json")])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" in data
        assert "note" in data

    def test_malformed_payload_json_is_soft_error(self, tmp_path):
        bad = tmp_path / "payload.json"
        bad.write_text("{not json", encoding="utf-8")
        result = CliRunner().invoke(cli, ["--payload", str(bad)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" in data
        assert "note" in data

    def test_malformed_spot_yaml_is_soft_error(self, tmp_path):
        payload_path, _ = write_inputs(tmp_path)
        bad = tmp_path / "spot.yaml"
        bad.write_text("works_on: [unclosed", encoding="utf-8")
        result = CliRunner().invoke(
            cli, ["--payload", str(payload_path), "--spot-file", str(bad)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" in data
        assert "note" in data

    def test_invalid_target_day_exits_1(self, tmp_path):
        payload_path, _ = write_inputs(tmp_path)
        result = CliRunner().invoke(
            cli, ["--payload", str(payload_path), "--target-day", "next tuesday"]
        )
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_unwritable_output_is_soft_error(self, tmp_path):
        payload_path, spot_path = write_inputs(tmp_path)
        result = CliRunner().invoke(
            cli,
            [
                "--payload", str(payload_path),
                "--spot-file", str(spot_path),
                "--output", str(tmp_path / "missing-dir" / "package.json"),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "error" in data
        assert "note" in data

    def test_error_package_skips_output_file(self, tmp_path):
        payload_path, spot_path = write_inputs(tmp_path, payload=make_payload(windows=None))
        out = tmp_path / "package.json"
        result = CliRunner().invoke(
            cli,
            ["--payload", str(payload_path), "--spot-file", str(spot_path), "--output", str(out)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "error" in data
        assert not out.exists()
