#!/usr/bin/env python3
"""Unified surf conditions fetcher - swell, wind, tides, water temp, daylight.

Fetches all environmental/conditions data for a surf spot from various APIs.
Returns unified JSON matching the data contract for the spot-researcher skill.

Data sources:
- Open-Meteo Marine API: wave/swell height, period, direction, sea surface temp
- Open-Meteo Forecast API: wind, air temp, precipitation, UV index
- NOAA CO-OPS: tide predictions (US stations only)
- NOAA NDBC: nearest buoy observations (real observed waves + water temp)
- astral: sunrise/sunset/twilight
"""

import json
import math
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import click
import httpx

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NOAA_STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
NOAA_PREDICTIONS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NDBC_STATIONS_URL = "https://www.ndbc.noaa.gov/activestations.xml"
NDBC_REALTIME_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"

# Max distance to accept a NOAA tide station; beyond this the spot is
# probably outside the US and we report a gap instead of a wrong tide.
MAX_TIDE_STATION_KM = 80.0

# Max distance to accept an NDBC buoy for observed-conditions cross-check.
MAX_BUOY_KM = 150.0

TIDE_FALLBACK_NOTE = (
    "NOAA CO-OPS covers US coasts only. For non-US spots check "
    "https://www.tide-forecast.com or use a WorldTides/Stormglass API key manually."
)

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
    return round(meters * 3.28084, 1)


def c_to_f(celsius: float | None) -> float | None:
    if celsius is None:
        return None
    return round(celsius * 9 / 5 + 32, 1)


def classify_wind(wind_from_deg: float | None, facing_deg: float, speed_kn: float | None) -> str | None:
    """Classify wind relative to the shore.

    `facing_deg` is the direction the spot faces looking out to sea.
    Wind direction is meteorological (direction the wind blows FROM), so wind
    coming from the same direction the beach faces is onshore.
    Anything under 6 kn is 'light' regardless of direction (glassy-ish).
    """
    if wind_from_deg is None:
        return None
    if speed_kn is not None and speed_kn < 6:
        return "light"
    diff = abs((wind_from_deg - facing_deg + 180) % 360 - 180)
    if diff <= 45:
        return "onshore"
    if diff < 135:
        return "cross-shore"
    return "offshore"


def rate_block(
    swell_ht_ft: float | None,
    swell_period_s: float | None,
    wind_kn: float | None,
    wind_type: str | None,
) -> dict[str, Any] | None:
    """Heuristic surf quality score (0-10) for one forecast block.

    Rewards long-period swell in the rideable size band with light or
    offshore wind; punishes strong onshore wind. This is a generic
    heuristic - it does NOT know spot-specific swell windows, so the skill
    must cross-check against the spot's ideal conditions from research.
    """
    if swell_ht_ft is None:
        return None
    if swell_ht_ft < 1.0:
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

    # Size: 2-8 ft significant swell is the sweet spot for most breaks
    if 2 <= swell_ht_ft <= 8:
        score += 3
    elif 1 <= swell_ht_ft < 2 or 8 < swell_ht_ft <= 12:
        score += 2
    else:
        score += 1

    # Wind
    if wind_type == "light":
        score += 3
    elif wind_type == "offshore":
        score += 4 if (wind_kn or 0) <= 25 else 1
    elif wind_type == "cross-shore":
        score += 2 if (wind_kn or 0) < 12 else 1
    elif wind_type == "onshore":
        score += 1 if (wind_kn or 0) < 10 else 0

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


def wetsuit_for(water_temp_f: float | None) -> str | None:
    """Wetsuit recommendation from water temperature (deg F)."""
    if water_temp_f is None:
        return None
    if water_temp_f >= 75:
        return "Boardshorts / rash guard"
    if water_temp_f >= 70:
        return "1-2mm top or spring suit"
    if water_temp_f >= 65:
        return "2mm spring suit or 3/2 fullsuit"
    if water_temp_f >= 58:
        return "3/2 fullsuit"
    if water_temp_f >= 52:
        return "4/3 fullsuit + booties"
    if water_temp_f >= 43:
        return "5/4 hooded fullsuit + booties + gloves"
    return "6/5+ hooded fullsuit, booties, gloves (extreme cold)"


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
        "wind_speed_unit": "kn",
        "temperature_unit": "fahrenheit",
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


