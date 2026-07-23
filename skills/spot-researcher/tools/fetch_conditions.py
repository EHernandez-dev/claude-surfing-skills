#!/usr/bin/env python3
"""Unified surf conditions fetcher - swell, wind, tides, water temp, daylight.

Fetches all environmental/conditions data for a surf spot from various APIs.
Returns unified JSON matching the data contract for the spot-researcher skill.

All quantities are SI internally (meters, m/s, degrees C); conversion happens
only at the output edge, controlled by --units (metric default, imperial
optional). JSON keys are unit-neutral and the payload echoes the units in
effect in a top-level `units` object.

Surf folder integration: --spot-file loads coordinates, name, facing, tide
station, and a pinned buoy from a spot profile (spots/<slug>.yaml); explicit
flags override. --surfer-file reads the units preference from the surfer
profile (surfer.yaml); precedence is flag, then surfer profile, then metric.
When a spot profile is used, the payload echoes its age under spot.profile
(profiles never expire; past ~6 months re-research is only suggested).

Data sources:
- Open-Meteo Marine API: wave/swell height, period, direction, sea surface temp
- Open-Meteo Forecast API: wind, air temp, precipitation, UV index
- NOAA CO-OPS: tide predictions (US stations only)
- WorldTides: tide extremes elsewhere, on chart datum, when the optional
  WORLDTIDES_KEY environment variable is set (ADR 0001)
- Buoy observations from a region-keyed network registry (real observed waves
  + water temp): NOAA NDBC everywhere it reaches, Puertos del Estado (PORTUS)
  for Spanish coasts (ADR 0002)
- astral: sunrise/sunset/twilight
"""

import json
import math
import math
import os
import re
import sys
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import click
import httpx
import yaml

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NOAA_STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
NOAA_PREDICTIONS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
WORLDTIDES_URL = "https://www.worldtides.info/api/v3"
NDBC_STATIONS_URL = "https://www.ndbc.noaa.gov/activestations.xml"
NDBC_REALTIME_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"

# Puertos del Estado PORTUS API (keyless, undocumented; ADR 0002). Polite
# polling: one observation request per spot per run, browser-like User-Agent.
PORTUS_STATIONS_URL = "https://portus.puertos.es/portussvr/api/estaciones/hist/WAVE"
PORTUS_LASTDATA_URL = "https://portus.puertos.es/portussvr/api/lastData/station/{station_id}"
PORTUS_PORTAL_URL = "https://portus.puertos.es/"
PORTUS_CATEGORIES = ["WAVE", "WIND", "WATER_TEMP", "AIR_TEMP", "SEA_LEVEL", "CURRENTS", "SALINITY"]
PORTUS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}

# Max distance to accept a NOAA tide station; beyond this the spot is
# probably outside the US and we report a gap instead of a wrong tide.
MAX_TIDE_STATION_KM = 80.0

# Max distance to accept a buoy (any network) for observed-conditions cross-check.
MAX_BUOY_KM = 150.0

FT_PER_M = 3.28084
KN_PER_MS = 1.94384
KMH_PER_MS = 3.6

# Wind below this is "light" regardless of direction (~6 kn, glassy-ish).
LIGHT_WIND_MS = 3.1

# Display labels echoed in the payload's `units` object.
UNIT_LABELS = {
    "metric": {"wave_height": "m", "tide_height": "m", "wind_speed": "km/h", "temperature": "°C"},
    "imperial": {"wave_height": "ft", "tide_height": "ft", "wind_speed": "kn", "temperature": "°F"},
}

# Filename slugs for the per-day verdict (Go / Worth a check / Skip).
VERDICT_SLUGS = ("go", "check", "skip")

# Start hours of the 3-hourly forecast blocks (05:00-21:00 local).
BLOCK_GRID_HOURS = range(5, 22, 3)

# Spot profiles never expire; past this age the payload suggests re-research.
RERESEARCH_AFTER_DAYS = 183

# Chart datum matches published European tide tables, mirroring NOAA's MLLW
# (ADR 0001). WorldTides' default MSL would match no published table.
WORLDTIDES_DATUM = "CD"

TIDE_FALLBACK_NOTE = (
    "NOAA CO-OPS covers US coasts only. Set WORLDTIDES_KEY for station-grade "
    "tide extremes elsewhere (https://www.worldtides.info), or check "
    "https://www.tide-forecast.com manually."
)

WORLDTIDES_MANUAL_NOTE = "Check https://www.tide-forecast.com manually."

# EOT20: free global harmonic tide model (CC-BY 4.0), predicted offline via the
# optional `pyTMD` dependency (ADR 0004). Needs a one-time ~2 GB model download
# (download_tide_model.py) into EOT20_DIR, which must contain EOT20/ocean_tides/.
# Heights are metres about mean sea level, so the datum is "MSL", not a chart
# datum; for surf, the timing of the highs/lows is what the report leans on.
EOT20_DIR_ENV = "EOT20_DIR"
DEFAULT_EOT20_DIR = os.path.expanduser("~/.cache/claude-surfing-skills/tide_models")
EOT20_SOURCE = "EOT20 (harmonic model)"
EOT20_DATUM = "MSL"
EOT20_SETUP_NOTE = (
    "Free offline tides (EOT20) are not set up: install the optional dependency "
    "(uv sync --extra tides) and run `uv run python download_tide_model.py`, "
    "or check https://www.tide-forecast.com manually."
)
# 10-minute sampling resolves high/low times to within ~5 min of the true turn.
EOT20_SAMPLE_MINUTES = 10

COMPASS_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

WEATHER_CODES = {
    0: ("Clear", "☀️"),
    1: ("Partly cloudy", "⛅"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Fog", "🌫️"),
    51: ("Light drizzle", "🌧️"),
    53: ("Drizzle", "🌧️"),
    55: ("Heavy drizzle", "🌧️"),
    61: ("Light rain", "🌧️"),
    63: ("Rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    80: ("Light showers", "🌧️"),
    81: ("Showers", "🌧️"),
    82: ("Heavy showers", "🌧️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with hail", "⛈️"),
    99: ("Thunderstorm with hail", "⛈️"),
}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no network)
# ---------------------------------------------------------------------------


def parse_coordinates(value: str) -> tuple[float, float]:
    """Parse 'lat,lon' into a (lat, lon) float tuple."""
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError(f"Expected 'lat,lon', got: {value!r}")
    lat, lon = float(parts[0].strip()), float(parts[1].strip())
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Coordinates out of range: {lat},{lon}")
    return lat, lon


def compass(degrees: float | None) -> str | None:
    """Convert degrees to a 16-point compass direction."""
    if degrees is None:
        return None
    return COMPASS_POINTS[int((degrees % 360) / 22.5 + 0.5) % 16]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def m_to_ft(meters: float | None) -> float | None:
    if meters is None:
        return None
    return round(meters * FT_PER_M, 1)


def c_to_f(celsius: float | None) -> float | None:
    if celsius is None:
        return None
    return round(celsius * 9 / 5 + 32, 1)


def height_out(meters: float | None, units: str) -> float | None:
    """Wave/swell height at the output edge: m (metric) or ft (imperial)."""
    if meters is None:
        return None
    return m_to_ft(meters) if units == "imperial" else round(meters, 1)


def tide_height_out(meters: float | None, units: str) -> float | None:
    """Tide height at the output edge: 2-decimal m (tide-table precision) or ft."""
    if meters is None:
        return None
    return m_to_ft(meters) if units == "imperial" else round(meters, 2)


def wind_out(ms: float | None, units: str) -> int | None:
    """Wind speed at the output edge: km/h (metric) or kn (imperial)."""
    if ms is None:
        return None
    return round(ms * (KN_PER_MS if units == "imperial" else KMH_PER_MS))


def temp_out(celsius: float | None, units: str) -> float | None:
    """Temperature at the output edge: deg C (metric) or deg F (imperial)."""
    if celsius is None:
        return None
    return c_to_f(celsius) if units == "imperial" else round(celsius, 1)


def slugify(name: str) -> str:
    """Filename slug for a spot name: lowercase ASCII, hyphen-separated."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "spot"


def report_filename(target_date: str, spot_name: str, verdict: str) -> str:
    """Report filename per the naming rule: {target-date}-{spot-slug}-{verdict}.md.

    The date is the target day (the day the surfer intends to surf), never
    the run date; callers fall back to the forecast window's first day.
    """
    if verdict not in VERDICT_SLUGS:
        raise ValueError(f"Verdict must be one of {VERDICT_SLUGS}, got: {verdict!r}")
    return f"{target_date}-{slugify(spot_name)}-{verdict}.md"


def load_yaml_mapping(path: str, kind: str) -> dict[str, Any]:
    """Load a YAML file that must contain a mapping. Raises ValueError so the
    CLI can treat a bad profile path or file as an invalid argument (exit 1)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise ValueError(f"Cannot read {kind} {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {kind} {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{kind} {path} must be a YAML mapping")
    return data


def profile_coordinates(profile: dict[str, Any]) -> str | None:
    """Spot profile coordinates as a 'lat,lon' string for parse_coordinates."""
    value = profile.get("coordinates")
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]},{value[1]}"
    raise ValueError(f"Spot profile coordinates must be [lat, lon], got: {value!r}")


