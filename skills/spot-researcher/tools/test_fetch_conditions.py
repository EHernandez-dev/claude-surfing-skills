"""Tests for fetch_conditions.py.

Unit tests cover the pure helpers (no network). Integration tests hit live
APIs and only run with RUN_INTEGRATION_TESTS=1.
"""

import json
import os

import pytest
from click.testing import CliRunner

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
    wind FROM the east blows off the land = offshore."""

    def test_onshore(self):
        assert classify_wind(270, 270, 15) == "onshore"
        assert classify_wind(300, 270, 15) == "onshore"

    def test_offshore(self):
        assert classify_wind(90, 270, 15) == "offshore"
        assert classify_wind(120, 270, 15) == "offshore"

    def test_cross_shore(self):
        assert classify_wind(180, 270, 15) == "cross-shore"
        assert classify_wind(0, 270, 15) == "cross-shore"

    def test_light_wind_any_direction(self):
        assert classify_wind(270, 270, 3) == "light"

    def test_wraparound(self):
        # North-facing spot, wind from 350 deg -> onshore
        assert classify_wind(350, 0, 15) == "onshore"

    def test_none_direction(self):
        assert classify_wind(None, 270, 10) is None


class TestRateBlock:
    def test_flat(self):
        assert rate_block(0.5, 15, 5, "light") == {"score": 0, "rating": "flat"}

    def test_none_height(self):
        assert rate_block(None, 10, 5, "light") is None

    def test_epic_conditions(self):
        # 5 ft at 14 s with light offshore -> top rating
        result = rate_block(5, 14, 8, "offshore")
        assert result["rating"] == "epic"

    def test_junky_onshore(self):
        # 3 ft short-period windswell with strong onshore -> poor
        result = rate_block(3, 6, 20, "onshore")
        assert result["rating"] == "poor"

    def test_strong_offshore_penalized(self):
        gentle = rate_block(4, 12, 10, "offshore")
        nuking = rate_block(4, 12, 30, "offshore")
        assert nuking["score"] < gentle["score"]

    def test_score_bounds(self):
        for args in [(5, 14, 5, "light"), (2, 8, 25, "onshore"), (15, 20, 0, "light")]:
            result = rate_block(*args)
            assert 0 <= result["score"] <= 10

    def test_short_period_windswell_capped_at_poor(self):
        # 3 ft of 6 s windswell with glassy wind: size + wind alone must not
        # inflate junk swell past "poor"
        result = rate_block(3, 6, 4, "light")
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
            "swell_height_ft": 3.0,
            "swell_period_s": 12.0,
            "swell_direction": "W",
            "wind_kn": 5,
            "wind_direction": "E",
            "wind_type": "light",
            "quality": {"score": score, "rating": "good"},
        }

    def test_best_time_clamped_to_first_light(self):
        # The 05:00 block wins but first light is 05:24: the window must not
        # be reported in the dark
        days = [{"date": "2026-07-08", "blocks": [self._block("05:00", 8.0), self._block("14:00", 5.0)]}]
        windows = build_surf_windows(days, self.DAYLIGHT)
        assert windows[0]["best_time"] == "05:24"

    def test_daytime_block_not_clamped(self):
        days = [{"date": "2026-07-08", "blocks": [self._block("08:00", 8.0)]}]
        windows = build_surf_windows(days, self.DAYLIGHT)
        assert windows[0]["best_time"] == "08:00"

    def test_after_dark_block_excluded(self):
        # Only block starts after last light: no window that day
        late = {"days": [{**self.DAYLIGHT["days"][0], "last_light": "19:00"}]}
        days = [{"date": "2026-07-08", "blocks": [self._block("20:00", 9.0)]}]
        assert build_surf_windows(days, late) == []


class TestBuildSeaTemperature:
    MARINE = {
        "hourly": {
            "time": ["2026-07-08T05:00"],
            "sea_surface_temperature": [15.8],  # 60.4 F model SST
        }
    }

    def test_prefers_buoy_observation(self):
        result = build_sea_temperature(self.MARINE, {"water_temp_f": 57.6})
        assert result["current_f"] == 57.6
        assert result["source"] == "buoy observation"
        assert result["model_f"] == 60.4
        # 57.6 F and 60.4 F straddle the 4/3-vs-3/2 boundary; buoy wins
        assert "4/3" in result["wetsuit"]

    def test_falls_back_to_model(self):
        result = build_sea_temperature(self.MARINE, {"error": "no buoy"})
        assert result["current_f"] == 60.4
        assert result["source"] == "model SST"
        assert result["buoy_f"] is None

    def test_no_data_at_all(self):
        result = build_sea_temperature({"error": "down"}, {"error": "no buoy"})
        assert "error" in result


class TestWetsuit:
    def test_tropical(self):
        assert "Boardshorts" in wetsuit_for(80)

    def test_california(self):
        assert wetsuit_for(60) == "3/2 fullsuit"

    def test_cold(self):
        assert "5/4" in wetsuit_for(48)

    def test_extreme(self):
        assert "6/5" in wetsuit_for(40)

    def test_none(self):
        assert wetsuit_for(None) is None


class TestParseNdbcRealtime:
    SAMPLE = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi hPa    ft
2026 07 08 14 40 280  6.0  7.0   1.5  12.0   8.2 285 1015.2  15.0  13.5  12.0 99.0 +0.2    MM
2026 07 08 14 10 275  5.5  6.5   1.4  12.5   8.0 280 1015.0  14.8  13.4  11.9 99.0 +0.1    MM
"""

    def test_parses_latest_row(self):
        obs = parse_ndbc_realtime(self.SAMPLE)
        assert obs["wave_height_ft"] == 4.9  # 1.5 m
        assert obs["dominant_period_s"] == 12.0
        assert obs["mean_wave_direction"] == "WNW"  # 285 deg
        assert obs["water_temp_f"] == 56.3
        assert "2026-07-08" in obs["observed_at"]

    def test_skips_missing_wvht(self):
        sample = self.SAMPLE.replace("  1.5  12.0", "   MM  12.0")
        obs = parse_ndbc_realtime(sample)
        # falls through to the second row
        assert obs["wave_height_ft"] == 4.6  # 1.4 m

    def test_empty_input(self):
        assert parse_ndbc_realtime("") is None

    def test_all_missing(self):
        header = self.SAMPLE.splitlines()[:2]
        row = "2026 07 08 14 40 280  6.0  7.0    MM    MM    MM  MM 1015.2  15.0    MM  12.0 99.0 +0.2    MM"
        assert parse_ndbc_realtime("\n".join([*header, row])) is None


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
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["spot"]["name"] == "Ocean Beach"
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
        assert "error" in data["tides"]
        assert any("tides" in g for g in data["gaps"])
