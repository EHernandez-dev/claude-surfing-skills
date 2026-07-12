"""Tests for fetch_conditions.py.

Unit tests cover the pure helpers (no network) and the CLI contract with
network fetchers monkeypatched. Integration tests hit live APIs and only run
with RUN_INTEGRATION_TESTS=1.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

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
    nearest_portus_station,
    parse_coordinates,
    parse_ndbc_realtime,
    parse_portus_lastdata,
    parse_worldtides_extremes,
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


class TestParsePortusLastdata:
    """Fixture captured from a live probe of station 2136 (Bilbao-Vizcaya), 2026-07-10."""

    LASTDATA = {
        "fecha": "2026-07-10 16:00:00.0",
        "datos": [
            {"nombreParametro": "Periodo de Pico", "nombreColumna": "tp", "valor": "820", "factor": 100.0, "unidad": "s"},
            {"nombreParametro": "Periodo Medio Tm02", "nombreColumna": "tm02", "valor": "438", "factor": 100.0, "unidad": "s"},
            {"nombreParametro": "Altura Máxima del Oleaje", "nombreColumna": "hmax", "valor": "100", "factor": 100.0, "unidad": "m"},
            {"nombreParametro": "Altura Signif. del Oleaje", "nombreColumna": "hm0", "valor": "70", "factor": 100.0, "unidad": "m"},
            {"nombreParametro": "Direcc. Media de Proced.", "nombreColumna": "dmd", "valor": "323", "factor": 1.0, "unidad": "º"},
            {"nombreParametro": "Velocidad del viento", "nombreColumna": "vv_md", "valor": "164", "factor": 100.0, "unidad": "m/s"},
            {"nombreParametro": "Direc. de proced. del Viento", "nombreColumna": "dv_md", "valor": "335", "factor": 1.0, "unidad": "º"},
            {"nombreParametro": "Temperatura del Agua", "nombreColumna": "ts2", "valor": "2469", "factor": 100.0, "unidad": "ºC"},
            {"nombreParametro": "Latitud", "nombreColumna": "lat", "valor": "43.629395", "factor": 1.0, "unidad": "º"},
        ],
    }

    def test_parses_si_observation_with_factor_scaling(self):
        obs = parse_portus_lastdata(self.LASTDATA)
        assert obs["wave_height_m"] == 0.7  # hm0 70 / factor 100
        assert obs["dominant_period_s"] == 8.2  # tp 820 / factor 100
        assert obs["mean_wave_direction"] == "NW"  # dmd 323 deg
        assert obs["wind_ms"] == 1.64
        assert obs["wind_direction"] == "NNW"  # dv_md 335 deg
        assert obs["water_temp_c"] == 24.69
        assert obs["observed_at"] == "2026-07-10 16:00 UTC"

    def test_factor_scaling_example_from_research(self):
        # Documented example: Tp valor "859", factor 100 -> 8.59 s
        payload = {
            "fecha": "2026-07-10 14:00:00.0",
            "datos": [
                {"nombreColumna": "hm0", "valor": "120", "factor": 100.0},
                {"nombreColumna": "tp", "valor": "859", "factor": 100.0},
            ],
        }
        obs = parse_portus_lastdata(payload)
        assert obs["dominant_period_s"] == 8.59
        assert obs["wave_height_m"] == 1.2

    def test_coastal_buoy_without_direction_wind_or_temp(self):
        # Real case (station 1103): only wave height/period sensors report
        payload = {
            "fecha": "2026-07-10 15:00:00.0",
            "datos": [
                {"nombreColumna": "hm0", "valor": "60", "factor": 100.0},
                {"nombreColumna": "tp", "valor": "630", "factor": 100.0},
                {"nombreColumna": None, "valor": "1", "factor": 1.0},  # observed in the wild
            ],
        }
        obs = parse_portus_lastdata(payload)
        assert obs["wave_height_m"] == 0.6
        assert obs["dominant_period_s"] == 6.3
        assert obs["mean_wave_direction"] is None
        assert obs["wind_ms"] is None
        assert obs["water_temp_c"] is None

    def test_no_wave_height_returns_none(self):
        payload = {"fecha": "2026-07-10 16:00:00.0", "datos": [{"nombreColumna": "tp", "valor": "820", "factor": 100.0}]}
        assert parse_portus_lastdata(payload) is None

    def test_empty_response_returns_none(self):
        assert parse_portus_lastdata({}) is None
        assert parse_portus_lastdata(None) is None


class TestNearestPortusStation:
    STATIONS = [
        {"id": 1103, "nombre": "Boya Costera de Bilbao II", "latitud": 43.397, "longitud": -3.13, "disponible": True},
        {"id": 2136, "nombre": "Boya de Bilbao-Vizcaya", "latitud": 43.63, "longitud": -3.03, "disponible": True},
        {"id": 9999, "nombre": "Retired", "latitud": 43.40, "longitud": -3.01, "disponible": False},
        {"id": 1234, "nombre": "Canarias", "latitud": 28.05, "longitud": -15.39, "disponible": True},
    ]

    def test_picks_nearest_available(self):
        # Sopelana: the retired buoy is nearest but unavailable; Bilbao II wins
        station, distance = nearest_portus_station(self.STATIONS, 43.38, -3.01)
        assert station["id"] == 1103
        assert 0 < distance < 15

    def test_skips_stations_beyond_max_distance(self):
        # Madrid is far from every wave buoy
        assert nearest_portus_station(self.STATIONS, 40.42, -3.70) is None

    def test_no_stations(self):
        assert nearest_portus_station([], 43.38, -3.01) is None


class TestCoversSpain:
    """The PORTUS region must hug the Spanish coast: polling the rate-limited
    API for spots no Spanish buoy can reach wastes its per-run request."""

    def test_spanish_coasts_covered(self):
        covered = [
            (43.407, -2.699),  # Mundaka, Basque coast
            (42.2, -8.9),      # Galicia (Vigo)
            (36.5, -6.3),      # Cadiz
            (39.5, 2.6),       # Mallorca
            (41.4, 2.2),       # Barcelona
            (28.1, -15.4),     # Canarias (Las Palmas)
        ]
        for lat, lon in covered:
            assert fetch_conditions._covers_spain(lat, lon), (lat, lon)

    def test_far_iberian_and_french_spots_excluded(self):
        excluded = [
            (39.6, -9.07),     # Nazare, Portugal
            (38.7, -9.42),     # Lisbon coast
            (37.1, -8.0),      # Algarve
            (43.66, -1.44),    # Hossegor, France
            (37.759, -122.513),  # Ocean Beach, US
        ]
        for lat, lon in excluded:
            assert not fetch_conditions._covers_spain(lat, lon), (lat, lon)


class TestBuoyNetworkRegistry:
    MUNDAKA = (43.407, -2.699)
    OCEAN_BEACH = (37.759, -122.513)
    OBSERVATION = {
        "station": {"id": "2136", "name": "Boya de Bilbao-Vizcaya", "distance_km": 36.0, "url": "https://portus.puertos.es/"},
        "observed_at": "2026-07-10 16:00 UTC",
        "wave_height_m": 0.7,
        "dominant_period_s": 8.2,
        "mean_wave_direction": "NW",
        "wind_ms": 1.64,
        "wind_direction": "NNW",
        "water_temp_c": 24.69,
    }

    def test_spain_uses_portus_and_skips_ndbc(self, monkeypatch):
        calls = []
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_portus", lambda lat, lon: self.OBSERVATION)
        monkeypatch.setattr(
            fetch_conditions, "fetch_buoy_ndbc", lambda lat, lon: calls.append("ndbc") or {"error": "x", "note": "y"}
        )
        result = fetch_conditions.fetch_buoy(*self.MUNDAKA)
        assert result == self.OBSERVATION
        assert calls == [], "NDBC must not be polled when Puertos del Estado delivers"

    def test_spain_falls_back_to_ndbc_when_portus_fails(self, monkeypatch):
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_portus", lambda lat, lon: {"error": "portus down", "note": "n"})
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_ndbc", lambda lat, lon: self.OBSERVATION)
        assert fetch_conditions.fetch_buoy(*self.MUNDAKA) == self.OBSERVATION

    def test_us_never_polls_portus(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            fetch_conditions, "fetch_buoy_portus", lambda lat, lon: calls.append("portus") or self.OBSERVATION
        )
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_ndbc", lambda lat, lon: self.OBSERVATION)
        fetch_conditions.fetch_buoy(*self.OCEAN_BEACH)
        assert calls == []

    def test_all_networks_failing_aggregates_errors(self, monkeypatch):
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_portus", lambda lat, lon: {"error": "portus down", "note": "n1"})
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_ndbc", lambda lat, lon: {"error": "ndbc down", "note": "n2"})
        result = fetch_conditions.fetch_buoy(*self.MUNDAKA)
        assert "portus down" in result["error"] and "ndbc down" in result["error"]
        assert result["note"], "degradation contract requires a manual-fallback note"


class TestParseWorldtidesExtremes:
    """Fixture mirrors the WorldTides v3 extremes response shape (datum=CD,
    localtime requested so `date` carries the spot's local offset)."""

    PAYLOAD = {
        "status": 200,
        "requestDatum": "CD",
        "responseDatum": "CD",
        "station": "Bermeo, Spain",
        "copyright": "Tidal data retrieved from www.worldtides.info",
        "extremes": [
            {"dt": 1783051920, "date": "2026-07-10T04:12+0200", "height": 4.113, "type": "High"},
            {"dt": 1783074660, "date": "2026-07-10T10:31+0200", "height": 0.542, "type": "Low"},
            {"dt": 1783141320, "date": "2026-07-11T05:02+0200", "height": 4.35, "type": "High"},
        ],
    }

    def test_groups_events_by_local_date(self):
        tides = parse_worldtides_extremes(self.PAYLOAD)
        assert [d["date"] for d in tides["days"]] == ["2026-07-10", "2026-07-11"]
        first_day = tides["days"][0]["events"]
        assert first_day == [
            {"time": "04:12", "height_m": 4.113, "type": "high"},
            {"time": "10:31", "height_m": 0.542, "type": "low"},
        ]

    def test_datum_and_source_echoed(self):
        tides = parse_worldtides_extremes(self.PAYLOAD)
        assert tides["datum"] == "CD"
        assert tides["source"] == "WorldTides"

    def test_station_and_copyright_carried_when_present(self):
        tides = parse_worldtides_extremes(self.PAYLOAD)
        assert tides["station"]["name"] == "Bermeo, Spain"
        assert "worldtides.info" in tides["copyright"]

    def test_atlas_response_without_station(self):
        payload = {k: v for k, v in self.PAYLOAD.items() if k != "station"}
        tides = parse_worldtides_extremes(payload)
        assert "station" not in tides
        assert tides["days"], "extremes must still parse without a named station"


class TestTideSourceLadder:
    """NOAA CO-OPS stays primary where it has a station; WorldTides steps in
    only when NOAA gaps out AND WORLDTIDES_KEY is set; no key degrades to the
    manual-fallback note (ADR 0001)."""

    NOAA_OK = {
        "source": "NOAA CO-OPS",
        "station": {"id": "9414290", "url": "https://example.test"},
        "datum": "MLLW",
        "days": [{"date": "2026-07-10", "events": [{"time": "04:12", "height_m": 1.2, "type": "high"}]}],
    }
    NOAA_GAP = {"error": "Nearest NOAA station is 5000 km away", "note": fetch_conditions.TIDE_FALLBACK_NOTE}
    WORLDTIDES_OK = {
        "source": "WorldTides",
        "datum": "CD",
        "days": [{"date": "2026-07-10", "events": [{"time": "04:12", "height_m": 4.1, "type": "high"}]}],
    }

    def test_noaa_primary_even_with_key_set(self, monkeypatch):
        calls = []
        monkeypatch.setenv("WORLDTIDES_KEY", "sekret123")
        monkeypatch.setattr(fetch_conditions, "fetch_tides_noaa", lambda lat, lon, days: self.NOAA_OK)
        monkeypatch.setattr(
            fetch_conditions,
            "fetch_tides_worldtides",
            lambda lat, lon, days, key: calls.append("wt") or self.WORLDTIDES_OK,
        )
        assert fetch_conditions.fetch_tides(43.4, -2.7, 2) == self.NOAA_OK
        assert calls == [], "WorldTides must not be polled when NOAA delivers"

    def test_worldtides_when_noaa_gaps_and_key_set(self, monkeypatch):
        monkeypatch.setenv("WORLDTIDES_KEY", "sekret123")
        monkeypatch.setattr(fetch_conditions, "fetch_tides_noaa", lambda lat, lon, days: self.NOAA_GAP)
        monkeypatch.setattr(
            fetch_conditions, "fetch_tides_worldtides", lambda lat, lon, days, key: self.WORLDTIDES_OK
        )
        assert fetch_conditions.fetch_tides(43.4, -2.7, 2) == self.WORLDTIDES_OK

    def test_key_passed_from_environment(self, monkeypatch):
        seen = []
        monkeypatch.setenv("WORLDTIDES_KEY", "sekret123")
        monkeypatch.setattr(fetch_conditions, "fetch_tides_noaa", lambda lat, lon, days: self.NOAA_GAP)
        monkeypatch.setattr(
            fetch_conditions,
            "fetch_tides_worldtides",
            lambda lat, lon, days, key: seen.append(key) or self.WORLDTIDES_OK,
        )
        fetch_conditions.fetch_tides(43.4, -2.7, 2)
        assert seen == ["sekret123"]

    def test_no_key_degrades_to_manual_note(self, monkeypatch):
        monkeypatch.delenv("WORLDTIDES_KEY", raising=False)
        monkeypatch.setattr(fetch_conditions, "fetch_tides_noaa", lambda lat, lon, days: self.NOAA_GAP)
        monkeypatch.setattr(
            fetch_conditions,
            "fetch_tides_worldtides",
            lambda lat, lon, days, key: pytest.fail("WorldTides must not be called without a key"),
        )
        result = fetch_conditions.fetch_tides(43.4, -2.7, 2)
        assert result["error"] == self.NOAA_GAP["error"]
        assert "WORLDTIDES_KEY" in result["note"]
        assert "tide-forecast.com" in result["note"]

    def test_both_sources_failing_aggregates_errors(self, monkeypatch):
        monkeypatch.setenv("WORLDTIDES_KEY", "sekret123")
        monkeypatch.setattr(fetch_conditions, "fetch_tides_noaa", lambda lat, lon, days: self.NOAA_GAP)
        monkeypatch.setattr(
            fetch_conditions,
            "fetch_tides_worldtides",
            lambda lat, lon, days, key: {"error": "WorldTides request failed", "note": "n"},
        )
        result = fetch_conditions.fetch_tides(43.4, -2.7, 2)
        assert "5000 km away" in result["error"] and "WorldTides request failed" in result["error"]
        assert result["note"], "degradation contract requires a manual-fallback note"


class TestWorldtidesKeySafety:
    """The key comes from the environment only and must never leak into the
    payload: httpx exceptions embed the request URL, key and all."""

    KEY = "sekret-key-123"

    def test_transport_error_never_leaks_key(self, monkeypatch):
        class RaisingClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None):
                raise RuntimeError(f"connect failed for {url}?key={params['key']}")

        monkeypatch.setattr(fetch_conditions.httpx, "Client", RaisingClient)
        result = fetch_conditions.fetch_tides_worldtides(43.4, -2.7, 2, self.KEY)
        assert "error" in result
        assert self.KEY not in json.dumps(result)

    def test_url_encoded_key_never_leaks_either(self, monkeypatch):
        # httpx exception text carries the percent-encoded request URL, a
        # different spelling of the same secret
        key = "sekret+key/123"
        encoded = "sekret%2Bkey%2F123"

        class RaisingClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None):
                raise RuntimeError(f"connect failed for {url}?key={encoded}")

        monkeypatch.setattr(fetch_conditions.httpx, "Client", RaisingClient)
        result = fetch_conditions.fetch_tides_worldtides(43.4, -2.7, 2, key)
        payload = json.dumps(result)
        assert key not in payload
        assert encoded not in payload

    def test_api_error_payload_never_leaks_key(self, monkeypatch):
        class Response:
            def json(self):
                return {"status": 400, "error": f"invalid key: {TestWorldtidesKeySafety.KEY}"}

        class ErrorClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None):
                return Response()

        monkeypatch.setattr(fetch_conditions.httpx, "Client", ErrorClient)
        result = fetch_conditions.fetch_tides_worldtides(43.4, -2.7, 2, self.KEY)
        assert "error" in result
        assert result["note"], "degradation contract requires a manual-fallback note"
        assert self.KEY not in json.dumps(result)


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
    "source": "NOAA CO-OPS",
    "station": {"id": "9414290", "name": "Test Station", "state": "", "distance_km": 5.0, "url": "https://example.test"},
    "datum": "MLLW",
    "days": [
        {
            "date": "2026-07-10",
            "events": [{"time": "04:12", "height_m": 1.234, "type": "high"}],
        }
    ],
}

