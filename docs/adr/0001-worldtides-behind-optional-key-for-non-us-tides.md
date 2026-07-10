# WorldTides behind an optional key for non-US tides, heights on chart datum

The v0.1.0 tide lane is NOAA CO-OPS only (US); at European spots the improvised scrape produced safety-relevant errors (interpolated high tide at Mundaka, hour-level day-boundary drift at Sopelana), and no keyless station-grade source for Europe exists. We add WorldTides as the non-US tide source behind an optional `WORLDTIDES_KEY` environment variable: NOAA stays primary where it has a station; when the key is unset, the script degrades to today's manual-fallback note (exit-0 contract and zero-config default unchanged). Heights are requested on chart datum (`datum=CD`) so they match published European tide tables, mirroring NOAA's MLLW; the payload echoes the datum.

## Considered Options

- **Stormglass** (alone or alongside WorldTides): rejected; ~100x more expensive per request, free tier is non-commercial (binds plugin users at work), and a second provider doubles the parse/test surface. Single-provider simplicity won.
- **Hardening the tide scraper**: rejected; keeps zero-config absolute but is fragile, terms-questionable, and is the path that already produced the bad Mundaka data.
- **MSL heights** (WorldTides default): rejected; numbers would match no published tide table.