def surfer_units(surfer: dict[str, Any]) -> str | None:
    """Units preference from the surfer profile, validated."""
    units = surfer.get("units")
    if units is None:
        return None
    if units not in UNIT_LABELS:
        raise ValueError(f"Surfer profile units must be one of {sorted(UNIT_LABELS)}, got: {units!r}")
    return units


def profile_age(last_researched: Any, today: date) -> dict[str, Any]:
    """Age block for the payload's spot.profile echo.

    Spot profiles never expire: past ~6 months the payload only flags
    `reresearch_suggested`, it never withholds the profile.
    """
    if isinstance(last_researched, datetime):
        last_researched = last_researched.date()
    elif isinstance(last_researched, str):
        try:
            last_researched = date.fromisoformat(last_researched)
        except ValueError:
            last_researched = None
    if not isinstance(last_researched, date):
        return {"last_researched": None, "age_days": None, "reresearch_suggested": None}
    age_days = (today - last_researched).days
    return {
        "last_researched": last_researched.isoformat(),
        "age_days": age_days,
        "reresearch_suggested": age_days > RERESEARCH_AFTER_DAYS,
    }


def classify_wind(wind_from_deg: float | None, facing_deg: float, speed_ms: float | None) -> str | None:
    """Classify wind relative to the shore.

    `facing_deg` is the direction the spot faces looking out to sea.
    Wind direction is meteorological (direction the wind blows FROM), so wind
    coming from the same direction the beach faces is onshore.
    Anything under ~3 m/s (6 kn) is 'light' regardless of direction.
    """
    if wind_from_deg is None:
        return None
    if speed_ms is not None and speed_ms < LIGHT_WIND_MS:
        return "light"
    diff = abs((wind_from_deg - facing_deg + 180) % 360 - 180)
    if diff <= 45:
        return "onshore"
    if diff < 135:
        return "cross-shore"
    return "offshore"


def rate_block(
    swell_ht_m: float | None,
    swell_period_s: float | None,
    wind_ms: float | None,
    wind_type: str | None,
) -> dict[str, Any] | None:
    """Heuristic surf quality score (0-10) for one forecast block (SI inputs).

    Rewards long-period swell in the rideable size band with light or
    offshore wind; punishes strong onshore wind. This is a generic
    heuristic - it does NOT know spot-specific swell windows, so the skill
    must cross-check against the spot's ideal conditions from research.
    """
    if swell_ht_m is None:
        return None
    if swell_ht_m < 0.3:
        return {"score": 0, "rating": "flat"}

    score = 0.0

    # Swell period: the single best proxy for wave quality
    if swell_period_s is not None:
        if swell_period_s >= 13:
            score += 4
        elif swell_period_s >= 11:
            score += 3
        elif swell_period_s >= 9:
            score += 2
        elif swell_period_s >= 7:
            score += 1

    # Size: 0.6-2.4 m (2-8 ft) significant swell is the sweet spot for most breaks
    if 0.6 <= swell_ht_m <= 2.4:
        score += 3
    elif 0.3 <= swell_ht_m < 0.6 or 2.4 < swell_ht_m <= 3.7:
        score += 2
    else:
        score += 1

    # Wind (m/s thresholds ~ 25 / 12 / 10 kn)
    if wind_type == "light":
        score += 3
    elif wind_type == "offshore":
        score += 4 if (wind_ms or 0) <= 13 else 1
    elif wind_type == "cross-shore":
        score += 2 if (wind_ms or 0) < 6 else 1
    elif wind_type == "onshore":
        score += 1 if (wind_ms or 0) < 5 else 0

    score = round(min(score, 11) / 11 * 10, 1)
    # Short-period windswell is weak and disorganized regardless of size or
    # wind - cap it at "poor" so light-wind days don't inflate the rating.
    if swell_period_s is not None and swell_period_s < 7:
        score = min(score, 3.5)
    if score >= 8:
        rating = "epic"
    elif score >= 6:
        rating = "good"
    elif score >= 4:
        rating = "fair"
    else:
        rating = "poor"
    return {"score": score, "rating": rating}


def wetsuit_for(water_temp_c: float | None) -> str | None:
    """Wetsuit recommendation from water temperature (deg C)."""
    if water_temp_c is None:
        return None
    if water_temp_c >= 24:
        return "Boardshorts / rash guard"
    if water_temp_c >= 21:
        return "1-2mm top or spring suit"
    if water_temp_c >= 18:
        return "2mm spring suit or 3/2 fullsuit"
    if water_temp_c >= 14.5:
        return "3/2 fullsuit"
    if water_temp_c >= 11:
        return "4/3 fullsuit + booties"
    if water_temp_c >= 6:
        return "5/4 hooded fullsuit + booties + gloves"
    return "6/5+ hooded fullsuit, booties, gloves (extreme cold)"


# ---------------------------------------------------------------------------
# Per-spot model bias (the verification loop, /surfing:verify)
# ---------------------------------------------------------------------------

# Marine series adjusted by a stored model bias. Heights take the height bias,
# swell period takes the period bias; wind-wave height and SST are left alone
# (the bias describes how the model misses ground-swell size at this break).
_BIAS_HEIGHT_KEYS = {"hourly": ("wave_height", "swell_wave_height"),
                     "daily": ("wave_height_max", "swell_wave_height_max")}
_BIAS_PERIOD_KEYS = {"hourly": ("swell_wave_period",),
                     "daily": ("swell_wave_period_max",)}


def parse_model_bias(profile: dict[str, Any]) -> dict[str, Any] | None:
    """The spot profile's stored model bias, coerced to a numeric offset.

    `model_bias` is written by /surfing:verify from session logs vs archived
    forecasts and stored in metric (meters, seconds): `swell_height_m` and the
    optional `swell_period_s` are `observed - forecast`, so a positive value
    means the model under-calls and the offset is added to future forecasts.
    Returns None when the profile carries no `model_bias` mapping.
    """
    cfg = profile.get("model_bias")
    if not isinstance(cfg, dict):
        return None

    def num(key: str) -> float:
        try:
            value = cfg.get(key)
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    last_verified = cfg.get("last_verified")
    if isinstance(last_verified, datetime):
        last_verified = last_verified.date().isoformat()
    elif isinstance(last_verified, date):
        last_verified = last_verified.isoformat()
    return {
        "swell_height_m": num("swell_height_m"),
        "swell_period_s": num("swell_period_s"),
        "samples": cfg.get("samples"),
        "last_verified": last_verified,
        "note": cfg.get("note"),
    }


def _offset_series(series: list[Any], delta: float) -> list[Any]:
    """Add `delta` to each numeric entry, flooring at 0 (no negative wave/period)."""
    return [None if v is None else round(max(0.0, v + delta), 3) for v in series]


