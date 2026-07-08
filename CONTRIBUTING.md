# Contributing

Pull requests are welcome.

## Running Tests

```bash
cd skills/spot-researcher/tools
uv run pytest
```

## Markdown Formatting

Keep Markdown consistent with the rest of the repo:

- Blank line before lists and after headers
- Use `-` for bullets

## Tool Contract

`fetch_conditions.py` must always degrade gracefully. If a data source is unavailable, it should exit 0 and return a JSON error entry with a `note`, never a non-zero exit for a network or API failure. Exit 1 is reserved for invalid arguments.

If you add a new data source, include a manual-fallback link in that source's error output, following the pattern already used for marine, tide, and buoy data (for example, pointing non-US tide lookups at tide-forecast.com).

If you change the JSON keys `fetch_conditions.py` outputs, update `skills/spot-researcher/SKILL.md`'s data-contract section and the report template placeholders to match.