WORLDTIDES_SI = {
    "source": "WorldTides",
    "datum": "CD",
    "station": {"name": "Bermeo, Spain", "url": "https://www.worldtides.info/"},
    "days": [
        {
            "date": "2026-07-10",
            "events": [{"time": "04:12", "height_m": 4.113, "type": "high"}],
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

    def test_worldtides_shape_converts_and_echoes_datum(self, patched_fetchers, monkeypatch):
        monkeypatch.setattr(fetch_conditions, "fetch_tides", lambda lat, lon, days: WORLDTIDES_SI)
        metric = run_cli()
        assert metric["tides"]["source"] == "WorldTides"
        assert metric["tides"]["datum"] == "CD"
        assert metric["tides"]["days"][0]["events"][0]["height"] == 4.11
        imperial = run_cli("--units", "imperial")
        assert imperial["tides"]["days"][0]["events"][0]["height"] == 13.5  # 4.113 m


class TestHourlySeries:
    def test_hours_present_and_hourly(self, patched_fetchers):
        day = run_cli()["marine"]["days"][0]
        # MARINE_RAW carries hourly samples at 05:00 and 08:00 only.
        assert [h["time"] for h in day["hours"]] == ["05:00", "08:00"]
        h0 = day["hours"][0]
        for key in ("swell_height", "swell_period_s", "swell_direction",
                    "swell_direction_deg", "wind_speed", "wind_direction",
                    "wind_direction_deg", "wind_type", "quality"):
            assert key in h0, key
        assert h0["swell_height"] == 0.9
        assert h0["swell_period_s"] == 12.0
        assert h0["swell_direction_deg"] == 300.0
        assert h0["wind_direction_deg"] == 90.0

    def test_hours_convert_with_units(self, patched_fetchers):
        m = run_cli("--units", "metric")["marine"]["days"][0]["hours"][0]
        i = run_cli("--units", "imperial")["marine"]["days"][0]["hours"][0]
        assert m["swell_height"] == 0.9  # m
        assert i["swell_height"] == 3.0  # 0.9 m -> ft
        assert m["wind_speed"] == 18  # 5 m/s -> km/h
        assert i["wind_speed"] == 10  # 5 m/s -> kn

    def test_hours_omit_quality_without_facing(self):
        days = fetch_conditions.build_marine_days(MARINE_RAW, WIND_RAW, None, "metric")
        h0 = days[0]["hours"][0]
        assert "quality" not in h0
        assert h0["wind_type"] is None


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


class TestCliArchive:
    """The forecast side of the verification loop: --archive appends one JSONL
    snapshot per forecast day per spot to forecasts/<slug>.jsonl."""

    def test_appends_one_line_per_forecast_day(self, patched_fetchers, tmp_path):
        data = run_cli("--archive", str(tmp_path))
        path = tmp_path / "mundaka.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1  # MARINE_RAW carries a single forecast day
        record = json.loads(lines[0])
        assert record["date"] == "2026-07-10"
        assert record["spot_slug"] == "mundaka"
        assert record["swell_height"] == 1.1  # summary swell_height_max, display units
        assert record["swell_period_s"] == 13.0
        assert record["swell_direction"] == "WNW"  # 302 deg dominant
        assert record["units"]["system"] == "metric"
        assert record["archived_on"], "snapshot must be stamped with the run date"
        assert "best_window" in record, "facing 315 yields a surf window to snapshot"
        assert data["archive"]["appended"] == 1
        assert data["archive"]["path"].endswith("mundaka.jsonl")

    def test_append_only_accumulates(self, patched_fetchers, tmp_path):
        run_cli("--archive", str(tmp_path))
        run_cli("--archive", str(tmp_path))
        lines = (tmp_path / "mundaka.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, "the archive is append-only, never overwritten"

    def test_creates_archive_directory(self, patched_fetchers, tmp_path):
        target = tmp_path / "forecasts"
        run_cli("--archive", str(target))
        assert (target / "mundaka.jsonl").exists()

    def test_no_forecast_records_gap_and_writes_nothing(self, monkeypatch, tmp_path):
        error = {"error": "down", "note": "check manually"}
        for name in ("fetch_marine", "fetch_wind_weather", "fetch_buoy", "fetch_tides", "fetch_daylight"):
            monkeypatch.setattr(fetch_conditions, name, lambda *a, **k: error)
        data = run_cli("--archive", str(tmp_path))
        assert list(tmp_path.iterdir()) == [], "no forecast window means no snapshot"
        assert any("archive" in g for g in data["gaps"])


class TestCliModelBias:
    """conditions/week/briefing apply a spot's stored model bias: --spot-file
    with a model_bias block offsets the swell numbers before quality/windows and
    echoes what it applied under `bias` (heights in the payload's display units)."""

    BIAS_BLOCK = (
        "model_bias:\n"
        "  swell_height_m: 0.5\n"
        "  swell_period_s: 1.0\n"
        "  samples: 3\n"
        "  last_verified: 2026-07-12\n"
        "  note: model under-calls size by ~0.5 m\n"
    )

    @pytest.fixture
    def pinned_buoy_ok(self, monkeypatch):
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_pinned", lambda cfg: BUOY_SI)

    def _spot_with_bias(self, tmp_path, block=None):
        path = tmp_path / "mundaka.yaml"
        path.write_text(SPOT_PROFILE_YAML + (block if block is not None else self.BIAS_BLOCK))
        return str(path)

    def test_bias_offsets_swell_and_echoes_applied(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        data = json.loads(invoke_cli("--spot-file", self._spot_with_bias(tmp_path)).output)
        block = data["marine"]["days"][0]["blocks"][0]
        assert block["swell_height"] == 1.4  # 0.9 m + 0.5 m
        assert block["swell_period_s"] == 13.0  # 12.0 s + 1.0 s
        assert data["marine"]["days"][0]["summary"]["swell_height_max"] == 1.6  # 1.1 + 0.5
        bias = data["bias"]
        assert bias["applied"] is True
        assert bias["swell_height"] == 0.5  # metric echo of the stored offset
        assert bias["swell_period_s"] == 1.0
        assert bias["samples"] == 3
        assert "under-calls" in bias["note"]

    def test_bias_echo_in_display_units(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        data = json.loads(invoke_cli("--spot-file", self._spot_with_bias(tmp_path), "--units", "imperial").output)
        assert data["marine"]["days"][0]["blocks"][0]["swell_height"] == 4.6  # 1.4 m -> ft
        assert data["bias"]["swell_height"] == 1.6  # 0.5 m -> ft

    def test_no_bias_block_leaves_numbers_untouched(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        path = tmp_path / "mundaka.yaml"
        path.write_text(SPOT_PROFILE_YAML)
        data = json.loads(invoke_cli("--spot-file", str(path)).output)
        assert data["marine"]["days"][0]["blocks"][0]["swell_height"] == 0.9
        assert "bias" not in data

    def test_negative_bias_floors_at_zero(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        path = self._spot_with_bias(tmp_path, block="model_bias:\n  swell_height_m: -2.0\n")
        data = json.loads(invoke_cli("--spot-file", path).output)
        assert data["marine"]["days"][0]["blocks"][0]["swell_height"] == 0.0

    def test_archive_snapshots_raw_forecast_not_biased(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        # The loop must not un-learn itself: with a bias applied, the payload is
        # corrected but the archived snapshot keeps the RAW model forecast, so a
        # later /surfing:verify judges the model's own prediction.
        archive_dir = tmp_path / "forecasts"
        data = json.loads(
            invoke_cli("--spot-file", self._spot_with_bias(tmp_path), "--archive", str(archive_dir)).output
        )
        assert data["marine"]["days"][0]["summary"]["swell_height_max"] == 1.6  # bias-corrected view
        line = json.loads((archive_dir / "mundaka.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert line["swell_height"] == 1.1  # raw summary (1.1), never the biased 1.6
        assert line["swell_period_s"] == 13.0  # raw period, not 14.0


class TestCliValidation:
    def test_invalid_coordinates_exit_1(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--coordinates", "bogus", "--spot-name", "X"])
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)


# ---------------------------------------------------------------------------
# Spot profile (--spot-file) and surfer profile (--surfer-file) at the CLI seam
# ---------------------------------------------------------------------------

SPOT_PROFILE_YAML = """\
name: Mundaka
region: Bizkaia, Basque Country, Spain
coordinates: [43.407, -2.699]
facing_deg: 350
tide_source: WorldTides
tide_station: null
buoy:
  network: Puertos del Estado
  station_id: "2136"
  name: Boya de Bilbao-Vizcaya
  distance_km: 36.0
works_on:
  swell_direction: NW
  min_period_s: 12
last_researched: 2026-01-10
"""

SURFER_PROFILE_YAML = """\
name: Elena
skill_level: intermediate
boards:
  - name: daily driver
    type: shortboard
home_spots:
  - mundaka
units: imperial
target_days: [saturday, sunday]
"""


def invoke_cli(*args):
    return CliRunner().invoke(cli, list(args))


class TestCliSpotFile:
    @pytest.fixture
    def spot_path(self, tmp_path):
        path = tmp_path / "mundaka.yaml"
        path.write_text(SPOT_PROFILE_YAML)
        return str(path)

    @pytest.fixture
    def pinned_buoy_ok(self, monkeypatch):
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_pinned", lambda cfg: BUOY_SI)

    def test_profile_supplies_coordinates_name_and_facing(
        self, patched_fetchers, pinned_buoy_ok, monkeypatch, spot_path
    ):
        seen = {}
        monkeypatch.setattr(
            fetch_conditions, "fetch_marine", lambda lat, lon, days: seen.update(coords=(lat, lon)) or MARINE_RAW
        )
        result = invoke_cli("--spot-file", spot_path)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["spot"]["name"] == "Mundaka"
        assert data["spot"]["coordinates"] == [43.407, -2.699]
        assert seen["coords"] == (43.407, -2.699)
        assert data["spot"]["facing_deg"] == 350
        assert data["surf_windows"], "facing from the profile must enable surf windows"

    def test_pinned_buoy_skips_nearest_lookup(self, patched_fetchers, monkeypatch, spot_path):
        seen = {}
        monkeypatch.setattr(
            fetch_conditions, "fetch_buoy_pinned", lambda cfg: seen.update(cfg=cfg) or BUOY_SI
        )
        monkeypatch.setattr(
            fetch_conditions, "fetch_buoy", lambda lat, lon: pytest.fail("nearest-station lookup must be skipped")
        )
        result = invoke_cli("--spot-file", spot_path)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert seen["cfg"]["station_id"] == "2136"
        assert data["buoy"]["wave_height"] == 1.5

    def test_pinned_buoy_failure_falls_back_to_registry(self, patched_fetchers, monkeypatch, spot_path):
        monkeypatch.setattr(
            fetch_conditions, "fetch_buoy_pinned", lambda cfg: {"error": "station 2136 silent", "note": "n"}
        )
        monkeypatch.setattr(fetch_conditions, "fetch_buoy", lambda lat, lon: BUOY_SI)
        result = invoke_cli("--spot-file", spot_path)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["buoy"]["wave_height"] == 1.5, "registry lookup must take over"
        assert any("pinned" in g for g in data["gaps"]), "fallback must be reported as a gap"

    def test_tide_station_from_profile(self, patched_fetchers, pinned_buoy_ok, monkeypatch, tmp_path):
        profile = SPOT_PROFILE_YAML.replace("tide_station: null", 'tide_station: "9414290"')
        path = tmp_path / "spot.yaml"
        path.write_text(profile)
        seen = {}
        monkeypatch.setattr(
            fetch_conditions,
            "_fetch_tide_predictions",
            lambda station_id, days: seen.update(station=station_id) or TIDES_SI,
        )
        monkeypatch.setattr(
            fetch_conditions, "fetch_tides", lambda lat, lon, days: pytest.fail("ladder must be skipped")
        )
        result = invoke_cli("--spot-file", str(path))
        assert result.exit_code == 0, result.output
        assert seen["station"] == "9414290"

    def test_explicit_flags_override_profile(self, patched_fetchers, pinned_buoy_ok, monkeypatch, spot_path):
        seen = {}
        monkeypatch.setattr(
            fetch_conditions, "fetch_marine", lambda lat, lon, days: seen.update(coords=(lat, lon)) or MARINE_RAW
        )
        monkeypatch.setattr(
            fetch_conditions,
            "_fetch_tide_predictions",
            lambda station_id, days: seen.update(station=station_id) or TIDES_SI,
        )
        result = invoke_cli(
            "--spot-file", spot_path,
            "--coordinates", "37.759,-122.513",
            "--spot-name", "Ocean Beach",
            "--facing", "265",
            "--tide-station", "9414290",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["spot"]["name"] == "Ocean Beach"
        assert data["spot"]["coordinates"] == [37.759, -122.513]
        assert seen["coords"] == (37.759, -122.513)
        assert data["spot"]["facing_deg"] == 265
        assert seen["station"] == "9414290"

    def _profile_aged(self, tmp_path, age_days):
        last = (date.today() - timedelta(days=age_days)).isoformat()
        path = tmp_path / "spot.yaml"
        path.write_text(SPOT_PROFILE_YAML.replace("last_researched: 2026-01-10", f"last_researched: {last}"))
        return str(path)

    def test_fresh_profile_age_echoed(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        result = invoke_cli("--spot-file", self._profile_aged(tmp_path, 30))
        assert result.exit_code == 0, result.output
        profile = json.loads(result.output)["spot"]["profile"]
        assert profile["age_days"] == 30
        assert profile["reresearch_suggested"] is False
        assert profile["last_researched"] == (date.today() - timedelta(days=30)).isoformat()

    def test_stale_profile_suggests_reresearch(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        result = invoke_cli("--spot-file", self._profile_aged(tmp_path, 200))
        assert result.exit_code == 0, result.output
        profile = json.loads(result.output)["spot"]["profile"]
        assert profile["age_days"] == 200
        assert profile["reresearch_suggested"] is True

    def test_profile_without_last_researched(self, patched_fetchers, pinned_buoy_ok, tmp_path):
        path = tmp_path / "spot.yaml"
        path.write_text(SPOT_PROFILE_YAML.replace("last_researched: 2026-01-10\n", ""))
        result = invoke_cli("--spot-file", str(path))
        assert result.exit_code == 0, result.output
        profile = json.loads(result.output)["spot"]["profile"]
        assert profile["last_researched"] is None
        assert profile["age_days"] is None
        assert profile["reresearch_suggested"] is None

    def test_missing_spot_file_exits_1(self):
        result = invoke_cli("--spot-file", "/nonexistent/spots/nowhere.yaml")
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_invalid_yaml_exits_1(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("coordinates: [43.407, -2.699")
        result = invoke_cli("--spot-file", str(path))
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_profile_without_coordinates_exits_1(self, tmp_path):
        path = tmp_path / "spot.yaml"
        path.write_text("name: Nowhere\nfacing_deg: 270\n")
        result = invoke_cli("--spot-file", str(path))
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_no_spot_file_and_no_coordinates_still_exits_1(self):
        result = invoke_cli("--spot-name", "Mundaka")
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)


class TestCliSurferFile:
    @pytest.fixture
    def surfer_path(self, tmp_path):
        path = tmp_path / "surfer.yaml"
        path.write_text(SURFER_PROFILE_YAML)
        return str(path)

    def run(self, *extra):
        return invoke_cli("--coordinates", "43.407,-2.699", "--spot-name", "Mundaka", *extra)

    def test_surfer_units_apply(self, patched_fetchers, surfer_path):
        result = self.run("--surfer-file", surfer_path)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["units"]["system"] == "imperial"

    def test_units_flag_overrides_surfer(self, patched_fetchers, surfer_path):
        result = self.run("--surfer-file", surfer_path, "--units", "metric")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["units"]["system"] == "metric"

    def test_surfer_without_units_defaults_metric(self, patched_fetchers, tmp_path):
        path = tmp_path / "surfer.yaml"
        path.write_text(SURFER_PROFILE_YAML.replace("units: imperial\n", ""))
        result = self.run("--surfer-file", str(path))
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["units"]["system"] == "metric"

    def test_missing_surfer_file_exits_1(self):
        result = self.run("--surfer-file", "/nonexistent/surfer.yaml")
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)

    def test_invalid_surfer_units_exit_1(self, tmp_path):
        path = tmp_path / "surfer.yaml"
        path.write_text("units: nautical\n")
        result = self.run("--surfer-file", str(path))
        assert result.exit_code == 1
        assert "error" in json.loads(result.output)


class _FakeResponse:
    def __init__(self, text="", payload=None, status_code=200):
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class TestFetchBuoyPinned:
    """The spot profile pins the buoy that research found representative:
    the fetch goes straight to that station, no station-list download."""

    def test_ndbc_station_fetched_directly(self, monkeypatch):
        seen = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                seen["url"] = url
                return _FakeResponse(text=TestParseNdbcRealtime.SAMPLE)

        monkeypatch.setattr(fetch_conditions.httpx, "Client", FakeClient)
        cfg = {"network": "NOAA NDBC", "station_id": "46026", "name": "SF Bar", "distance_km": 30.0}
        result = fetch_conditions.fetch_buoy_pinned(cfg)
        assert "46026" in seen["url"]
        assert result["wave_height_m"] == 1.5
        assert result["station"]["id"] == "46026"
        assert result["station"]["name"] == "SF Bar"
        assert result["station"]["distance_km"] == 30.0
        assert "station=46026" in result["station"]["url"]

    def test_portus_station_fetched_directly(self, monkeypatch):
        seen = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, **kwargs):
                seen["url"] = url
                return _FakeResponse(payload=TestParsePortusLastdata.LASTDATA)

        monkeypatch.setattr(fetch_conditions.httpx, "Client", FakeClient)
        cfg = {"network": "Puertos del Estado", "station_id": "2136", "name": "Boya de Bilbao-Vizcaya"}
        result = fetch_conditions.fetch_buoy_pinned(cfg)
        assert "2136" in seen["url"]
        assert result["wave_height_m"] == 0.7
        assert result["station"]["id"] == "2136"
        assert result["station"]["name"] == "Boya de Bilbao-Vizcaya"

    def test_unknown_network_errors(self):
        result = fetch_conditions.fetch_buoy_pinned({"network": "CANDHIS", "station_id": "1"})
        assert "error" in result
        assert result["note"], "degradation contract requires a manual-fallback note"

    def test_missing_station_id_errors(self):
        result = fetch_conditions.fetch_buoy_pinned({"network": "NOAA NDBC"})
        assert "error" in result

    def test_station_not_reporting_errors(self, monkeypatch):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                return _FakeResponse(text="")

        monkeypatch.setattr(fetch_conditions.httpx, "Client", FakeClient)
        result = fetch_conditions.fetch_buoy_pinned({"network": "NOAA NDBC", "station_id": "46026"})
        assert "error" in result
        assert result["note"]


class TestAssetTemplates:
    """The shipped templates must load through the CLI: the schema the skill
    writes and the schema the script reads are the same file."""

    ASSETS = Path(__file__).resolve().parent.parent / "assets"

    def test_spot_profile_template_loads_through_cli(self, patched_fetchers, monkeypatch):
        monkeypatch.setattr(fetch_conditions, "fetch_buoy_pinned", lambda cfg: BUOY_SI)
        result = invoke_cli("--spot-file", str(self.ASSETS / "spot-profile-template.yaml"))
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["spot"]["name"]
        assert data["spot"]["facing_deg"] is not None
        assert data["spot"]["profile"]["last_researched"]

    def test_surfer_template_loads_through_cli(self, patched_fetchers):
        result = invoke_cli(
            "--coordinates", "43.407,-2.699",
            "--spot-name", "Mundaka",
            "--surfer-file", str(self.ASSETS / "surfer-template.yaml"),
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["units"]["system"] == "metric"


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

    def test_non_us_spot_reports_tide_gap(self, monkeypatch):
        """Mundaka, Spain - marine data should work; with no WorldTides key
        NOAA tides gap out to the manual-fallback note."""
        monkeypatch.delenv("WORLDTIDES_KEY", raising=False)
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

    @pytest.mark.skipif(
        not os.environ.get("WORLDTIDES_KEY"),
        reason="Set WORLDTIDES_KEY to probe the live WorldTides API",
    )
    def test_non_us_spot_gets_worldtides_extremes_with_key(self):
        """Mundaka, Spain - with a key the tide ladder lands on WorldTides."""
        tides = fetch_conditions.fetch_tides(43.407, -2.699, 2)
        assert "error" not in tides, tides
        assert tides["source"] == "WorldTides"
        assert tides["datum"] == "CD"
        events = [e for day in tides["days"] for e in day["events"]]
        assert events, "expected tide extremes"
        assert {e["type"] for e in events} <= {"high", "low"}
        assert all(isinstance(e["height_m"], float) for e in events)

    def test_basque_coast_gets_portus_buoy_observation(self):
        """Mundaka, Spain - observed wave data must come from Puertos del Estado."""
        result = fetch_conditions.fetch_buoy(43.407, -2.699)
        assert "error" not in result, result
        assert result["wave_height_m"] is not None
        assert result["dominant_period_s"] is not None
        assert result["station"]["distance_km"] < fetch_conditions.MAX_BUOY_KM
        assert "portus.puertos.es" in result["station"]["url"]