def apply_swell_bias(
    marine_raw: dict[str, Any], height_bias_m: float, period_bias_s: float
) -> dict[str, Any]:
    """Return marine data with swell/wave heights and swell period offset by the
    stored bias (SI meters/seconds), so the biased numbers flow into the block
    quality, surf windows, and the report before any unit conversion. A no-op on
    a marine error payload or when both offsets are zero.
    """
    if "error" in marine_raw or (not height_bias_m and not period_bias_s):
        return marine_raw
    adjusted = dict(marine_raw)
    for section in ("hourly", "daily"):
        block = marine_raw.get(section)
        if not isinstance(block, dict):
            continue
        new_block = dict(block)
        if height_bias_m:
            for key in _BIAS_HEIGHT_KEYS[section]:
                if key in new_block:
                    new_block[key] = _offset_series(new_block[key], height_bias_m)
        if period_bias_s:
            for key in _BIAS_PERIOD_KEYS[section]:
                if key in new_block:
                    new_block[key] = _offset_series(new_block[key], period_bias_s)
        adjusted[section] = new_block
    return adjusted


# ---------------------------------------------------------------------------
# Fetchers (network, graceful degradation)
# ---------------------------------------------------------------------------


def fetch_marine(lat: float, lon: float, days: int) -> dict[str, Any]:
    """Fetch wave/swell forecast and sea surface temperature from Open-Meteo Marine API."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "wave_height,wave_direction,wave_period,"
            "swell_wave_height,swell_wave_direction,swell_wave_period,"
            "wind_wave_height,sea_surface_temperature"
        ),
        "daily": (
            "wave_height_max,wave_direction_dominant,wave_period_max,"
            "swell_wave_height_max,swell_wave_direction_dominant,swell_wave_period_max"
        ),
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(MARINE_URL, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {
            "error": str(e),
            "note": "Marine forecast unavailable. Check https://www.windy.com/-Waves-waves or Surfline manually.",
        }


def fetch_wind_weather(lat: float, lon: float, days: int) -> dict[str, Any]:
    """Fetch wind (hourly) and general weather (daily) from Open-Meteo Forecast API."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,uv_index_max"
        ),
        "wind_speed_unit": "ms",
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(FORECAST_URL, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {
            "error": str(e),
            "note": "Wind/weather forecast unavailable. Check https://www.windy.com manually.",
        }


def fetch_tides_noaa(lat: float, lon: float, days: int) -> dict[str, Any]:
    """Find nearest NOAA CO-OPS tide station and fetch high/low predictions.

    US coastal waters only - outside NOAA coverage this returns an error
    entry so the tide ladder can fall through to WorldTides or the gap note.
    """
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(NOAA_STATIONS_URL, params={"type": "tidepredictions"})
            response.raise_for_status()
            stations = response.json().get("stations", [])
    except Exception as e:
        return {
            "error": str(e),
            "note": "NOAA station lookup failed. Check https://www.tide-forecast.com manually.",
        }

    if not stations:
        return {"error": "No stations returned", "note": "Check https://www.tide-forecast.com manually."}

    nearest = min(
        stations,
        key=lambda s: haversine_km(lat, lon, float(s["lat"]), float(s["lng"])),
    )
    distance_km = haversine_km(lat, lon, float(nearest["lat"]), float(nearest["lng"]))

    if distance_km > MAX_TIDE_STATION_KM:
        return {
            "error": f"Nearest NOAA station ({nearest['name']}) is {round(distance_km)} km away",
            "note": TIDE_FALLBACK_NOTE,
        }

    result = _fetch_tide_predictions(nearest["id"], days)
    if "error" in result:
        return result
    result["station"].update(
        {
            "name": nearest["name"],
            "state": nearest.get("state", ""),
            "distance_km": round(distance_km, 1),
        }
    )
    return result


def _fetch_tide_predictions(station_id: str, days: int) -> dict[str, Any]:
    """Fetch high/low tide predictions for a NOAA station, heights in meters (SI)."""
    begin = datetime.now().strftime("%Y%m%d")
    params = {
        "product": "predictions",
        "application": "claude-surfing-skills",
        "begin_date": begin,
        "range": days * 24,
        "datum": "MLLW",
        "station": station_id,
        "time_zone": "lst_ldt",
        "units": "metric",
        "interval": "hilo",
        "format": "json",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(NOAA_PREDICTIONS_URL, params=params)
            response.raise_for_status()
            predictions = response.json().get("predictions", [])
    except Exception as e:
        return {
            "error": str(e),
            "note": f"Tide predictions failed for station {station_id}. Check https://tidesandcurrents.noaa.gov manually.",
        }

    events: dict[str, list[dict[str, Any]]] = {}
    for p in predictions:
        # p: {"t": "2026-07-08 04:12", "v": "1.655", "type": "H"}
        date_key, time_part = p["t"].split(" ")
        events.setdefault(date_key, []).append(
            {
                "time": time_part,
                "height_m": float(p["v"]),
                "type": "high" if p["type"] == "H" else "low",
            }
        )

    return {
        "source": "NOAA CO-OPS",
        "station": {
            "id": station_id,
            "url": f"https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id={station_id}",
        },
        "datum": "MLLW",
        "days": [{"date": d, "events": evs} for d, evs in sorted(events.items())],
    }


def parse_worldtides_extremes(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse a WorldTides extremes response into the tides contract shape (SI).

    Event `date` strings carry the spot's local time ("2026-07-10T04:12+0200",
    from the `localtime` request param); heights are meters on the requested
    datum, which the API echoes back in `responseDatum`.
    """
    events: dict[str, list[dict[str, Any]]] = {}
    for extreme in payload.get("extremes", []):
        date_key, time_part = extreme["date"].split("T")
        events.setdefault(date_key, []).append(
            {
                "time": time_part[:5],
                "height_m": float(extreme["height"]),
                "type": extreme["type"].lower(),
            }
        )

    result: dict[str, Any] = {
        "source": "WorldTides",
        "datum": payload.get("responseDatum", WORLDTIDES_DATUM),
        "days": [{"date": d, "events": evs} for d, evs in sorted(events.items())],
    }
    if payload.get("station"):
        result["station"] = {"name": payload["station"], "url": "https://www.worldtides.info/"}
    if payload.get("copyright"):
        result["copyright"] = payload["copyright"]
    return result


def _redact(text: str, key: str) -> str:
    """Scrub the API key from error text; httpx exceptions embed the request
    URL, query string and all, and WorldTides error strings may echo the key.
    The URL form is percent-encoded, so scrub that spelling too."""
    if not key:
        return text
    for form in (key, urllib.parse.quote(key, safe="")):
        text = text.replace(form, "***")
    return text


def fetch_tides_worldtides(lat: float, lon: float, days: int, key: str) -> dict[str, Any]:
    """Tide extremes from WorldTides on chart datum (ADR 0001), heights in meters.

    The key is passed in by the caller (environment only) and must never
    appear in the returned payload.
    """
    params = {
        "extremes": "",
        "date": "today",
        "days": days,
        "datum": WORLDTIDES_DATUM,
        "localtime": "",
        "lat": lat,
        "lon": lon,
        "key": key,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(WORLDTIDES_URL, params=params)
            payload = response.json()
    except Exception as e:
        return {
            "error": _redact(f"WorldTides request failed: {e}", key),
            "note": WORLDTIDES_MANUAL_NOTE,
        }

    if payload.get("status") != 200 or "extremes" not in payload:
        api_error = payload.get("error", "no extremes in response")
        return {
            "error": _redact(f"WorldTides returned no extremes: {api_error}", key),
            "note": WORLDTIDES_MANUAL_NOTE,
        }
    return parse_worldtides_extremes(payload)


def fetch_tides(lat: float, lon: float, days: int, tz_name: str = "UTC") -> dict[str, Any]:
    """Tide source ladder: NOAA CO-OPS where it has a nearby station, then
    WorldTides when WORLDTIDES_KEY is set, then the free offline EOT20 harmonic
    model when its files are installed, else the manual-fallback note.

    WorldTides ranks above EOT20 deliberately: a user who set a key opted into
    paid station-grade extremes on a chart datum, which read closer to the
    printed local tables than EOT20's mean-sea-level harmonic prediction. Without
    a key, EOT20 is the free keyless fallback that covers the whole globe (ADR 0004).
    """
    noaa = fetch_tides_noaa(lat, lon, days)
    if "error" not in noaa:
        return noaa
    errors = [f"NOAA CO-OPS: {noaa['error']}"]

    key = os.environ.get("WORLDTIDES_KEY")
    if key:
        worldtides = fetch_tides_worldtides(lat, lon, days, key)
        if "error" not in worldtides:
            return worldtides
        errors.append(f"WorldTides: {worldtides['error']}")

    harmonic = fetch_tides_harmonic(lat, lon, days, tz_name)
    if "error" not in harmonic:
        return harmonic
    errors.append(f"EOT20: {harmonic['error']}")

    return {
        "error": "; ".join(errors),
        "note": harmonic.get("note", EOT20_SETUP_NOTE),
    }


def find_tide_extremes(
    times: list[datetime], heights_m: list[float]
) -> list[dict[str, Any]]:
    """Reduce a densely-sampled tide elevation series to its high/low turning points.

    `times` are local datetimes in ascending order and `heights_m` the predicted
    elevation in metres at each. Returns one event per local extremum, in order:
    ``{"date": "YYYY-MM-DD", "time": "HH:MM", "height_m": float, "type": "high"|"low"}``,
    detected where the slope changes sign. A turning point needs a neighbour on
    each side, so the first and last samples are never reported; sample finely
    enough (every ~10 min) that no extreme is missed. Flats are handled by
    requiring a strict change on one side, so a plateau yields a single event.
    """
    events: list[dict[str, Any]] = []
    for i in range(1, len(heights_m) - 1):
        prev, cur, nxt = heights_m[i - 1], heights_m[i], heights_m[i + 1]
        if cur >= prev and cur > nxt:
            kind = "high"
        elif cur <= prev and cur < nxt:
            kind = "low"
        else:
            continue
        when = times[i]
        events.append(
            {
                "date": when.strftime("%Y-%m-%d"),
                "time": when.strftime("%H:%M"),
                "height_m": round(float(cur), 2),
                "type": kind,
            }
        )
    return events


def _predict_tide_series(
    lat: float, lon: float, local_times: list[datetime], model_dir: str
) -> list[float]:
    """Predict EOT20 tide elevation (metres about MSL) at one point for a list of
    timezone-aware local datetimes. This is the only pyTMD/numpy-touching step,
    isolated so the orchestration around it stays testable without the optional
    scientific stack or the 2 GB model files installed (tests monkeypatch this).

    `extrapolate=True`: surf spots sit on the coast (Plentzia is a rivermouth),
    which the 1/8 deg grid may mask; extrapolation snaps to the nearest ocean
    cell rather than returning NaN, trading a little accuracy for a usable curve.
    """
    import numpy as np
    import pyTMD

    times_utc = np.array(
        [t.astimezone(timezone.utc).replace(tzinfo=None) for t in local_times],
        dtype="datetime64[s]",
    )
    # EOT20 uses lon_wrap=180, so normalise to -180..180.
    lon_wrapped = ((float(lon) + 180.0) % 360.0) - 180.0
    darr = pyTMD.compute.tide_elevations(
        lon_wrapped,
        float(lat),
        times_utc,
        directory=model_dir,
        model="EOT20",
        crs="4326",
        standard="datetime",
        method="linear",
        extrapolate=True,
        cutoff=25.0,
        infer_minor=True,
    )
    return [float(v) for v in np.asarray(darr.values, dtype="float64").ravel()]


def fetch_tides_harmonic(
    lat: float, lon: float, days: int, tz_name: str = "UTC"
) -> dict[str, Any]:
    """Predict tides from the free EOT20 global harmonic model, computed locally
    (no key, no network). Heights are metres about mean sea level (datum "MSL").

    Requires the optional `pyTMD` dependency and the EOT20 constituent files under
    EOT20_DIR (see download_tide_model.py). Absent either, or if the point falls
    off the model grid, returns the standard gap shape with a setup note - never
    raises, per the tool contract.
    """
    model_dir = os.environ.get(EOT20_DIR_ENV) or DEFAULT_EOT20_DIR
    if not os.path.isdir(os.path.join(model_dir, "EOT20", "ocean_tides")):
        return {"error": f"EOT20 model files not found in {model_dir}", "note": EOT20_SETUP_NOTE}

    try:
        tz = ZoneInfo(tz_name)
        start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(minutes=EOT20_SAMPLE_MINUTES)
        n = int(days * 24 * 60 / EOT20_SAMPLE_MINUTES)
        local_times = [start_local + step * k for k in range(n + 1)]

        heights = list(_predict_tide_series(lat, lon, local_times, model_dir))
        if not heights or all(math.isnan(h) for h in heights):
            return {
                "error": "EOT20 returned no data for this point (off-grid)",
                "note": WORLDTIDES_MANUAL_NOTE,
            }

        extremes = find_tide_extremes(local_times, heights)
    except ImportError as e:
        return {"error": f"optional tide support not installed ({e})", "note": EOT20_SETUP_NOTE}
    except Exception as e:  # never hard-fail: degrade to the manual note
        return {"error": f"EOT20 prediction failed: {e}", "note": WORLDTIDES_MANUAL_NOTE}

    if not extremes:
        return {"error": "EOT20 produced no tide extremes", "note": WORLDTIDES_MANUAL_NOTE}

    days_out: dict[str, list[dict[str, Any]]] = {}
    for e in extremes:
        days_out.setdefault(e["date"], []).append(
            {"time": e["time"], "height_m": e["height_m"], "type": e["type"]}
        )
    return {
        "source": EOT20_SOURCE,
        "datum": EOT20_DATUM,
        "days": [{"date": d, "events": evs} for d, evs in sorted(days_out.items())],
    }


def parse_ndbc_realtime(text: str) -> dict[str, Any] | None:
    """Parse the latest observation from an NDBC realtime2 text file.

    File format: two header lines (#YY MM DD hh mm WDIR WSPD ... / units),
    then newest-first data rows. 'MM' marks missing values. Returns the first
    row that has a wave height reading, or None.
    """
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return None
    header = lines[0].lstrip("#").split()
    idx = {name: i for i, name in enumerate(header)}

    def field(row: list[str], name: str) -> float | None:
        i = idx.get(name)
        if i is None or i >= len(row):
            return None
        value = row[i]
        return None if value == "MM" else float(value)

    for line in lines[2:]:
        row = line.split()
        wvht = field(row, "WVHT")
        if wvht is None:
            continue
        observed_at = f"{row[0]}-{row[1]}-{row[2]} {row[3]}:{row[4]} UTC"
        return {
            "observed_at": observed_at,
            "wave_height_m": wvht,
            "dominant_period_s": field(row, "DPD"),
            "mean_wave_direction": compass(field(row, "MWD")),
            "wind_ms": field(row, "WSPD"),
            "wind_direction": compass(field(row, "WDIR")),
            "water_temp_c": field(row, "WTMP"),
        }
    return None


def parse_portus_lastdata(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Parse a PORTUS lastData response into an SI observation.

    Each entry in `datos` has `nombreColumna` (sometimes null), a string
    `valor`, and a `factor` to divide by (e.g. tp valor "859", factor 100
    -> 8.59 s). Coastal buoys report fewer sensors than deep-water ones, so
    any column except significant wave height (hm0) may be absent.
    Returns None when there is no wave observation.
    """
    if not payload:
        return None

    values: dict[str, float] = {}
    for entry in payload.get("datos", []):
        column = entry.get("nombreColumna")
        if not column:
            continue
        try:
            factor = float(entry.get("factor") or 1.0)
            values[column] = float(entry["valor"]) / (factor or 1.0)
        except (TypeError, ValueError, KeyError):
            continue

    if "hm0" not in values:
        return None

    # fecha is UTC, e.g. "2026-07-10 16:00:00.0"
    observed_at = None
    fecha = payload.get("fecha")
    if fecha:
        observed_at = f"{fecha[:16]} UTC"

    return {
        "observed_at": observed_at,
        "wave_height_m": values["hm0"],
        "dominant_period_s": values.get("tp"),
        "mean_wave_direction": compass(values.get("dmd")),
        "wind_ms": values.get("vv_md"),
        "wind_direction": compass(values.get("dv_md")),
        "water_temp_c": values.get("ts2"),
    }


def nearest_portus_station(
    stations: list[dict[str, Any]], lat: float, lon: float
) -> tuple[dict[str, Any], float] | None:
    """Nearest available PORTUS wave station within MAX_BUOY_KM, or None.

    Selection is by distance and `disponible` only: other metadata (incidencia,
    maxFechaAna) proved unreliable in live probes - beached-flagged stations
    still report. Staleness surfaces at parse time instead.
    """
    candidates = []
    for station in stations:
        if not station.get("disponible"):
            continue
        st_lat, st_lon = station.get("latitud"), station.get("longitud")
        if st_lat is None or st_lon is None:
            continue
        distance = haversine_km(lat, lon, st_lat, st_lon)
        if distance <= MAX_BUOY_KM:
            candidates.append((distance, station))
    if not candidates:
        return None
    distance, station = min(candidates, key=lambda c: c[0])
    return station, distance


PORTUS_MANUAL_NOTE = "Check https://portus.puertos.es manually."


def _fetch_portus_station(station_id: str, station_meta: dict[str, Any]) -> dict[str, Any]:
    """Latest observation from one known PORTUS station (one lastData request).

    `station_meta` carries display fields (`name`, `distance_km`) from the
    station list or a spot profile's pinned buoy entry.
    """
    try:
        with httpx.Client(timeout=30.0, headers=PORTUS_HEADERS) as client:
            response = client.post(
                PORTUS_LASTDATA_URL.format(station_id=station_id),
                params={"locale": "es"},
                json=PORTUS_CATEGORIES,
            )
            response.raise_for_status()
            observation = parse_portus_lastdata(response.json())
    except Exception as e:
        return {
            "error": f"Puertos del Estado observation failed for station {station_id}: {e}",
            "note": PORTUS_MANUAL_NOTE,
        }

    if observation is None:
        return {
            "error": f"Puertos del Estado station {station_id} ({station_meta.get('name', '')}) has no current wave data",
            "note": PORTUS_MANUAL_NOTE,
        }

    return {
        "station": {
            "id": str(station_id),
            "name": station_meta.get("name", ""),
            "distance_km": station_meta.get("distance_km"),
            "url": PORTUS_PORTAL_URL,
        },
        **observation,
    }


def fetch_buoy_portus(lat: float, lon: float) -> dict[str, Any]:
    """Latest observation from the nearest Puertos del Estado wave buoy.

    Polite polling per ADR 0002: one station-list GET plus exactly one
    lastData request per run - if the nearest station has no usable data,
    degrade rather than try the next one.
    """
    try:
        with httpx.Client(timeout=30.0, headers=PORTUS_HEADERS) as client:
            response = client.get(PORTUS_STATIONS_URL, params={"locale": "es"})
            response.raise_for_status()
            stations = response.json()
    except Exception as e:
        return {
            "error": f"Puertos del Estado station lookup failed: {e}",
            "note": PORTUS_MANUAL_NOTE,
        }

    nearest = nearest_portus_station(stations, lat, lon)
    if nearest is None:
        return {
            "error": f"No Puertos del Estado wave buoy within {int(MAX_BUOY_KM)} km",
            "note": f"No nearby observed-wave data from this network. {PORTUS_MANUAL_NOTE}",
        }
    station, distance = nearest
    return _fetch_portus_station(
        str(station["id"]),
        {"name": station.get("nombre", ""), "distance_km": round(distance, 1)},
    )


def fetch_buoy_ndbc(lat: float, lon: float) -> dict[str, Any]:
    """Find the nearest NDBC buoys and return the latest real observation.

    Observed data is the ground truth the model forecast gets cross-checked
    against. Tries the nearest few wave-reporting stations within MAX_BUOY_KM.
    """
    try:
        import xml.etree.ElementTree as ET

        with httpx.Client(timeout=30.0) as client:
            response = client.get(NDBC_STATIONS_URL)
            response.raise_for_status()
            root = ET.fromstring(response.text)
    except Exception as e:
        return {
            "error": str(e),
            "note": "NDBC station lookup failed. Check https://www.ndbc.noaa.gov manually.",
        }

    candidates = []
    for st in root.iter("station"):
        try:
            st_lat, st_lon = float(st.get("lat")), float(st.get("lon"))
        except (TypeError, ValueError):
            continue
        distance = haversine_km(lat, lon, st_lat, st_lon)
        if distance <= MAX_BUOY_KM:
            candidates.append((distance, st.get("id"), st.get("name", "")))
    candidates.sort()

    if not candidates:
        return {
            "error": f"No NDBC buoy within {int(MAX_BUOY_KM)} km",
            "note": "No nearby observed-wave data. Rely on model forecast; check local buoy networks manually.",
        }

    with httpx.Client(timeout=30.0) as client:
        for distance, station_id, name in candidates[:5]:
            try:
                response = client.get(NDBC_REALTIME_URL.format(station_id=station_id))
                if response.status_code != 200:
                    continue
                observation = parse_ndbc_realtime(response.text)
            except Exception:
                continue
            if observation:
                return {
                    "station": {
                        "id": station_id,
                        "name": name,
                        "distance_km": round(distance, 1),
                        "url": f"https://www.ndbc.noaa.gov/station_page.php?station={station_id}",
                    },
                    **observation,
                }

    return {
        "error": "No nearby NDBC buoy is currently reporting wave data",
        "note": "Rely on model forecast; check https://www.ndbc.noaa.gov for station status.",
    }


def _covers_spain(lat: float, lon: float) -> bool:
    """Spanish coasts served by Puertos del Estado buoys.

    Boxes hug the Spanish coastline so runs in Portugal or France do not
    poll the rate-limited PORTUS API for stations that cannot be within
    range (ADR 0002 politeness). Border fringes (northern Portugal, the
    French Basque corner) stay inside deliberately: Spanish buoys sit
    within MAX_BUOY_KM of those breaks.
    """
    north_coast = 41.5 <= lat <= 44.2 and -9.6 <= lon <= -1.6
    med_and_south = 35.9 <= lat <= 42.7 and -7.6 <= lon <= 4.5
    canarias = 27.0 <= lat <= 29.8 and -18.5 <= lon <= -13.0
    return north_coast or med_and_south or canarias


# Region-keyed buoy source registry. Networks are tried in order for any
# region covering the spot; the first one that returns an observation wins.
# Every fetcher returns the same SI observation shape, so a later network
# slots in with one entry and no contract change. `fetch` wraps the function
# in a lambda so the module-level name is resolved at call time (tests
# monkeypatch the fetchers).
BUOY_NETWORKS: list[dict[str, Any]] = [
    {
        "name": "Puertos del Estado",
        "covers": _covers_spain,
        "fetch": lambda lat, lon: fetch_buoy_portus(lat, lon),
        "manual_url": "https://portus.puertos.es",
    },
    {
        "name": "NOAA NDBC",
        "covers": lambda lat, lon: True,
        "fetch": lambda lat, lon: fetch_buoy_ndbc(lat, lon),
        "manual_url": "https://www.ndbc.noaa.gov",
    },
]


def fetch_buoy(lat: float, lon: float) -> dict[str, Any]:
    """Latest buoy observation from the first covering network in the registry."""
    errors = []
    urls = []
    for network in BUOY_NETWORKS:
        if not network["covers"](lat, lon):
            continue
        result = network["fetch"](lat, lon)
        if "error" not in result:
            return result
        errors.append(f"{network['name']}: {result['error']}")
        urls.append(network["manual_url"])
    return {
        "error": "; ".join(errors) or "No buoy network covers this location",
        "note": "No nearby observed-wave data. Rely on the model forecast; check "
        + " or ".join(urls or ["https://www.ndbc.noaa.gov"])
        + " manually.",
    }


def _fetch_ndbc_station(station_id: str, station_meta: dict[str, Any]) -> dict[str, Any]:
    """Latest observation from one known NDBC station (no station-list download)."""
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(NDBC_REALTIME_URL.format(station_id=station_id))
            response.raise_for_status()
            observation = parse_ndbc_realtime(response.text)
    except Exception as e:
        return {
            "error": f"NDBC station {station_id} fetch failed: {e}",
            "note": "Check https://www.ndbc.noaa.gov manually.",
        }

    if observation is None:
        return {
            "error": f"NDBC station {station_id} is not reporting wave data",
            "note": "Check https://www.ndbc.noaa.gov for station status.",
        }

    return {
        "station": {
            "id": str(station_id),
            "name": station_meta.get("name", ""),
            "distance_km": station_meta.get("distance_km"),
            "url": f"https://www.ndbc.noaa.gov/station_page.php?station={station_id}",
        },
        **observation,
    }


# Single-station fetchers per network, used for a spot profile's pinned buoy.
# `network` values match the registry names in BUOY_NETWORKS. Lambdas resolve
# the module-level names at call time, same as BUOY_NETWORKS (tests
# monkeypatch the fetchers).
PINNED_BUOY_FETCHERS: dict[str, Any] = {
    "NOAA NDBC": lambda station_id, meta: _fetch_ndbc_station(station_id, meta),
    "Puertos del Estado": lambda station_id, meta: _fetch_portus_station(station_id, meta),
}


def fetch_buoy_pinned(buoy_cfg: dict[str, Any]) -> dict[str, Any]:
    """Observation from the spot profile's pinned buoy, skipping the
    nearest-station lookup. Returns the standard error shape on any failure;
    the caller falls back to the registry lookup and reports a gap.
    """
    station_id = buoy_cfg.get("station_id")
    if station_id is None:
        return {
            "error": "Spot profile buoy entry has no station_id",
            "note": "Fix the profile's buoy block or remove it to use the nearest-station lookup.",
        }
    network = buoy_cfg.get("network")
    fetcher = PINNED_BUOY_FETCHERS.get(network)
    if fetcher is None:
        return {
            "error": f"Unknown buoy network in spot profile: {network!r} (known: {', '.join(PINNED_BUOY_FETCHERS)})",
            "note": "Fix the profile's buoy.network or remove the buoy block to use the nearest-station lookup.",
        }
    return fetcher(str(station_id), buoy_cfg)


def fetch_daylight(lat: float, lon: float, tz_name: str, days: int) -> dict[str, Any]:
    """Compute sunrise/sunset/twilight per day using astral."""
    try:
        from astral import LocationInfo
        from astral.sun import sun

        tz = ZoneInfo(tz_name)
        location = LocationInfo(latitude=lat, longitude=lon)
        out = []
        today = datetime.now(tz).date()
        for i in range(days):
            date = today + timedelta(days=i)
            try:
                s = sun(location.observer, date=date, tzinfo=tz)
                daylight_h = (s["sunset"] - s["sunrise"]).total_seconds() / 3600
                out.append(
                    {
                        "date": date.isoformat(),
                        "first_light": s["dawn"].strftime("%H:%M"),
                        "sunrise": s["sunrise"].strftime("%H:%M"),
                        "sunset": s["sunset"].strftime("%H:%M"),
                        "last_light": s["dusk"].strftime("%H:%M"),
                        "daylight_hours": round(daylight_h, 1),
                    }
                )
            except ValueError:
                out.append({"date": date.isoformat(), "error": "sun does not cross threshold at this latitude"})
        return {"timezone": tz_name, "days": out}
    except Exception as e:
        return {
            "error": str(e),
            "note": "Daylight calculation failed. Check https://sunrise-sunset.org manually.",
        }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _hourly_lookup(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Map ISO hour timestamp -> value for an hourly series."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    values = hourly.get(key, [])
    return dict(zip(times, values))


def build_marine_days(
    marine_raw: dict[str, Any],
    wind_raw: dict[str, Any],
    facing: float | None,
    units: str,
) -> list[dict[str, Any]]:
    """Condense hourly marine + wind data into 3-hour blocks per day (05:00-21:00).

    All computation (wind classification, quality rating) runs on SI values;
    `units` only controls the numbers written into the output blocks.
    """
    if "error" in marine_raw:
        return []

    wave_ht = _hourly_lookup(marine_raw, "wave_height")
    swell_ht = _hourly_lookup(marine_raw, "swell_wave_height")
    swell_dir = _hourly_lookup(marine_raw, "swell_wave_direction")
    swell_per = _hourly_lookup(marine_raw, "swell_wave_period")
    wind_wave = _hourly_lookup(marine_raw, "wind_wave_height")
    wind_speed = _hourly_lookup(wind_raw, "wind_speed_10m") if "error" not in wind_raw else {}
    wind_dir = _hourly_lookup(wind_raw, "wind_direction_10m") if "error" not in wind_raw else {}
    wind_gust = _hourly_lookup(wind_raw, "wind_gusts_10m") if "error" not in wind_raw else {}

    daily = marine_raw.get("daily", {})
    days = []
    for i, date_str in enumerate(daily.get("time", [])):
        blocks = []
        for hour in BLOCK_GRID_HOURS:
            ts = f"{date_str}T{hour:02d}:00"
            if ts not in wave_ht:
                continue
            spd_ms = wind_speed.get(ts)
            wdir = wind_dir.get(ts)
            wtype = classify_wind(wdir, facing, spd_ms) if facing is not None else None
            s_ht_m = swell_ht.get(ts)
            s_per = swell_per.get(ts)
            block: dict[str, Any] = {
                "time": f"{hour:02d}:00",
                "wave_height": height_out(wave_ht.get(ts), units),
                "swell_height": height_out(s_ht_m, units),
                "swell_period_s": s_per,
                "swell_direction": compass(swell_dir.get(ts)),
                "swell_direction_deg": swell_dir.get(ts),
                "wind_wave_height": height_out(wind_wave.get(ts), units),
                "wind_speed": wind_out(spd_ms, units),
                "wind_gust": wind_out(wind_gust.get(ts), units),
                "wind_direction": compass(wdir),
                "wind_type": wtype,
            }
            if facing is not None:
                block["quality"] = rate_block(s_ht_m, s_per, spd_ms, wtype)
            blocks.append(block)

        # Full 1-hour resolution for the day, used by the tide chart's aligned
        # hourly strip. Leaner than blocks (only the strip's four metrics plus
        # the direction degrees the renderer needs to draw wind/swell arrows).
        hours = []
        for hour in range(24):
            ts = f"{date_str}T{hour:02d}:00"
            if ts not in wave_ht:
                continue
            spd_ms = wind_speed.get(ts)
            wdir = wind_dir.get(ts)
            wtype = classify_wind(wdir, facing, spd_ms) if facing is not None else None
            s_ht_m = swell_ht.get(ts)
            s_per = swell_per.get(ts)
            entry: dict[str, Any] = {
                "time": f"{hour:02d}:00",
                "swell_height": height_out(s_ht_m, units),
                "swell_period_s": s_per,
                "swell_direction": compass(swell_dir.get(ts)),
                "swell_direction_deg": swell_dir.get(ts),
                "wind_speed": wind_out(spd_ms, units),
                "wind_direction": compass(wdir),
                "wind_direction_deg": wdir,
                "wind_type": wtype,
            }
            if facing is not None:
                entry["quality"] = rate_block(s_ht_m, s_per, spd_ms, wtype)
            hours.append(entry)

        def _get(key: str) -> Any:
            values = daily.get(key, [])
            return values[i] if i < len(values) else None

        days.append(
            {
                "date": date_str,
                "summary": {
                    "wave_height_max": height_out(_get("wave_height_max"), units),
                    "swell_height_max": height_out(_get("swell_wave_height_max"), units),
                    "swell_period_max_s": _get("swell_wave_period_max"),
                    "swell_direction_dominant": compass(_get("swell_wave_direction_dominant")),
                },
                "blocks": blocks,
                "hours": hours,
            }
        )
    return days


def _block_end(time_str: str) -> str:
    """End of a 3-hour forecast block, clamped to the same day."""
    return f"{min(int(time_str[:2]) + 3, 23):02d}{time_str[2:]}"


def build_surf_windows(
    marine_days: list[dict[str, Any]], daylight: dict[str, Any], units: str
) -> list[dict[str, Any]]:
    """Pick the best-rated surfable-light block per day. Requires --facing (quality present).

    A block qualifies if any part of it overlaps first light..last light, and
    the reported best_time is clamped to first light so it never lands in the
    dark (the 05:00 block IS the dawn patrol window when first light is 05:24).
    """
    first_by_date = {}
    last_by_date = {}
    for d in daylight.get("days", []):
        if "first_light" in d:
            first_by_date[d["date"]] = d["first_light"]
            last_by_date[d["date"]] = d["last_light"]

    windows = []
    for day in marine_days:
        date = day["date"]
        first_light = first_by_date.get(date, "06:00")
        last_light = last_by_date.get(date, "20:00")
        candidates = [
            b for b in day["blocks"]
            if b.get("quality") and b["time"] < last_light and _block_end(b["time"]) > first_light
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda b: b["quality"]["score"])
        wind_label = UNIT_LABELS[units]["wind_speed"]
        windows.append(
            {
                "date": date,
                "best_time": max(best["time"], first_light),
                "rating": best["quality"]["rating"],
                "score": best["quality"]["score"],
                "swell_height": best["swell_height"],
                "swell_period_s": best["swell_period_s"],
                "swell_direction": best["swell_direction"],
                "wind": f"{best['wind_speed']} {wind_label} {best['wind_direction']} ({best['wind_type']})"
                if best.get("wind_speed") is not None
                else None,
            }
        )
    return windows


def build_sea_temperature(marine_raw: dict[str, Any], buoy: dict[str, Any], units: str) -> dict[str, Any]:
    """Current water temperature + wetsuit recommendation.

    Prefers the buoy's observed water temp over the model SST when both exist
    (they can straddle a wetsuit-thickness boundary); reports both so the
    report can cite its source.
    """
    model_c = None
    if "error" not in marine_raw:
        sst = _hourly_lookup(marine_raw, "sea_surface_temperature")
        values = [v for v in sst.values() if v is not None]
        if values:
            model_c = values[0]

    buoy_c = buoy.get("water_temp_c") if "error" not in buoy else None

    current_c = buoy_c if buoy_c is not None else model_c
    if current_c is None:
        return {
            "error": "No water temperature data at this location",
            "note": "Check Surfline or surf-forecast.com for water temp.",
        }
    return {
        "current": temp_out(current_c, units),
        "source": "buoy observation" if buoy_c is not None else "model SST",
        "model": temp_out(model_c, units),
        "buoy": temp_out(buoy_c, units),
        "wetsuit": wetsuit_for(current_c),
    }


def build_buoy(buoy: dict[str, Any], units: str) -> dict[str, Any]:
    """Convert the SI buoy observation to the requested output units."""
    if "error" in buoy:
        return buoy
    return {
        "station": buoy["station"],
        "observed_at": buoy["observed_at"],
        "wave_height": height_out(buoy["wave_height_m"], units),
        "dominant_period_s": buoy["dominant_period_s"],
        "mean_wave_direction": buoy["mean_wave_direction"],
        "wind_speed": wind_out(buoy["wind_ms"], units),
        "wind_direction": buoy["wind_direction"],
        "water_temp": temp_out(buoy["water_temp_c"], units),
    }


def build_tides(tides: dict[str, Any], units: str) -> dict[str, Any]:
    """Convert SI tide predictions to the requested output units."""
    if "error" in tides:
        return tides
    return {
        **tides,
        "days": [
            {
                "date": day["date"],
                "events": [
                    {
                        "time": e["time"],
                        "height": tide_height_out(e["height_m"], units),
                        "type": e["type"],
                    }
                    for e in day["events"]
                ],
            }
            for day in tides["days"]
        ],
    }


def build_weather(wind_raw: dict[str, Any], units: str) -> dict[str, Any]:
    """Daily air temp / precip / UV summary."""
    if "error" in wind_raw:
        return {"error": wind_raw["error"], "note": wind_raw.get("note", "")}
    daily = wind_raw.get("daily", {})
    days = []
    for i, date_str in enumerate(daily.get("time", [])):
        code = daily.get("weather_code", [None])[i]
        label, icon = WEATHER_CODES.get(code, ("Unknown", ""))
        days.append(
            {
                "date": date_str,
                "conditions": label,
                "icon": icon,
                "temp_max": temp_out(daily.get("temperature_2m_max", [None])[i], units),
                "temp_min": temp_out(daily.get("temperature_2m_min", [None])[i], units),
                "precip_probability_pct": daily.get("precipitation_probability_max", [None])[i],
                "uv_index_max": daily.get("uv_index_max", [None])[i],
            }
        )
    return {"days": days}


# ---------------------------------------------------------------------------
# Forecast archive (the verification loop's forecast side)
# ---------------------------------------------------------------------------


def build_archive_records(
    marine_days: list[dict[str, Any]],
    surf_windows: list[dict[str, Any]],
    units: dict[str, Any],
    spot_name: str,
    spot_slug: str,
    archived_on: str,
) -> list[dict[str, Any]]:
    """One append-only snapshot per forecast day.

    Each record carries the run date (`archived_on`), the forecast day it
    predicts (`date`), the lead time in days, the units in effect, and that
    day's swell/wave summary (in display units). When a surf window was computed
    for the day its best block is attached under `best_window`, so
    /surfing:verify can compare the model's call for a day against the session
    log for it. Returns [] when there is no forecast to snapshot.

    The caller passes the RAW (pre-bias) forecast: the verification loop judges
    the model's own prediction, so a stored bias must never fold back into what
    a later /surfing:verify compares against (that would un-learn the bias).
    """
    if not marine_days:
        return []
    windows = {w["date"]: w for w in (surf_windows or []) if w.get("date")}

    records = []
    for day in marine_days:
        forecast_date = day.get("date")
        if not forecast_date:
            continue
        summary = day.get("summary", {})
        try:
            lead_days = (date.fromisoformat(forecast_date) - date.fromisoformat(archived_on)).days
        except (ValueError, TypeError):
            lead_days = None
        record: dict[str, Any] = {
            "archived_on": archived_on,
            "spot": spot_name,
            "spot_slug": spot_slug,
            "date": forecast_date,
            "lead_days": lead_days,
            "units": units,
            "wave_height": summary.get("wave_height_max"),
            "swell_height": summary.get("swell_height_max"),
            "swell_period_s": summary.get("swell_period_max_s"),
            "swell_direction": summary.get("swell_direction_dominant"),
        }
        window = windows.get(forecast_date)
        if window:
            record["best_window"] = {
                "best_time": window.get("best_time"),
                "rating": window.get("rating"),
                "score": window.get("score"),
                "wind": window.get("wind"),
            }
        records.append(record)
    return records


def write_archive(directory: str, slug: str, records: list[dict[str, Any]]) -> str:
    """Append forecast snapshots as JSONL to `directory/<slug>.jsonl`, creating
    the directory if needed. Append-only machine data; returns the file path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{slug}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


@click.command()
@click.option(
    "--coordinates",
    default=None,
    help='Spot coordinates as "lat,lon" (in the water, near the break). Required unless --spot-file provides them.',
)
@click.option("--spot-name", default=None, help="Surf spot name. Required unless --spot-file provides it.")
@click.option(
    "--spot-file",
    default=None,
    help="Path to a spot profile (spots/<slug>.yaml in the surf folder). Supplies coordinates, "
    "name, facing, tide station, and pinned buoy; explicit flags override profile values.",
)
@click.option(
    "--surfer-file",
    default=None,
    help="Path to the surfer profile (surfer.yaml in the surf folder). Supplies the units preference "
    "(the --units flag wins).",
)
@click.option(
    "--facing",
    type=float,
    default=None,
    help="Direction the spot faces looking out to sea, degrees true (e.g. 270 = west-facing). "
    "Enables wind classification (on/off/cross-shore), per-block quality ratings, and surf_windows.",
)
@click.option("--days", type=int, default=7, help="Forecast days (1-7)")
@click.option("--tide-station", default=None, help="NOAA CO-OPS station ID override (skips nearest-station lookup)")
@click.option(
    "--archive",
    default=None,
    help="Directory (the surf folder's forecasts/) to append one JSONL forecast snapshot per day to, "
    "as forecasts/<spot-slug>.jsonl. The forecast side of the verification loop; append-only machine data.",
)
@click.option(
    "--units",
    type=click.Choice(["metric", "imperial"]),
    default=None,
    help="Output units: metric (heights m, wind km/h, temps °C) or imperial (ft, kn, °F). "
    "Precedence: this flag, then the surfer profile, then metric.",
)
@click.option(
    "--target-day",
    default=None,
    help="Target day (YYYY-MM-DD) the surfer intends to surf; keys the report filename. "
    "Defaults to the forecast window's first day.",
)
def cli(
    coordinates: str | None,
    spot_name: str | None,
    spot_file: str | None,
    surfer_file: str | None,
    facing: float | None,
    days: int,
    tide_station: str | None,
    archive: str | None,
    units: str | None,
    target_day: str | None,
):
    """Fetch surf conditions for a spot and print unified JSON to stdout."""
    try:
        profile = load_yaml_mapping(spot_file, "spot profile") if spot_file else {}
        surfer = load_yaml_mapping(surfer_file, "surfer profile") if surfer_file else {}
        coordinates = coordinates or profile_coordinates(profile)
        if coordinates is None:
            raise ValueError("--coordinates is required (directly or via a --spot-file with coordinates)")
        lat, lon = parse_coordinates(coordinates)
        spot_name = spot_name or profile.get("name")
        if not spot_name:
            raise ValueError("--spot-name is required (directly or via a --spot-file with a name)")
        if facing is None and profile.get("facing_deg") is not None:
            facing = float(profile["facing_deg"])
        tide_station = tide_station or profile.get("tide_station")
        units = units or surfer_units(surfer) or "metric"
        bias_cfg = parse_model_bias(profile) if spot_file else None
        if target_day is not None:
            date.fromisoformat(target_day)
    except ValueError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)

    days = max(1, min(days, 7))
    gaps: list[str] = []

    # marine_raw stays the model's raw forecast (the archive snapshots it); the
    # report and verdicts run on report_marine, which folds in any stored bias.
    marine_raw = fetch_marine(lat, lon, days)
    if bias_cfg:
        report_marine = apply_swell_bias(marine_raw, bias_cfg["swell_height_m"], bias_cfg["swell_period_s"])
    else:
        report_marine = marine_raw
    wind_raw = fetch_wind_weather(lat, lon, days)

    buoy_cfg = profile.get("buoy")
    if isinstance(buoy_cfg, dict):
        buoy = fetch_buoy_pinned(buoy_cfg)
        if "error" in buoy:
            gaps.append(
                f"buoy: pinned station {buoy_cfg.get('station_id')} failed ({buoy['error']}); "
                "fell back to the nearest-station lookup"
            )
            buoy = fetch_buoy(lat, lon)
    else:
        buoy = fetch_buoy(lat, lon)

    # Resolve the spot timezone first: the EOT20 tide rung needs it to report
    # highs/lows in local time (NOAA/WorldTides already return local times).
    tz_name = report_marine.get("timezone") or wind_raw.get("timezone") or "UTC"

    if tide_station:
        tides = _fetch_tide_predictions(str(tide_station), days)
    else:
        tides = fetch_tides(lat, lon, days, tz_name)

    daylight = fetch_daylight(lat, lon, tz_name, days)

    marine_days = build_marine_days(report_marine, wind_raw, facing, units)

    # Target date for the report filename: explicit target day, else the
    # forecast window's first day - never the run date. With no window and no
    # --target-day it stays null and the gap is reported instead.
    window_start = None
    if marine_days:
        window_start = marine_days[0]["date"]
    elif daylight.get("days"):
        window_start = daylight["days"][0].get("date")
    target_date = target_day or window_start
    spot_slug = slugify(spot_name)

    spot: dict[str, Any] = {
        "name": spot_name,
        "coordinates": [lat, lon],
        "facing_deg": facing,
        "facing_compass": compass(facing) if facing is not None else None,
        "timezone": tz_name,
    }
    if spot_file:
        spot["profile"] = {
            "path": spot_file,
            **profile_age(profile.get("last_researched"), date.today()),
        }

    result: dict[str, Any] = {
        "spot": spot,
        "units": {"system": units, **UNIT_LABELS[units]},
        "report": {
            "directory": "reports",
            "target_date": target_date,
            "spot_slug": spot_slug,
            "filenames": {
                verdict: f"reports/{report_filename(target_date, spot_name, verdict)}"
                for verdict in VERDICT_SLUGS
            }
            if target_date
            else None,
        },
        "marine": {"days": marine_days} if marine_days else report_marine,
        "buoy": build_buoy(buoy, units),
        "tides": build_tides(tides, units),
        "sea_temperature": build_sea_temperature(report_marine, buoy, units),
        "daylight": daylight,
        "weather": build_weather(wind_raw, units),
    }

    if facing is not None and marine_days:
        result["surf_windows"] = build_surf_windows(marine_days, daylight, units)
    elif facing is None:
        gaps.append("surf_windows and wind classification not computed - pass --facing (degrees the spot faces out to sea)")

    # Applied model bias echo: heights in the payload's display units (unit-neutral
    # keys, per the units object), period always in seconds. The stored offset is
    # metric; height_out converts it to the units in effect for a labelled note.
    if bias_cfg:
        result["bias"] = {
            "applied": bool(bias_cfg["swell_height_m"] or bias_cfg["swell_period_s"]),
            "swell_height": height_out(bias_cfg["swell_height_m"], units),
            "swell_period_s": bias_cfg["swell_period_s"],
            "samples": bias_cfg["samples"],
            "last_verified": bias_cfg["last_verified"],
            "note": bias_cfg["note"],
            "source": spot_file,
        }

    if archive:
        # Snapshot the RAW model forecast, never the bias-corrected view: verify
        # judges the model's own prediction, so re-deriving bias from a corrected
        # archive would converge to zero and un-learn the correction.
        if bias_cfg:
            raw_days = build_marine_days(marine_raw, wind_raw, facing, units)
            raw_windows = (
                build_surf_windows(raw_days, daylight, units) if facing is not None and raw_days else []
            )
        else:
            raw_days = marine_days
            raw_windows = result.get("surf_windows", [])
        records = build_archive_records(
            raw_days, raw_windows, result["units"], spot_name, spot_slug, date.today().isoformat()
        )
        if not records:
            gaps.append("archive: no forecast days to snapshot")
        else:
            try:
                path = write_archive(archive, spot_slug, records)
                result["archive"] = {"path": path, "appended": len(records)}
            except OSError as e:
                gaps.append(f"archive: could not append forecast snapshot ({e})")

    if target_date is None:
        gaps.append(
            "report: target date unknown (no forecast window and no --target-day) - "
            "name the report by the intended surf day, never the run date"
        )

    for key in ("marine", "buoy", "tides", "sea_temperature", "daylight", "weather"):
        section = result.get(key)
        if isinstance(section, dict) and "error" in section:
            gaps.append(f"{key}: {section['error']}")

    result["gaps"] = gaps
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
