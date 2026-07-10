"""Tests for fetch_conditions.py.

Unit tests cover the pure helpers (no network) and the CLI contract with
network fetchers monkeypatched. Integration tests hit live APIs and only run
with RUN_INTEGRATION_TESTS=1.
"""

import json
import os

import pytest
from click.testing import CliRunner

import fetch_conditions
from fetch_conditions import (
    build_sea_temperature,
    build_surf_windows,
    c_to_f,
    classify_wind,
    cli,
    compass,
    haversine_km,
    m_to_ft,
    parse_coordinates,
    parse_ndbc_realtime,
    rate_block,
    report_filename,
    slugify,
    wetsuit_for,
)


class TestParseCoordinates:
    def test_valid(self):
        assert parse_coordinates("37.76,-122.51") == (37.76, -122.51)

    def test_with_spaces(self):
        assert parse_coordinates(" 43.42 , -1.67 ") == (43.42, -1.67)

    def test_missing_comma(self):
        with pytest.raises(ValueError):
            parse_coordinates("37.76")

    def test_out_of_range(self):
        with pytest.raises(ValueError):
            parse_coordinates("91.0,0.0")


class TestCompass:
    def test_cardinals(self):
        assert compass(0) == "N"
        assert compass(90) == "E"
        assert compass(180) == "S"
        assert compass(270) == "W"

    def test_intercardinal(self):
        assert compass(225) == "SW"
        assert compass(292.5) == "WNW"

    def test_wraps(self):
        assert compass(359) == "N"
        assert compass(361) == "N"

    def test_none(self):
        assert compass(None) is None


class TestConversions:
    def test_m_to_ft(self):
        assert m_to_ft(1.0) == 3.3
        assert m_to_ft(None) is None

    def test_c_to_f(self):
        assert c_to_f(0) == 32.0
        assert c_to_f(20) == 68.0
        assert c_to_f(None) is None


class TestHaversine:
    def test_zero_distance(self):
        assert haversine_km(37.76, -122.51, 37.76, -122.51) == 0

    def test_known_distance(self):
        # SF to LA is roughly 560 km
        d = haversine_km(37.77, -122.42, 34.05, -118.24)
        assert 540 < d < 580


class TestClassifyWind:
    """West-facing spot (facing=270): wind FROM the west is onshore,
    wind FROM the east blows off the land = offshore. Speeds in m/s."""

    def test_onshore(self):
        assert classify_wind(270, 270, 8) == "onshore"
        assert classify_wind(300, 270, 8) == "onshore"

    def test_offshore(self):
        assert classify_wind(90, 270, 8) == "offshore"
        assert classify_wind(120, 270, 8) == "offshore"

    def test_cross_shore(self):
        assert classify_wind(180, 270, 8) == "cross-shore"
        assert classify_wind(0, 270, 8) == "cross-shore"

    def test_light_wind_any_direction(self):
        assert classify_wind(270, 270, 1.5) == "light"

    def test_wraparound(self):
        # North-facing spot, wind from 350 deg -> onshore
        assert classify_wind(350, 0, 8) == "onshore"

    def test_none_direction(self):
        assert classify_wind(None, 270, 5) is None


class TestRateBlock:
    """Inputs are SI: swell height m, period s, wind m/s."""

    def test_flat(self):
        assert rate_block(0.15, 15, 2, "light") == {"score": 0, "rating": "flat"}

    def test_none_height(self):
        assert rate_block(None, 10, 2, "light") is None

    def test_epic_conditions(self):
        # 1.5 m at 14 s with gentle offshore -> top rating
        result = rate_block(1.5, 14, 4, "offshore")
        assert result["rating"] == "epic"

    def test_junky_onshore(self):
        # 0.9 m short-period windswell with strong onshore -> poor
        result = rate_block(0.9, 6, 10, "onshore")
        assert result["rating"] == "poor"

    def test_strong_offshore_penalized(self):
        gentle = rate_block(1.2, 12, 5, "offshore")
        nuking = rate_block(1.2, 12, 15, "offshore")
        assert nuking["score"] < gentle["score"]

    def test_score_bounds(self):
        for args in [(1.5, 14, 2, "light"), (0.6, 8, 13, "onshore"), (4.5, 20, 0, "light")]:
            result = rate_block(*args)
            assert 0 <= result["score"] <= 10

    def test_short_period_windswell_capped_at_poor(self):
        # 0.9 m of 6 s windswell with glassy wind: size + wind alone must not
        # inflate junk swell past "poor"
        result = rate_block(0.9, 6, 2, "light")
        assert result["score"] <= 3.5
        assert result["rating"] == "poor"


