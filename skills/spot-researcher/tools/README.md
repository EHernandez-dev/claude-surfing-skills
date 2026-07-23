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

- `--coordinates` (required unless `--spot-file` provides them): Lat/lon as `"lat,lon"`, a point in the water near the break, not the town center
- `--spot-name` (required unless `--spot-file` provides it): Surf spot name
- `--spot-file` (optional): Path to a spot profile (`spots/<slug>.yaml` in the surf folder, schema: `../assets/spot-profile-template.yaml`). Supplies coordinates, name, facing, tide station, and a pinned buoy (fetched directly, skipping the nearest-station lookup; on failure the registry lookup takes over and a gap is reported). Explicit flags override profile values. A missing or invalid file is an argument error (exit 1)
- `--surfer-file` (optional): Path to the surfer profile (`surfer.yaml` in the surf folder, example: `../assets/surfer-template.yaml`). Supplies the units preference
- `--facing` (optional): Direction the spot faces looking out to sea, degrees true (e.g. 270 = west-facing). Enables `wind_type` (offshore/onshore/cross-shore/light), per-block `quality` ratings, and `surf_windows`
- `--days` (optional): Forecast days, 1-7 (default: 7)
- `--units` (optional): `metric` (heights m, wind km/h, temps °C) or `imperial` (heights ft, wind kn, temps °F). Precedence: this flag, then the surfer profile, then metric. All quantities are SI internally; conversion happens only at the output edge
- `--target-day` (optional): The day (YYYY-MM-DD) the surfer intends to surf; keys `report.target_date`. Defaults to the forecast window's first day, never the run date
- `--tide-station` (optional): NOAA CO-OPS station ID override, skips the nearest-station lookup
- `--archive` (optional): Directory (the surf folder's `forecasts/`) to append one JSONL forecast snapshot per day to, as `forecasts/<slug>.jsonl`. Append-only machine data, the forecast side of the verification loop (`verify_forecast.py` / `/surfing:verify`). When `--spot-file` carries a `model_bias`, the snapshot records the already bias-corrected numbers

**Environment:**

- `WORLDTIDES_KEY` (optional): enables WorldTides tide extremes for spots outside NOAA coverage, heights on chart datum (ADR 0001). Read from the environment only; never appears in the output, including error messages
- `EOT20_DIR` (optional): directory holding the free offline EOT20 tide model (must contain `EOT20/ocean_tides/`). Defaults to `~/.cache/claude-surfing-skills/tide_models`. When present (and the optional `pyTMD` dependency is installed), tides are predicted from EOT20 for any coastal spot with no key and no network (ADR 0004; setup below)

**Output:**

Returns unified JSON with these keys. All keys are unit-neutral; the `units` object states the units in effect:

- `spot`: echo of inputs plus `facing_compass` and `timezone`; when `--spot-file` was passed, also `profile` (`path`, `last_researched`, `age_days`, `reresearch_suggested` - true past ~6 months (183 days); profiles never expire, the flag only prompts a re-research suggestion)
- `units`: `system` ("metric"/"imperial") plus display labels `wave_height`, `tide_height`, `wind_speed`, `temperature`
- `report`: report naming inputs, `directory` ("reports"), `target_date` (target day, falling back to the forecast window's first day, never the run date; null when neither is known), `spot_slug`, `filenames` (exact report path per verdict slug: `go`/`check`/`skip`, following `reports/{target-date}-{spot-slug}-{verdict}.md`)
- `marine.days[]`: per-day forecast, each with `summary` (`wave_height_max`, `swell_height_max`, `swell_period_max_s`, `swell_direction_dominant`), `blocks[]` (3-hourly, 05:00-21:00 local) containing `wave_height`, `swell_height`, `swell_period_s`, `swell_direction`(+`_deg`), `wind_wave_height`, `wind_speed`, `wind_gust`, `wind_direction`, `wind_type`, and `quality` (`score` 0-10 + `rating`), and `hours[]` (full 1-hour resolution, one entry per model hour) carrying `time`, `swell_height`, `swell_period_s`, `swell_direction`(+`_deg`), `wind_speed`, `wind_direction`(+`_deg`), `wind_type`, and `quality` (the target day's `hours` feed the tide chart's aligned hourly strip)
- `buoy`: nearest buoy real observation from the regional network registry (NOAA NDBC in the US, Puertos del Estado on Spanish coasts), `station` (id, name, distance_km, url), `observed_at`, `wave_height`, `dominant_period_s`, `mean_wave_direction`, `wind_speed`, `wind_direction`, `water_temp`. Coastal stations may report height/period only (null direction/wind/temp). This is observed ground truth, cross-check the model forecast against it
- `tides`: high/low predictions from a source ladder, `source` ("NOAA CO-OPS" where a station is within range, else "WorldTides" when `WORLDTIDES_KEY` is set, else "EOT20 (harmonic model)" when the keyless EOT20 model is installed), `datum` ("MLLW" for NOAA, "CD" chart datum for WorldTides, "MSL" mean sea level for EOT20), `days[]` with high/low `events[]` (`time`, `height`, `type`), `station` (NOAA: id/name/distance_km/url; WorldTides: name + url when a named station backs the prediction; none for EOT20), plus `copyright` on WorldTides responses. EOT20 heights are about mean sea level, not a chart datum (timing reliable, absolute heights differ from printed tables; less exact in estuaries). Only when none of the three is available does it return an `error` plus a fallback note
- `sea_temperature`: `current`, `source` (prefers "buoy observation" over "model SST" when both exist), `model`, `buoy`, and a deterministic `wetsuit` recommendation
- `daylight`: per-day `first_light`, `sunrise`, `sunset`, `last_light`, `daylight_hours`
- `weather`: per-day `conditions`, `icon`, `temp_max`/`temp_min`, `precip_probability_pct`, `uv_index_max`
- `surf_windows`: best-rated surfable-light block per day (`best_time` is clamped to first light so it never lands in the dark), only present when `--facing` was provided
- `bias`: only when `--spot-file` carries a `model_bias`; the applied per-spot correction from `/surfing:verify` (`applied`, `swell_height` in display units, `swell_period_s`, `samples`, `last_verified`, `note`, `source`). The offset is folded into the marine heights/periods, quality, and surf windows before output
- `archive`: only when `--archive` snapshotted a forecast; `path` to `forecasts/<slug>.jsonl` and the `appended` line count
- `gaps`: any API failures or skipped computations

**Data Sources:**

- Open-Meteo Marine API (wave/swell height, period, direction, sea surface temperature)
- Open-Meteo Forecast API (wind, air temp, precipitation, UV index)
- NOAA CO-OPS (tide predictions, US stations only)
- WorldTides (tide extremes elsewhere, chart datum, behind the optional `WORLDTIDES_KEY`; ADR 0001)
- EOT20 global harmonic tide model (free, keyless, offline; ADR 0004): predicted locally via the optional `pyTMD` dependency from a one-time ~2 GB model download. The keyless fallback below WorldTides that gives worldwide coastal tide coverage; heights are about mean sea level. Unavailable (not installed / point off-grid) degrades to a tide-forecast.com fallback note
- Buoy network registry (nearest buoy real observations): NOAA NDBC everywhere it reaches, Puertos del Estado PORTUS (keyless, undocumented; ADR 0002) for Spanish coasts. Networks are tried in registry order for regions that cover the spot; adding a network is one registry entry, no JSON contract change. PORTUS polling is polite: one observation request per spot per run
- astral (sunrise/sunset/twilight)

**Optional: free offline tides (EOT20):**

Outside NOAA coverage and without a `WORLDTIDES_KEY`, tides can be predicted for free from the EOT20 global harmonic model (CC-BY 4.0), computed locally with no API key and no network (ADR 0004). One-time setup:

```bash
# 1. install the optional scientific dependency (pyTMD + numpy/scipy/pyproj/xarray/...)
uv sync --extra tides
# 2. download the ~2 GB EOT20 model into the cache dir (or --dir / EOT20_DIR)
uv run --extra tides python download_tide_model.py
```

After that, the tide ladder uses EOT20 automatically for any coastal spot the higher rungs miss. Heights are relative to mean sea level, so highs/lows read at the right *times* but at different absolute heights than a printed chart-datum table; accuracy is lower in estuaries and rivermouths. Without this setup the tool behaves exactly as before (NOAA / WorldTides / gap note), so the base install stays lightweight.

**Testing:**

```bash
uv run pytest -v

# Integration tests hit live APIs
RUN_INTEGRATION_TESTS=1 uv run pytest -v
```

---

### build_package.py

Assembles the draft dashboard data package (SKILL.md Step 5A shape) deterministically from a `fetch_conditions.py` payload plus the spot profile YAML. Pure transform, no network. Driven by `/surfing:dashboard`: fast mode renders the draft as-is, normal mode edits only its judgment layer. Draft verdicts are true Verdicts (ADR 0007): quality-rating bands (epic/good = go, fair = check, poor/flat = skip) corrected against the machine-readable works-on fields; period below `min_period_s` is a hard skip, swell direction outside a +/-45 degree arc around the profile's compass token demotes one step. The prose works-on fields are never consulted, so the draft `one_liner` ends with the "Computed call, no analyst pass." tag.

**Usage:**

```bash
uv run python build_package.py \
  --payload "/abs/path/to/payload.json" \
  --spot-file "/abs/path/to/spots/mundaka.yaml" \
  --output "/abs/path/to/package.json"
```

**Parameters:**

- `--payload` (required): Path to the fetch payload JSON (the `fetch_conditions.py` stdout, saved to a file)
- `--spot-file` (optional): Path to the spot profile (`spots/<slug>.yaml`). Supplies the works-on corrections and the `spot_data` mapping; without it the draft has no `spot_data` and verdicts are rating-only
- `--surfer-file` (optional): Path to `surfer.yaml`; passed through as the package's `surfer_profile`
- `--target-day` (optional): The day (YYYY-MM-DD) the analysis keys to; defaults to the payload's `report.target_date`. `conditions.report` is rewritten to stay consistent when it differs
- `--output` (optional): Write the package JSON here and echo `{"package_path", "target_day", "verdict"}`; without it the full package prints to stdout

**Output:**

The complete render-ready package: `conditions` (payload verbatim), `analysis` (`target_day`, `week`, `windows`, all draft), `spot_data` (structured YAML fields mapped; the `notes` prose carried verbatim as `profile.description`; `community_notes: []`; prose-derived cards absent), `gaps` (copied from the payload), and `surfer_profile` when provided.

**Contract:** a data problem (missing/unreadable/malformed payload or YAML, a payload without `surf_windows` or forecast days, a target day outside the window) exits 0 with `{"error", "note"}` and writes no output file; exit 1 is reserved for invalid CLI arguments (malformed `--target-day`).

**Testing:**

```bash
uv run pytest -v test_build_package.py
```

---

### verify_forecast.py

The forecast verification arithmetic: compares a surfer's observed sessions against the archived forecast snapshots for the same days and reduces the differences to a per-spot model bias. Pure computation, no network. Driven by `/surfing:verify`, which extracts the observed numbers from the freeform session logs and writes the returned bias into the spot profile's `model_bias` block.

**Usage:**

```bash
uv run python verify_forecast.py \
  --forecast-log "/abs/path/to/forecasts/mundaka.jsonl" \
  --observations '[{"date":"2026-07-12","swell_height":1.4,"swell_period_s":13}]'
```

**Parameters:**

- `--forecast-log` (required): Path to the spot's forecast archive (`forecasts/<slug>.jsonl`, built by `fetch_conditions.py --archive`)
- `--observations` (required unless `--observations-file`): Observed sessions as a JSON array, `{date, swell_height, swell_period_s?}`, in the **same units as the forecast log**
- `--observations-file` (optional): Path to a JSON file with the observations array (alternative to `--observations`)
- `--spot-slug` (optional): Slug echoed in the result; defaults to the log's `spot_slug`

**Output:**

JSON with `samples` (session/forecast pairs matched, freshest forecast per day), `bias` (`swell_height_m` in meters and `swell_period_s` in seconds, each `observed - forecast`, or null), a human `note` (e.g. "model under-calls size by ~0.3 m"), `unmatched_sessions`, per-session `matched` detail, and the log's `units`. Sign convention matches the profile: a positive bias means the model under-calls and the offset is added to future forecasts. Small biases (within ~0.1 m) are zeroed so noise does not manufacture a correction.

**Contract:** a data problem (unreadable log, no overlapping days) exits 0 with a result or an `error`/`note`; exit 1 is reserved for invalid CLI arguments (missing log or observations, malformed observations JSON).

**Testing:**

```bash
uv run pytest -v test_verify_forecast.py
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

Ensure you're in a directory where you have write permissions. Reports are created in a `reports/` folder inside your current working directory (named `{target-date}-{spot-slug}-{go|check|skip}.md`), not in the plugin installation directory.

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
- **pyyaml** - spot and surfer profile parsing (`--spot-file` / `--surfer-file`)

Dev dependencies: pytest