def fetch_tides(lat: float, lon: float, days: int) -> dict[str, Any]:
    """Find nearest NOAA CO-OPS tide station and fetch high/low predictions.

    US coastal waters only - outside NOAA coverage this returns an error
    entry so the skill can note the gap and link a manual source.
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

    begin = datetime.now().strftime("%Y%m%d")
    params = {
        "product": "predictions",
        "application": "claude-surfing-skills",
        "begin_date": begin,
        "range": days * 24,
        "datum": "MLLW",
        "station": nearest["id"],
        "time_zone": "lst_ldt",
        "units": "english",
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
            "note": f"Tide predictions failed for station {nearest['id']}. Check https://tidesandcurrents.noaa.gov manually.",
        }

    events: dict[str, list[dict[str, Any]]] = {}
    for p in predictions:
        # p: {"t": "2026-07-08 04:12", "v": "5.43", "type": "H"}
        date_key, time_part = p["t"].split(" ")
        events.setdefault(date_key, []).append(
            {
                "time": time_part,
                "height_ft": round(float(p["v"]), 1),
                "type": "high" if p["type"] == "H" else "low",
            }
        )

    return {
        "station": {
            "id": nearest["id"],
            "name": nearest["name"],
            "state": nearest.get("state", ""),
            "distance_km": round(distance_km, 1),
            "url": f"https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id={nearest['id']}",
        },
        "datum": "MLLW",
        "days": [{"date": d, "events": evs} for d, evs in sorted(events.items())],
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
        wtmp_c = field(row, "WTMP")
        return {
            "observed_at": observed_at,
            "wave_height_ft": m_to_ft(wvht),
            "dominant_period_s": field(row, "DPD"),
            "mean_wave_direction": compass(field(row, "MWD")),
            "wind_kn": round(field(row, "WSPD") * 1.94384) if field(row, "WSPD") is not None else None,
            "wind_direction": compass(field(row, "WDIR")),
            "water_temp_f": c_to_f(wtmp_c),
            "water_temp_c": wtmp_c,
        }
    return None


def fetch_buoy(lat: float, lon: float) -> dict[str, Any]:
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
        "error": "No nearby buoy is currently reporting wave data",
        "note": "Rely on model forecast; check https://www.ndbc.noaa.gov for station status.",
    }


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
) -> list[dict[str, Any]]:
    """Condense hourly marine + wind data into 3-hour blocks per day (05:00-21:00)."""
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
        for hour in range(5, 22, 3):
            ts = f"{date_str}T{hour:02d}:00"
            if ts not in wave_ht:
                continue
            spd = wind_speed.get(ts)
            wdir = wind_dir.get(ts)
            wtype = classify_wind(wdir, facing, spd) if facing is not None else None
            s_ht_ft = m_to_ft(swell_ht.get(ts))
            s_per = swell_per.get(ts)
            block: dict[str, Any] = {
                "time": f"{hour:02d}:00",
                "wave_height_ft": m_to_ft(wave_ht.get(ts)),
                "swell_height_ft": s_ht_ft,
                "swell_period_s": s_per,
                "swell_direction": compass(swell_dir.get(ts)),
                "swell_direction_deg": swell_dir.get(ts),
                "wind_wave_height_ft": m_to_ft(wind_wave.get(ts)),
                "wind_kn": round(spd) if spd is not None else None,
                "wind_gust_kn": round(wind_gust[ts]) if wind_gust.get(ts) is not None else None,
                "wind_direction": compass(wdir),
                "wind_type": wtype,
            }
            if facing is not None:
                block["quality"] = rate_block(s_ht_ft, s_per, spd, wtype)
            blocks.append(block)

        def _get(key: str) -> Any:
            values = daily.get(key, [])
            return values[i] if i < len(values) else None

        days.append(
            {
                "date": date_str,
                "summary": {
                    "wave_height_max_ft": m_to_ft(_get("wave_height_max")),
                    "swell_height_max_ft": m_to_ft(_get("swell_wave_height_max")),
                    "swell_period_max_s": _get("swell_wave_period_max"),
                    "swell_direction_dominant": compass(_get("swell_wave_direction_dominant")),
                },
                "blocks": blocks,
            }
        )
    return days


def _block_end(time_str: str) -> str:
    """End of a 3-hour forecast block, clamped to the same day."""
    return f"{min(int(time_str[:2]) + 3, 23):02d}{time_str[2:]}"


def build_surf_windows(marine_days: list[dict[str, Any]], daylight: dict[str, Any]) -> list[dict[str, Any]]:
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
        windows.append(
            {
                "date": date,
                "best_time": max(best["time"], first_light),
                "rating": best["quality"]["rating"],
                "score": best["quality"]["score"],
                "swell_height_ft": best["swell_height_ft"],
                "swell_period_s": best["swell_period_s"],
                "swell_direction": best["swell_direction"],
                "wind": f"{best['wind_kn']} kn {best['wind_direction']} ({best['wind_type']})"
                if best.get("wind_kn") is not None
                else None,
            }
        )
    return windows


def build_sea_temperature(marine_raw: dict[str, Any], buoy: dict[str, Any]) -> dict[str, Any]:
    """Current water temperature + wetsuit recommendation.

    Prefers the buoy's observed water temp over the model SST when both exist
    (they can straddle a wetsuit-thickness boundary); reports both so the
    report can cite its source.
    """
    model_f = None
    if "error" not in marine_raw:
        sst = _hourly_lookup(marine_raw, "sea_surface_temperature")
        values = [v for v in sst.values() if v is not None]
        if values:
            model_f = c_to_f(values[0])

    buoy_f = buoy.get("water_temp_f") if "error" not in buoy else None

    current_f = buoy_f if buoy_f is not None else model_f
    if current_f is None:
        return {
            "error": "No water temperature data at this location",
            "note": "Check Surfline or surf-forecast.com for water temp.",
        }
    return {
        "current_f": current_f,
        "current_c": round((current_f - 32) * 5 / 9, 1),
        "source": "buoy observation" if buoy_f is not None else "model SST",
        "model_f": model_f,
        "buoy_f": buoy_f,
        "wetsuit": wetsuit_for(current_f),
    }


def build_weather(wind_raw: dict[str, Any]) -> dict[str, Any]:
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
                "temp_max_f": daily.get("temperature_2m_max", [None])[i],
                "temp_min_f": daily.get("temperature_2m_min", [None])[i],
                "precip_probability_pct": daily.get("precipitation_probability_max", [None])[i],
                "uv_index_max": daily.get("uv_index_max", [None])[i],
            }
        )
    return {"days": days}


@click.command()
@click.option("--coordinates", required=True, help='Spot coordinates as "lat,lon" (in the water, near the break)')
@click.option("--spot-name", required=True, help="Surf spot name")
@click.option(
    "--facing",
    type=float,
    default=None,
    help="Direction the spot faces looking out to sea, degrees true (e.g. 270 = west-facing). "
    "Enables wind classification (on/off/cross-shore), per-block quality ratings, and surf_windows.",
)
@click.option("--days", type=int, default=7, help="Forecast days (1-7)")
@click.option("--tide-station", default=None, help="NOAA CO-OPS station ID override (skips nearest-station lookup)")
def cli(coordinates: str, spot_name: str, facing: float | None, days: int, tide_station: str | None):
    """Fetch surf conditions for a spot and print unified JSON to stdout."""
    try:
        lat, lon = parse_coordinates(coordinates)
    except ValueError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)

    days = max(1, min(days, 7))
    gaps: list[str] = []

    marine_raw = fetch_marine(lat, lon, days)
    wind_raw = fetch_wind_weather(lat, lon, days)
    buoy = fetch_buoy(lat, lon)

    if tide_station:
        tides = _fetch_tides_for_station(tide_station, days)
    else:
        tides = fetch_tides(lat, lon, days)

    tz_name = marine_raw.get("timezone") or wind_raw.get("timezone") or "UTC"
    daylight = fetch_daylight(lat, lon, tz_name, days)

    marine_days = build_marine_days(marine_raw, wind_raw, facing)
    result: dict[str, Any] = {
        "spot": {
            "name": spot_name,
            "coordinates": [lat, lon],
            "facing_deg": facing,
            "facing_compass": compass(facing) if facing is not None else None,
            "timezone": tz_name,
        },
        "marine": {"days": marine_days} if marine_days else marine_raw,
        "buoy": buoy,
        "tides": tides,
        "sea_temperature": build_sea_temperature(marine_raw, buoy),
        "daylight": daylight,
        "weather": build_weather(wind_raw),
    }

    if facing is not None and marine_days:
        result["surf_windows"] = build_surf_windows(marine_days, daylight)
    elif facing is None:
        gaps.append("surf_windows and wind classification not computed - pass --facing (degrees the spot faces out to sea)")

    for key in ("marine", "buoy", "tides", "sea_temperature", "daylight", "weather"):
        section = result.get(key)
        if isinstance(section, dict) and "error" in section:
            gaps.append(f"{key}: {section['error']}")

    result["gaps"] = gaps
    click.echo(json.dumps(result, indent=2))


def _fetch_tides_for_station(station_id: str, days: int) -> dict[str, Any]:
    """Fetch tide predictions for an explicit NOAA station ID."""
    begin = datetime.now().strftime("%Y%m%d")
    params = {
        "product": "predictions",
        "application": "claude-surfing-skills",
        "begin_date": begin,
        "range": days * 24,
        "datum": "MLLW",
        "station": station_id,
        "time_zone": "lst_ldt",
        "units": "english",
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
        date_key, time_part = p["t"].split(" ")
        events.setdefault(date_key, []).append(
            {
                "time": time_part,
                "height_ft": round(float(p["v"]), 1),
                "type": "high" if p["type"] == "H" else "low",
            }
        )
    return {
        "station": {
            "id": station_id,
            "url": f"https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id={station_id}",
        },
        "datum": "MLLW",
        "days": [{"date": d, "events": evs} for d, evs in sorted(events.items())],
    }


if __name__ == "__main__":
    cli()
