"""Tests for verify_forecast.py.

Unit tests cover the pure bias arithmetic (log parsing, freshest-snapshot
selection, mean signed error, deadband, unit conversion). CLI tests exercise
the full seam: a forecast-log JSONL file plus observations JSON in, a bias
result out, honoring the exit-0 data-degradation / exit-1 argument contract.
"""

import json

from click.testing import CliRunner

from verify_forecast import (
    bias_note,
    cli,
    compute_bias,
    freshest_by_date,
    height_to_m,
    load_forecast_log,
    match_sessions,
)

METRIC = {"system": "metric", "wave_height": "m"}
IMPERIAL = {"system": "imperial", "wave_height": "ft"}


def _line(date, swell_height, period, archived_on, lead_days, units=METRIC, **extra):
    return {
        "archived_on": archived_on,
        "date": date,
        "lead_days": lead_days,
        "units": units,
        "swell_height": swell_height,
        "swell_period_s": period,
        "swell_direction": "NW",
        "spot_slug": "mundaka",
        **extra,
    }


# ---------------------------------------------------------------------------
# Pure arithmetic
# ---------------------------------------------------------------------------


class TestLoadForecastLog:
    def test_parses_and_skips_bad_lines(self):
        text = "\n".join(
            [
                json.dumps(_line("2026-07-10", 1.0, 12.0, "2026-07-09", 1)),
                "",
                "{not json",
                json.dumps({"no": "date"}),
                json.dumps(_line("2026-07-11", 1.2, 13.0, "2026-07-09", 2)),
            ]
        )
        records = load_forecast_log(text)
        assert [r["date"] for r in records] == ["2026-07-10", "2026-07-11"]

    def test_empty_text(self):
        assert load_forecast_log("") == []


class TestFreshestByDate:
    def test_prefers_latest_archived_on(self):
        records = [
            _line("2026-07-10", 1.0, 12.0, "2026-07-08", 2),
            _line("2026-07-10", 1.1, 12.5, "2026-07-09", 1),  # fresher
        ]
        by_date = freshest_by_date(records)
        assert by_date["2026-07-10"]["archived_on"] == "2026-07-09"
        assert by_date["2026-07-10"]["swell_height"] == 1.1

    def test_ties_break_toward_shorter_lead(self):
        records = [
            _line("2026-07-10", 1.0, 12.0, "2026-07-09", 3),
            _line("2026-07-10", 1.4, 12.0, "2026-07-09", 1),  # same run, shorter lead
        ]
        assert freshest_by_date(records)["2026-07-10"]["swell_height"] == 1.4


class TestHeightConversion:
    def test_meters_pass_through(self):
        assert height_to_m(1.0, "m") == 1.0

    def test_feet_to_meters(self):
        assert round(height_to_m(3.28084, "ft"), 3) == 1.0


class TestBiasNote:
    def test_under_call(self):
        assert "under-calls" in bias_note(0.4)

    def test_over_call(self):
        assert "over-calls" in bias_note(-0.4)

    def test_deadband_tracks(self):
        assert "tracks" in bias_note(0.05)

    def test_no_samples(self):
        assert "no overlapping" in bias_note(None)


class TestComputeBias:
    def _matched(self, pairs):
        # pairs: list of (obs_h, fc_h, obs_p, fc_p)
        matched = []
        for obs_h, fc_h, obs_p, fc_p in pairs:
            diff = {}
            if obs_h is not None and fc_h is not None:
                diff["swell_height"] = obs_h - fc_h
            if obs_p is not None and fc_p is not None:
                diff["swell_period_s"] = obs_p - fc_p
            matched.append({"diff": diff})
        return matched

    def test_mean_signed_under_call(self):
        bias = compute_bias(self._matched([(1.5, 1.1, 13.0, 12.5), (1.2, 0.8, 11.0, 10.0)]), "m")
        assert bias["swell_height_m"] == 0.4
        assert bias["swell_period_s"] == 0.75
        assert "under-calls" in bias["note"]

    def test_deadband_zeroes_small_bias(self):
        bias = compute_bias(self._matched([(1.05, 1.0, None, None)]), "m")
        assert bias["swell_height_m"] == 0.0
        assert "tracks" in bias["note"]

    def test_imperial_height_converted_to_meters(self):
        # forecast 3.0 ft, observed 4.0 ft -> +1.0 ft under-call -> ~0.30 m
        bias = compute_bias(self._matched([(4.0, 3.0, None, None)]), "ft")
        assert bias["swell_height_m"] == 0.3

    def test_no_height_samples(self):
        bias = compute_bias(self._matched([(None, None, 12.0, 11.0)]), "m")
        assert bias["swell_height_m"] is None
        assert bias["swell_period_s"] == 1.0


class TestMatchSessions:
    def test_matches_and_reports_unmatched(self):
        by_date = freshest_by_date([_line("2026-07-10", 1.0, 12.0, "2026-07-09", 1)])
        observations = [
            {"date": "2026-07-10", "swell_height": 1.5, "swell_period_s": 13.0},
            {"date": "2026-07-14", "swell_height": 2.0},  # no forecast archived
        ]
        matched, unmatched = match_sessions(observations, by_date)
        assert len(matched) == 1
        assert matched[0]["diff"]["swell_height"] == 0.5
        assert matched[0]["archived_on"] == "2026-07-09"
        assert unmatched == ["2026-07-14"]


