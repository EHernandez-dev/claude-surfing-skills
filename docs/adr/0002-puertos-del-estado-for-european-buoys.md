# Puertos del Estado for European buoy observations, Spain-only this wave

Both Basque e2e runs (2026-07-08) had zero observed wave data because NDBC is US-only. We integrate Puertos del Estado's PORTUS JSON API (keyless, verified live 2026-07-10; see docs/research/2026-07-10-surf-data-sources.md) into the buoy lane, structured as a small region-keyed source registry so other networks (CANDHIS, UK CCO) can slot in later without another contract change. Scope for this wave is Spain only: that is where the evidence and the user's home spots are, and each additional network is an unresearched API.

## Consequences

- The PORTUS API is undocumented and rate-limited (community reports of 429s and one IP-block warning for abusive polling). The existing graceful-degradation contract (exit 0, `error` + `note` fields) covers outages; polling stays at one request per spot per run.
