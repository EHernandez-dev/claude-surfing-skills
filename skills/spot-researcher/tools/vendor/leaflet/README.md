# Leaflet 1.9.4 (vendored)

License: BSD-2-Clause (see `LICENSE` in this directory).

Downloaded from unpkg (the npm dist build): `leaflet.js` and `leaflet.css` come from
`https://unpkg.com/leaflet@1.9.4/dist/`, unmodified. These files are inlined into
generated HTML by `render_report.py`, so the surf reports work without any
network access or CDN dependency at render time.

## Upgrade procedure

1. Replace `leaflet.js` and `leaflet.css` with the newer version's dist build files
   (same unpkg path, updated version number), and replace `LICENSE` if it changed.
2. Update any version references in this README and in `render_report.py`.
3. Re-run the test suite (`cd skills/spot-researcher/tools && uv run pytest -v`)
   to confirm the renderer still inlines and renders maps correctly.