class TestBuildSurfWindows:
    DAYLIGHT = {
        "days": [
            {
                "date": "2026-07-08",
                "first_light": "05:24",
                "sunrise": "05:56",
                "sunset": "20:34",
                "last_light": "21:05",
                "daylight_hours": 14.6,
            }
        ]
    }

    @staticmethod
    def _block(time, score):
        return {
            "time": time,
            "swell_height": 0.9,
            "swell_period_s": 12.0,
            "swell_direction": "W",
            "wind_speed": 9,
            "wind_direction": "E",
            "wind_type": "light",
            "quality": {"score": score, "rating": "good"},
        }

    def test_best_time_clamped_to_first_light(self):
        # The 05:00 block wins but first light is 05:24: the window must not
        # be reported in the dark
        days = [{"date": "2026-07-08", "blocks": [self._block("05:00", 8.0), self._block("14:00", 5.0)]}]
        windows = build_surf_windows(days, self.DAYLIGHT, "metric")
        assert windows[0]["best_time"] == "05:24"

    def test_daytime_block_not_clamped(self):
        days = [{"date": "2026-07-08", "blocks": [self._block("08:00", 8.0)]}]
        windows = build_surf_windows(days, self.DAYLIGHT, "metric")
        assert windows[0]["best_time"] == "08:00"

    def test_after_dark_block_excluded(self):
        # Only block starts after last light: no window that day
        late = {"days": [{**self.DAYLIGHT["days"][0], "last_light": "19:00"}]}
        days = [{"date": "2026-07-08", "blocks": [self._block("20:00", 9.0)]}]
        assert build_surf_windows(days, late, "metric") == []

    def test_wind_string_carries_units(self):
        days = [{"date": "2026-07-08", "blocks": [self._block("08:00", 8.0)]}]
        assert build_surf_windows(days, self.DAYLIGHT, "metric")[0]["wind"] == "9 km/h E (light)"
        assert build_surf_windows(days, self.DAYLIGHT, "imperial")[0]["wind"] == "9 kn E (light)"


class TestBuildSeaTemperature:
    MARINE = {
        "hourly": {
            "time": ["2026-07-08T05:00"],
            "sea_surface_temperature": [15.8],  # model SST, deg C
        }
    }

    def test_prefers_buoy_observation(self):
        result = build_sea_temperature(self.MARINE, {"water_temp_c": 14.2}, "metric")
        assert result["current"] == 14.2
        assert result["source"] == "buoy observation"
        assert result["model"] == 15.8
        # 14.2 C and 15.8 C straddle the 4/3-vs-3/2 boundary; buoy wins
        assert "4/3" in result["wetsuit"]

    def test_imperial_output(self):
        result = build_sea_temperature(self.MARINE, {"water_temp_c": 14.2}, "imperial")
        assert result["current"] == 57.6
        assert result["model"] == 60.4
        assert "4/3" in result["wetsuit"]

    def test_falls_back_to_model(self):
        result = build_sea_temperature(self.MARINE, {"error": "no buoy"}, "metric")
        assert result["current"] == 15.8
        assert result["source"] == "model SST"
        assert result["buoy"] is None

    def test_no_data_at_all(self):
        result = build_sea_temperature({"error": "down"}, {"error": "no buoy"}, "metric")
        assert "error" in result


class TestWetsuit:
    """Input is deg C (SI internally)."""

    def test_tropical(self):
        assert "Boardshorts" in wetsuit_for(26.5)

    def test_california(self):
        assert wetsuit_for(15.5) == "3/2 fullsuit"

    def test_cold(self):
        assert "5/4" in wetsuit_for(9)

    def test_extreme(self):
        assert "6/5" in wetsuit_for(4)

    def test_none(self):
        assert wetsuit_for(None) is None


