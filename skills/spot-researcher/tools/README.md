# Spot Researcher Tools

Python CLI tools for gathering current conditions data for surf spot research.

## Overview

These tools are invoked by the `spot-researcher` skill to fetch real-time data that supplements web-scraped spot information. Each tool outputs structured JSON to stdout for easy parsing.

**Design Philosophy:**

- Tools focus on **computation and API calls**, not web scraping
- All tools handle API/network errors gracefully (exit 0 with JSON error output)
- JSON output includes helpful fallback info when data is unavailable
- Timeout-friendly (30s default per request)

## Tools

### cloudscrape.py

Fetches HTML content from websites, with optional JS-rendering for Cloudflare-protected or JavaScript-heavy pages.

**Usage:**

```bash
# Default: fast httpx fetch (browser-like headers, no browser)
uv run python cloudscrape.py "https://www.wannasurf.com/spot/..."

# --render: Patchright headless browser for JS-rendered / Cloudflare-challenged pages
uv run python cloudscrape.py --render "https://www.surfline.com/surf-report/..."
```

**Parameters:**

- `url` (required): URL to fetch
- `--render` (optional): Use Patchright stealth browser for JS-rendered or Cloudflare-protected pages
- `--timeout` (optional): Request timeout in seconds (default: 30)

**Output:**

Returns the full HTML content to stdout. On failure, exits 0 with a JSON error note to stdout so callers always succeed.

**Behavior:**

- Default: plain httpx with browser-like headers; fast, no external install, no TLS spoofing
- `--render`: launches Patchright (undetected Playwright); Chromium is installed lazily on first use via `patchright install chromium`, base install stays light
- Any failure exits 0 (graceful degradation); error details go to stdout as JSON

**Dependencies:**

- httpx (default fetch path)
- patchright (stealth headless browser, `--render` path)
- click (CLI)

**Use Cases:**

- Surfline spot guide pages, which are JS-heavy and usually need `--render`
- Wannasurf and surf-forecast.com pages (default path usually sufficient)
- Some webcam listing pages that render content client-side

**Example:**

```bash
# Standard fetch, fast, no browser
uv run python cloudscrape.py "https://www.wannasurf.com/spot/..." | grep -i "swell"

# JS-rendered Surfline page
uv run python cloudscrape.py --render "https://www.surfline.com/surf-report/ocean-beach-overview/..."
```

---

### fetch_conditions.py

Unified surf conditions fetcher: swell/wind/tide forecasts, live buoy observations, water temperature, and daylight.

**Usage:**

```bash
uv run python fetch_conditions.py \
  --coordinates "37.759,-122.513" \
  --spot-name "Ocean Beach" \
  --facing 265 \
  --days 7
```

**Parameters:**

- `--coordinates` (required): Lat/lon as `"lat,lon"`, a point in the water near the break, not the town center
- `--spot-name` (required): Surf spot name
- `--facing` (optional): Direction the spot faces looking out to sea, degrees true (e.g. 270 = west-facing). Enables `wind_type` (offshore/onshore/cross-shore/light), per-block `quality` ratings, and `surf_windows`
- `--days` (optional): Forecast days, 1-7 (default: 7)
- `--tide-station` (optional): NOAA CO-OPS station ID override, skips the nearest-station lookup

**Output:**

Returns unified JSON with these keys:

- `spot`: echo of inputs plus `facing_compass` and `timezone`
- `marine.days[]`: per-day forecast, each with `summary` (max wave/swell height ft, max period, dominant direction) and `blocks[]` (3-hourly, 05:00-21:00 local) containing `wave_height_ft`, `swell_height_ft`, `swell_period_s`, `swell_direction`(+`_deg`), `wind_wave_height_ft`, `wind_kn`, `wind_gust_kn`, `wind_direction`, `wind_type`, and `quality` (`score` 0-10 + `rating`)
- `buoy`: nearest NDBC buoy real observation, `station` (id, name, distance_km, url), `observed_at`, `wave_height_ft`, `dominant_period_s`, `mean_wave_direction`, `water_temp_f`. This is observed ground truth, cross-check the model forecast against it
- `tides`: NOAA CO-OPS predictions, `station`, `datum` (MLLW), `days[]` with high/low `events[]`. **US only**, non-US spots return an `error` plus a fallback note
- `sea_temperature`: `current_f`, `current_c`, `source` (prefers "buoy observation" over "model SST" when both exist), `model_f`, `buoy_f`, and a deterministic `wetsuit` recommendation
- `daylight`: per-day `first_light`, `sunrise`, `sunset`, `last_light`, `daylight_hours`
- `weather`: per-day `conditions`, `icon`, `temp_max_f`/`temp_min_f`, `precip_probability_pct`, `uv_index_max`
- `surf_windows`: best-rated surfable-light block per day (`best_time` is clamped to first light so it never lands in the dark), only present when `--facing` was provided
- `gaps`: any API failures or skipped computations

**Data Sources:**

- Open-Meteo Marine API (wave/swell height, period, direction, sea surface temperature)
- Open-Meteo Forecast API (wind, air temp, precipitation, UV index)
- NOAA CO-OPS (tide predictions, US stations only; non-US spots should use tide-forecast.com, or a WorldTides/Stormglass API key manually)
- NOAA NDBC (nearest buoy real observations)
- astral (sunrise/sunset/twilight)

**Testing:**

```bash
uv run pytest -v

# Integration tests hit live APIs
RUN_INTEGRATION_TESTS=1 uv run pytest -v
```

---

## Installation

All tools are managed via `uv` with dependencies in `pyproject.toml`.

**Setup:**

```bash
cd skills/spot-researcher/tools
uv sync
```

This creates a virtual environment and installs all dependencies.

**Python Version:**

Python 3.11+ (specified in `.python-version`).

### Common Issues

**Dependencies not installing**

1. Check if `uv` is installed: `uv --version`
2. Try `uv sync --reinstall` in the tools directory
3. The skill will still work, just without some Python tools

**Cloudflare blocking requests**

- Retry with `--render` on `cloudscrape.py` to use the Patchright stealth browser
- First `--render` use installs Chromium lazily; subsequent calls are fast
- If both paths fail, the skill notes it in "Information Gaps" and continues

**No report generated**

Ensure you're in a directory where you have write permissions. Reports are created in your current working directory, not in the plugin installation directory.

## Development

### Error Handling Guidelines

All tools follow these principles:

1. **Never hard-fail on API errors** - exit 0 on network/API failures; exit 1 only on invalid arguments
2. **Always return JSON** - structured output for parsing
3. **Include helpful context** - URLs, notes, fallback suggestions
4. **Timeout gracefully** - 30s default per request
5. **Log to stderr** - use `click.echo(..., err=True)` for warnings

Any new data source added to `fetch_conditions.py` must include a manual-fallback link in its error output, matching the pattern already used for marine, tides, and buoy data.

Example error output:

```json
{
  "error": "Connection timeout",
  "note": "Check service.com manually for current data."
}
```

This ensures the skill can continue even if individual tools fail.

### Running Tests

```bash
cd skills/spot-researcher/tools
uv run pytest -v
```

## Performance

**Typical execution times:**

- `fetch_conditions.py`: 5-15s, five API round-trips (marine, wind/weather, tides, buoy, daylight); NOAA station lists and NDBC's active-stations list are each roughly 1MB, most of the latency is downloading and scanning those
- `cloudscrape.py`: 1-3s default httpx; 10-30s first `--render` (Chromium install), 3-8s subsequent `--render`

**Timeouts:**

- Individual tools: 30s per request
- Total skill execution: 3-5 minutes target

## Dependencies

Managed in `pyproject.toml`:

- **click** - CLI framework
- **httpx** - modern HTTP client
- **astral** - astronomy calculations (daylight)
- **patchright** - stealth headless browser (`--render` path in cloudscrape.py)

Dev dependencies: pytest
