# Roadmap: surf companion features (pre-grilling draft)

> Status: SUPERSEDED (2026-07-10). Grilling is done; the committed spec is
> [PRD #1](https://github.com/EHernandez-dev/claude-surfing-skills/issues/1) with
> sub-issues #2-#10, plus CONTEXT.md and docs/adr/. Kept for historical context only.

The v0.1.0 skill produces one-shot markdown research reports. These six increments turn it
into a surf companion: visual reports, multi-spot planning, proactive alerts, and a spot
library that learns. Each increment is shippable, committed, and e2e-tested before the next.

## Increment 0: metric units (prereq, from FUTURE-IMPROVEMENTS)

- `fetch_conditions.py`: add `--units metric|imperial` (default metric). Keep SI internally,
  convert at the edge. Unit-neutral JSON keys plus a `units` echo in the payload
  (e.g. `wave_height` + `units: {height: "m", speed: "kn", temp: "C"}`).
- Update together (CLAUDE.md sync rule): `test_fetch_conditions.py`, SKILL.md contract
  section, report-template placeholders, tools/README.
- Fold in report naming (FUTURE-IMPROVEMENTS item 10): more informative filenames.
- Evidence: the Basque e2e reports mix feet (swell), meters (tides), and Fahrenheit.

## Increment 1: spot library + surfer profile (foundation for everything below)

- New convention `spots/<slug>.yaml` in the user's working directory: name, region,
  coordinates, facing_deg, works-on profile (swell window, size min/max, min period,
  ideal wind, ideal tide), tide source, buoy id, webcams, hazard one-liners, `last_researched`.
- SKILL.md Phase 4 writes/updates the spot YAML after analysis. `conditions` and `windows`
  commands use it when present: skip re-research, produce spot-corrected verdicts.
- New `surfer.yaml` (example in `assets/`): skill level, boards, home spots, units,
  target-day defaults. Codifies the personalization both e2e reports improvised.
- `fetch_conditions.py --spot-file spots/<slug>.yaml` loads coordinates/facing/tide-station.

## Increment 2: HTML visual report

- New `skills/spot-researcher/tools/render_report.py`: deterministic Python (no LLM) that
  takes the run's data-package JSON and emits a self-contained `{date}-{slug}.html`:
  hero verdict banner, tide curve SVG with session windows shaded (cosine interpolation
  between high/low events; pure, unit-testable), per-day swell/wind bars (inline SVG),
  Leaflet + OSM map centered on the spot, webcam cards, hazards summary.
- SKILL.md Phase 5 addition: run renderer after the markdown report, then `open` the HTML.
- Design question for `/prototype`: what the page should look like.

## Increment 3: surf week planner

- New command `/surfing:week`: read `surfer.yaml` home spots (or args), run
  `fetch_conditions.py --spot-file ...` per spot in parallel, apply cached profile
  corrections, output one ranked dashboard (best windows this week across spots)
  plus a multi-spot HTML variant (`render_report.py --mode week`).

## Increment 4: briefings and swell alerts

- New command `/surfing:briefing`: compact tomorrow-morning call for home spots
  (week-planner machinery, 1-day horizon).
- Alert variant: output only when triggered (forecast swell >= spot minimum size AND
  period >= spot minimum AND offshore/light window inside next 5 days).
- New `docs/AUTOMATION.md`: scheduling options (Claude Code routines or cron +
  `claude -p "/surfing:briefing"`) and notification paths.

## Increment 5: forecast verification loop

- `fetch_conditions.py --archive`: append per-day snapshots to `spots/<slug>/forecast-log.jsonl`.
- Session logs saved as `sessions/<date>-<slug>.md` (existing template).
- New command `/surfing:verify <spot>`: compare session logs against archived forecasts,
  compute per-spot model bias, store it in the spot YAML; conditions/week/briefing apply it.

## Deliberately out of scope here (tracked in FUTURE-IMPROVEMENTS.md)

European buoy/tide API integration (feeds Increments 0-1), Surfline fetching ladder,
Claude Desktop / ChatGPT variants, README rework.