# ---------------------------------------------------------------------------
# CLI seam
# ---------------------------------------------------------------------------


def run_verify(tmp_path, lines, observations=None, *extra, obs_arg=True):
    log = tmp_path / "mundaka.jsonl"
    log.write_text("\n".join(json.dumps(line) for line in lines) + ("\n" if lines else ""))
    args = ["--forecast-log", str(log)]
    if obs_arg and observations is not None:
        args += ["--observations", json.dumps(observations)]
    args += list(extra)
    return CliRunner().invoke(cli, args)


class TestCliBiasArithmetic:
    LINES = [
        _line("2026-07-10", 1.0, 12.0, "2026-07-08", 2),
        _line("2026-07-10", 1.1, 12.5, "2026-07-09", 1),  # freshest for 07-10
        _line("2026-07-12", 0.8, 10.0, "2026-07-11", 1),
    ]

    def test_under_call_bias_from_sessions(self, tmp_path):
        observations = [
            {"date": "2026-07-10", "swell_height": 1.5, "swell_period_s": 13.0},
            {"date": "2026-07-12", "swell_height": 1.2, "swell_period_s": 11.0},
        ]
        result = run_verify(tmp_path, self.LINES, observations)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["samples"] == 2
        assert data["bias"]["swell_height_m"] == 0.4  # mean(0.4, 0.4)
        assert data["bias"]["swell_period_s"] == 0.75  # mean(0.5, 1.0)
        assert "under-calls" in data["note"]
        assert data["spot_slug"] == "mundaka"
        # the freshest 07-10 snapshot (archived 07-09) is the one compared against
        matched = next(m for m in data["matched"] if m["date"] == "2026-07-10")
        assert matched["forecast"]["archived_on"] == "2026-07-09"

    def test_over_call_bias(self, tmp_path):
        observations = [{"date": "2026-07-12", "swell_height": 0.4}]  # forecast said 0.8
        data = json.loads(run_verify(tmp_path, self.LINES, observations).output)
        assert data["bias"]["swell_height_m"] == -0.4
        assert "over-calls" in data["note"]

    def test_unmatched_session_reported(self, tmp_path):
        observations = [{"date": "2026-07-20", "swell_height": 1.0}]
        data = json.loads(run_verify(tmp_path, self.LINES, observations).output)
        assert data["samples"] == 0
        assert data["unmatched_sessions"] == ["2026-07-20"]
        assert "overlap" in data["note"]

    def test_imperial_log_converts_bias_to_meters(self, tmp_path):
        lines = [_line("2026-07-10", 3.0, 12.0, "2026-07-09", 1, units=IMPERIAL)]
        observations = [{"date": "2026-07-10", "swell_height": 4.0}]  # +1.0 ft
        data = json.loads(run_verify(tmp_path, lines, observations).output)
        assert data["bias"]["swell_height_m"] == 0.3
        assert data["units"]["system"] == "imperial"

    def test_empty_log_reports_nothing_to_compare(self, tmp_path):
        data = json.loads(run_verify(tmp_path, [], [{"date": "2026-07-10", "swell_height": 1.0}]).output)
        assert data["samples"] == 0
        assert "empty" in data["note"]

    def test_spot_slug_override(self, tmp_path):
        data = json.loads(
            run_verify(tmp_path, self.LINES, [{"date": "2026-07-10", "swell_height": 1.1}], "--spot-slug", "custom").output
        )
        assert data["spot_slug"] == "custom"


class TestCliObservationsFile:
    def test_reads_observations_from_file(self, tmp_path):
        log = tmp_path / "mundaka.jsonl"
        log.write_text(json.dumps(_line("2026-07-10", 1.0, 12.0, "2026-07-09", 1)) + "\n")
        obs = tmp_path / "obs.json"
        obs.write_text(json.dumps([{"date": "2026-07-10", "swell_height": 1.5}]))
        result = CliRunner().invoke(
            cli, ["--forecast-log", str(log), "--observations-file", str(obs)]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["bias"]["swell_height_m"] == 0.5


class TestCliContract:
    def test_missing_forecast_log_exits_1(self):
        result = CliRunner().invoke(cli, ["--observations", "[]"])
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_missing_observations_exits_1(self, tmp_path):
        log = tmp_path / "mundaka.jsonl"
        log.write_text("")
        result = CliRunner().invoke(cli, ["--forecast-log", str(log)])
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_malformed_observations_exits_1(self, tmp_path):
        log = tmp_path / "mundaka.jsonl"
        log.write_text("")
        result = CliRunner().invoke(cli, ["--forecast-log", str(log), "--observations", "{not a list}"])
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_unreadable_log_degrades_exit_0(self):
        result = CliRunner().invoke(
            cli, ["--forecast-log", "/nonexistent/forecasts/nowhere.jsonl", "--observations", "[]"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "error" in payload
        assert payload["note"], "degradation contract requires a manual-fallback note"
