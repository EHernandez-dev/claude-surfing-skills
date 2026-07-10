# Research: Surf data sources for European coverage

Researched 2026-07-10. Three questions from the surf-companion feature wave, answered against
primary sources (official docs and live endpoint probes run today from this machine).

## 1. Puertos del Estado buoy data: public JSON access

**Answer: Yes. The PORTUS portal's backend API is public JSON, keyless, and works today (verified live), but it is undocumented, rate-limited, and its legal notice governs reuse.**

- Base URL: `https://portus.puertos.es/portussvr/api` (hardcoded as the axios `baseURL` in the
  PORTUS web app bundle, `https://portus.puertos.es/js/app.398b9015.js`).
- The official developer-facing documentation ([widgets.pdf](https://portus.puertos.es/Portus/docs/widgets.pdf))
  only covers iframe/widget embedding, not the JSON API. The JSON endpoints below come from
  inspecting the app bundle and community clients, then verifying live.
- **Station discovery** (verified 2026-07-10, HTTP 200, plain GET, no auth):
  `GET /portussvr/api/estaciones/hist/WAVE?locale=es` returns 54 wave stations as JSON, each with
  `id`, `nombre`, `latitud`, `longitud`, `boya`, `disponible`, `cadencia` (minutes), and network
  (`red`). Bilbao-Vizcaya deep-water buoy is `id=2136` (`disponible: true`, `cadencia: 60`);
  the coastal Bilbao II buoy is `id=1103`.
- **Latest observations** (verified 2026-07-10, HTTP 200):
  `POST /portussvr/api/lastData/station/2136?locale=es` with JSON body
  `["WAVE","WIND","WATER_TEMP","AIR_TEMP","SEA_LEVEL","CURRENTS","SALINITY"]` and
  `Content-Type: application/json` returns `{"fecha": "2026-07-10 14:00:00.0", "datos": [...]}`
  where each entry has `nombreParametro`, `nombreColumna` (e.g. `tp`, `tm02`, `hmax`, `hm0`),
  `valor` (string), `factor` (divide `valor` by it; e.g. Tp `valor: "859"`, `factor: 100` → 8.59 s),
  and `unidad`. Timestamp appears to be UTC and was ~45 min old at probe time, consistent with the
  60-minute cadence.
- **Historical series**: `POST /portussvr/api/historicosSerialTime/estacion/WAVE/{id}?locale=es`
  with a `{graficos: [{parametro: "hm0", ...}], desde, hasta, variable: "WAVE"}` body (used by
  [vpedrosa/surf-forecast-history](https://github.com/vpedrosa/surf-forecast-history/blob/main/data/fetch-wave-data.ts)).
- Quirks observed while probing: a POST with an empty body `[]` returns `200 []`; a 500 came back
  once for a partial category list, and retrying with the full category list succeeded. A
  browser-like `User-Agent` is advisable. Community operators report intermittent `429`s with a
  roughly 30-60 s rate-limit window and one report of Puertos del Estado emailing an IP-block
  warning for abusive polling ([Bateas/MeteoMapGal ingestor](https://github.com/Bateas/MeteoMapGal/blob/main/ingestor/buoyFetcher.ts)),
  so poll politely (one request per spot per run is nowhere near the limit).
- No API key or registration for any of the above. Terms: use of the embedding widgets requires
  accepting the legal notice at `http://www.puertos.es/Advertencia_Legal.html` (stated in
  widgets.pdf); Puertos del Estado also publishes through Spain's open-data portal
  ([datos.gob.es](https://datos.gob.es/es/aplicaciones/puertos-del-estado)). The JSON API itself
  carries no stated terms; treat it as an undocumented public interface that can change without
  notice, and keep the graceful-degradation contract.

## 2. WorldTides vs Stormglass for non-US tides

**Answer: both cover Spain and both require an API key. WorldTides is far cheaper per prediction (credits, ~$0.0005/request prepaid); Stormglass has a usable free tier for development (10 requests/day) but jumps to €19-49/month beyond that.**

### WorldTides ([developer/pricing](https://www.worldtides.info/developer), [API docs](https://www.worldtides.info/apidocs))

- API: `GET https://www.worldtides.info/api/v3?extremes&date=...&lat=...&lon=...&key=<apiKey>`.
  Key passed as a query parameter; obtained by registering an account. New accounts get
  **100 free credits**.
- Credits, not rate limits: 1 credit buys 7 days of high/low extremes for one location; sampled
  30-minute heights cost 1 more credit; datums 1 more. Prepaid packs from $9.99 for 20,000 credits;
  monthly plans from $4.99/month for 20,000 credits/month.
- Extremes response: array of `{dt, date, height, type}` (height in meters relative to the response
  datum; MSL default, Chart Datum/LAT selectable). Response reports `station` when a real tide
  gauge backs the prediction and the `atlas`/model otherwise, so nearest-station distance is
  inspectable.
- Coverage: worldwide; blends tide-station data with a global background model, so Spanish coasts
  are covered even between gauges.
- Terms note: responses must be fetched per end-user request; caching is allowed only for the
  requesting user (fine for this plugin's one-shot report generation).

### Stormglass ([pricing](https://stormglass.io/pricing/), [tide docs](https://docs.stormglass.io/#/tide))

- API: `GET https://api.stormglass.io/v2/tide/extremes/point?lat=...&lng=...&start=...&end=...`
  with the key in an `Authorization` header. Datum selectable (MSL default; LAT/MLLW/etc.).
- Response: `data: [{time, height, type: "high"|"low"}]` plus `meta.station`
  `{name, distance (km), source}` — the docs' own sea-level example uses lat 43.38, lng -3.01
  (Sopelana, Spain) and resolves to a "bilbao" station 4 km away, so Spanish coverage is
  first-class. Tide constants derive from the CC-BY-4.0 TICON-3/GESLA-3 tide-gauge dataset.
- Pricing: free tier **10 requests/day, non-commercial**; Small €19/month for 500/day
  (still non-commercial); commercial use starts at Medium €49/month (5,000/day). Quota usage is
  returned in every response (`meta.cost`, `meta.dailyQuota`, `meta.requestCount`).

### Comparison for this repo

| | WorldTides | Stormglass |
|---|---|---|
| Free allowance | 100 credits one-time (~100 weeks of extremes) | 10 requests/day forever |
| Cheapest paid | $4.99/mo or $9.99 prepaid/20k credits | €19/mo (500/day) |
| Key handling | query param `key=` | `Authorization` header |
| Spain coverage | station + global model blend | station-based, Bilbao station confirmed |
| Commercial use | allowed on paid credits | only from €49/mo tier |

Neither is keyless, so either one breaks fetch_conditions.py's current zero-config property
(see Implications).

## 3. Surfline unauthenticated KBYG endpoints

**Answer: dead for programmatic use. Verified today: Cloudflare-blocked (HTTP 403 with a Cloudflare challenge page), matching both 2026-07-08 e2e runs. There is no official API, and Surfline's terms explicitly prohibit scraping.**

- Live probe 2026-07-10:
  `GET https://services.surfline.com/kbyg/spots/forecasts/wave?spotId=5842041f4e65fad6a7708801&days=3&intervalHours=3`
  with a browser User-Agent returned **HTTP 403**, `text/html`, containing a Cloudflare challenge
  script (`window.__CF$cv$params`). This is a JS-challenge wall, not an auth error; a plain HTTP
  client cannot pass it.
- Official position: Surfline's support article
  ["Does Surfline have a forecast API?"](https://support.surfline.com/hc/en-us/articles/13883685219227-Does-Surfline-have-a-forecast-API)
  says there is no public API (business inquiries via support@surfline.com), and the
  [Terms of Use](https://www.surfline.com/terms-of-use) prohibit "any robot, spider, scraper or
  other automated means to access the Services" and programmatic extraction generally.
- Third-party wrappers (Apify actors, parse.bot, the archived
  [surflinef](https://github.com/mhelmetag/surflinef) client) either scrape in violation of those
  terms or are stale. Not a foundation to build on.

## Implications for this repo

`fetch_conditions.py` is currently keyless end to end (Open-Meteo, NOAA CO-OPS, NDBC, astral).
Against that property:

- **Puertos del Estado is the win**: keyless JSON, fills the European buoy gap found in the Mundaka
  and La Salvaje e2e runs (Bilbao-Vizcaya buoy is the natural station for both), and slots into the
  existing `buoy` key alongside NDBC. Undocumented API, so keep the exit-0 + `error`/`note`
  degradation contract and a station-discovery fallback.
- **Tides**: no keyless station-grade source for Spain emerged. Options: (a) keep scraped/manual
  fallback as today, (b) add WorldTides or Stormglass behind an *optional* env-var key
  (`WORLDTIDES_KEY` / `STORMGLASS_KEY`), degrading to the current fallback note when unset. If one
  is chosen: WorldTides is cheaper and its per-user caching terms fit one-shot report generation;
  Stormglass's free 10/day is enough for personal use but its non-commercial clause binds anyone
  who installs the plugin at work.
- **Surfline**: drop from the roadmap as a data source; the gap it covered (human spot forecasts)
  stays manual.