class TestParseNdbcRealtime:
    SAMPLE = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi hPa    ft
2026 07 08 14 40 280  6.0  7.0   1.5  12.0   8.2 285 1015.2  15.0  13.5  12.0 99.0 +0.2    MM
2026 07 08 14 10 275  5.5  6.5   1.4  12.5   8.0 280 1015.0  14.8  13.4  11.9 99.0 +0.1    MM
"""

    def test_parses_latest_row_in_si(self):
        obs = parse_ndbc_realtime(self.SAMPLE)
        assert obs["wave_height_m"] == 1.5
        assert obs["dominant_period_s"] == 12.0
        assert obs["mean_wave_direction"] == "WNW"  # 285 deg
        assert obs["wind_ms"] == 6.0
        assert obs["water_temp_c"] == 13.5
        assert "2026-07-08" in obs["observed_at"]

    def test_skips_missing_wvht(self):
        sample = self.SAMPLE.replace("  1.5  12.0", "   MM  12.0")
        obs = parse_ndbc_realtime(sample)
        # falls through to the second row
        assert obs["wave_height_m"] == 1.4

    def test_empty_input(self):
        assert parse_ndbc_realtime("") is None

    def test_all_missing(self):
        header = self.SAMPLE.splitlines()[:2]
        row = "2026 07 08 14 40 280  6.0  7.0    MM    MM    MM  MM 1015.2  15.0    MM  12.0 99.0 +0.2    MM"
        assert parse_ndbc_realtime("\n".join([*header, row])) is None


class TestSlugify:
    def test_basic(self):
        assert slugify("Mundaka") == "mundaka"

    def test_spaces_and_punctuation(self):
        assert slugify("La Salvaje / Sopelana") == "la-salvaje-sopelana"

    def test_accents_stripped(self):
        assert slugify("La Gravière") == "la-graviere"

    def test_no_leading_trailing_hyphens(self):
        assert slugify("  Ocean Beach (SF)  ") == "ocean-beach-sf"


class TestReportFilename:
    def test_composes_pattern(self):
        assert report_filename("2026-07-11", "Mundaka", "go") == "2026-07-11-mundaka-go.md"

    def test_slugifies_spot_name(self):
        assert report_filename("2026-07-11", "La Salvaje / Sopelana", "check") == (
            "2026-07-11-la-salvaje-sopelana-check.md"
        )

    def test_rejects_unknown_verdict(self):
        with pytest.raises(ValueError):
            report_filename("2026-07-11", "Mundaka", "worth a check")


# ---------------------------------------------------------------------------
# CLI seam: full JSON contract with network fetchers monkeypatched
# ---------------------------------------------------------------------------

MARINE_RAW = {
    "timezone": "Europe/Madrid",
    "hourly": {
        "time": ["2026-07-10T05:00", "2026-07-10T08:00"],
        "wave_height": [1.0, 1.2],
        "swell_wave_height": [0.9, 1.1],
        "swell_wave_direction": [300.0, 305.0],
        "swell_wave_period": [12.0, 13.0],
        "wind_wave_height": [0.2, 0.3],
        "sea_surface_temperature": [15.8, 15.9],
    },
    "daily": {
        "time": ["2026-07-10"],
        "wave_height_max": [1.2],
        "swell_wave_height_max": [1.1],
        "swell_wave_period_max": [13.0],
        "swell_wave_direction_dominant": [302.0],
    },
}

WIND_RAW = {
    "timezone": "Europe/Madrid",
    "hourly": {
        "time": ["2026-07-10T05:00", "2026-07-10T08:00"],
        "wind_speed_10m": [5.0, 3.0],  # m/s
        "wind_direction_10m": [90.0, 100.0],
        "wind_gusts_10m": [8.0, 6.0],
    },
    "daily": {
        "time": ["2026-07-10"],
        "weather_code": [1],
        "temperature_2m_max": [22.5],  # deg C
        "temperature_2m_min": [15.0],
        "precipitation_probability_max": [10],
        "uv_index_max": [7.0],
    },
}

BUOY_SI = {
    "station": {"id": "46026", "name": "Test Buoy", "distance_km": 30.0, "url": "https://example.test"},
    "observed_at": "2026-07-10 05:40 UTC",
    "wave_height_m": 1.5,
    "dominant_period_s": 12.0,
    "mean_wave_direction": "WNW",
    "wind_ms": 6.0,
    "wind_direction": "W",
    "water_temp_c": 13.5,
}

TIDES_SI = {
    "station": {"id": "9414290", "name": "Test Station", "state": "", "distance_km": 5.0, "url": "https://example.test"},
    "datum": "MLLW",
    "days": [
        {
            "date": "2026-07-10",
            "events": [{"time": "04:12", "height_m": 1.234, "type": "high"}],
        }
    ],
}

DAYLIGHT = {
    "timezone": "Europe/Madrid",
    "days": [
        {
            "date": "2026-07-10",
            "first_light": "06:12",
            "sunrise": "06:47",
            "sunset": "21:48",
            "last_light": "22:23",
            "daylight_hours": 15.0,
        }
    ],
}


@pytest.fixture
def patched_fetchers(monkeypatch):
    monkeypatch.setattr(fetch_conditions, "fetch_marine", lambda lat, lon, days: MARINE_RAW)
    monkeypatch.setattr(fetch_conditions, "fetch_wind_weather", lambda lat, lon, days: WIND_RAW)
    monkeypatch.setattr(fetch_conditions, "fetch_buoy", lambda lat, lon: BUOY_SI)
    monkeypatch.setattr(fetch_conditions, "fetch_tides", lambda lat, lon, days: TIDES_SI)
    monkeypatch.setattr(fetch_conditions, "fetch_daylight", lambda lat, lon, tz, days: DAYLIGHT)


def run_cli(*extra_args):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--coordinates", "43.407,-2.699", "--spot-name", "Mundaka", "--facing", "315", *extra_args],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


class TestCliUnitsContract:
    def test_metric_is_default_and_echoed(self, patched_fetchers):
        data = run_cli()
        assert data["units"] == {
            "system": "metric",
            "wave_height": "m",
            "tide_height": "m",
            "wind_speed": "km/h",
            "temperature": "°C",
        }

    def test_metric_values(self, patched_fetchers):
        data = run_cli("--units", "metric")
        block = data["marine"]["days"][0]["blocks"][0]
        assert block["wave_height"] == 1.0
        assert block["swell_height"] == 0.9
        assert block["wind_speed"] == 18  # 5 m/s -> km/h
        assert block["wind_gust"] == 29  # 8 m/s -> km/h
        assert data["marine"]["days"][0]["summary"]["wave_height_max"] == 1.2
        assert data["buoy"]["wave_height"] == 1.5
        assert data["buoy"]["wind_speed"] == 22  # 6 m/s -> km/h
        assert data["buoy"]["water_temp"] == 13.5
        assert data["tides"]["days"][0]["events"][0]["height"] == 1.23
        assert data["sea_temperature"]["current"] == 13.5
        assert data["weather"]["days"][0]["temp_max"] == 22.5

    def test_imperial_values(self, patched_fetchers):
        data = run_cli("--units", "imperial")
        assert data["units"] == {
            "system": "imperial",
            "wave_height": "ft",
            "tide_height": "ft",
            "wind_speed": "kn",
            "temperature": "°F",
        }
        block = data["marine"]["days"][0]["blocks"][0]
        assert block["wave_height"] == 3.3  # 1.0 m
        assert block["wind_speed"] == 10  # 5 m/s -> kn
        assert data["buoy"]["wave_height"] == 4.9  # 1.5 m
        assert data["buoy"]["water_temp"] == 56.3  # 13.5 C
        assert data["tides"]["days"][0]["events"][0]["height"] == 4.0  # 1.234 m
        assert data["sea_temperature"]["current"] == 56.3
        assert data["weather"]["days"][0]["temp_max"] == 72.5  # 22.5 C

    def test_no_unit_suffixed_keys_remain(self, patched_fetchers):
        data = run_cli()
        payload = json.dumps(data)
        for legacy_key in ("wave_height_ft", "swell_height_ft", "wind_kn", "wind_gust_kn",
                           "height_ft", "water_temp_f", "current_f", "temp_max_f"):
            assert f'"{legacy_key}"' not in payload

    def test_wetsuit_same_in_both_modes(self, patched_fetchers):
        metric = run_cli("--units", "metric")
        imperial = run_cli("--units", "imperial")
        assert metric["sea_temperature"]["wetsuit"] == imperial["sea_temperature"]["wetsuit"]
        assert "4/3" in metric["sea_temperature"]["wetsuit"]  # 13.5 C

    def test_surf_windows_present_with_facing(self, patched_fetchers):
        data = run_cli()
        assert data["surf_windows"], "expected surf windows with --facing"
        assert "swell_height" in data["surf_windows"][0]


class TestCliReportNaming:
    def test_target_date_falls_back_to_window_start(self, patched_fetchers):
        data = run_cli()
        report = data["report"]
        assert report["directory"] == "reports"
        assert report["target_date"] == "2026-07-10"  # first forecast day, not run date
        assert report["spot_slug"] == "mundaka"
        assert report["filenames"] == {
            "go": "reports/2026-07-10-mundaka-go.md",
            "check": "reports/2026-07-10-mundaka-check.md",
            "skip": "reports/2026-07-10-mundaka-skip.md",
        }

    def test_explicit_target_day_wins(self, patched_fetchers):
        data = run_cli("--target-day", "2026-07-12")
        assert data["report"]["target_date"] == "2026-07-12"
        assert data["report"]["filenames"]["go"] == "reports/2026-07-12-mundaka-go.md"

    def test_no_window_leaves_target_date_null_never_run_date(self, monkeypatch):
        # Every fetch fails and no --target-day given: the run date must NOT
        # stand in for the target date; the gap is reported instead.
        error = {"error": "down", "note": "check manually"}
        monkeypatch.setattr(fetch_conditions, "fetch_marine", lambda lat, lon, days: error)
        monkeypatch.setattr(fetch_conditions, "fetch_wind_weather", lambda lat, lon, days: error)
        monkeypatch.setattr(fetch_conditions, "fetch_buoy", lambda lat, lon: error)
        monkeypatch.setattr(fetch_conditions, "fetch_tides", lambda lat, lon, days: error)
        monkeypatch.setattr(fetch_conditions, "fetch_daylight", lambda lat, lon, tz, days: error)
        data = run_cli()
        assert data["report"]["target_date"] is None
        assert data["report"]["filenames"] is None
        assert any("target date unknown" in g for g in data["gaps"])

    def test_invalid_target_day_exits_1(self, patched_fetchers):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--coordinates", "43.407,-2.699", "--spot-name", "Mundaka", "--target-day", "next tuesday"],
        )
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)


class TestCliValidation:
    def test_invalid_coordinates_exit_1(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--coordinates", "bogus", "--spot-name", "X"])
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)


@pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 to hit live APIs",
)
class TestIntegration:
    def test_ocean_beach_full_run(self):
        """Ocean Beach, San Francisco - US spot, everything should populate."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--coordinates", "37.759,-122.513",
                "--spot-name", "Ocean Beach",
                "--facing", "265",
                "--days", "3",
                "--units", "imperial",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["spot"]["name"] == "Ocean Beach"
        assert data["units"]["system"] == "imperial"
        assert data["marine"]["days"], "expected marine forecast days"
        assert data["surf_windows"], "expected surf windows with --facing"
        assert "days" in data["tides"], "expected NOAA tides for a US spot"
        assert data["sea_temperature"].get("wetsuit")

    def test_non_us_spot_reports_tide_gap(self):
        """Mundaka, Spain - marine data should work, NOAA tides should gap out."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--coordinates", "43.407,-2.699", "--spot-name", "Mundaka", "--days", "2"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["marine"]["days"]
        assert data["units"]["system"] == "metric"
        assert "error" in data["tides"]
        assert any("tides" in g for g in data["gaps"])
