# Claude Surfing Skills

This repo is a Claude Code plugin: the `spot-researcher` skill researches surf spots and generates Markdown surf reports.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (via the `gh` CLI); external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical label names (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), no remapping. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` at the repo root plus `docs/adr/`, created lazily by `/domain-modeling`. See `docs/agents/domain.md`.

## Test Command

```bash
cd skills/spot-researcher/tools
uv run pytest -v
```

## Tool Contract

`fetch_conditions.py` (and any tool it grows a sibling to) must never hard-fail on a network or API error. On failure it exits 0 and returns a JSON object with an `error` field and a `note` pointing to a manual fallback (for example, tide-forecast.com when NOAA CO-OPS has no station nearby). Exit 1 is reserved for invalid CLI arguments.

## Keeping the Data Contract in Sync

`skills/spot-researcher/SKILL.md` documents the exact JSON keys `fetch_conditions.py` returns (`units`, `report`, `marine`, `buoy`, `tides`, `sea_temperature`, `daylight`, `weather`, `surf_windows`, `gaps`), and the report template in `skills/spot-researcher/assets/report-template.md` references those same fields via placeholders. JSON keys are unit-neutral (no `_ft`/`_f`/`_kn` suffixes); the payload's `units` object states the units in effect. Any change to the script's output keys must be reflected in both places in the same change.

`skills/spot-researcher/tools/render_report.py` is a second consumer of the `fetch_conditions.py` payload, plus the data package's `analysis` block (`analysis.target_day`, `analysis.week`; documented in SKILL.md Step 5A). A change to either the script's output keys or the `analysis` schema must be reflected everywhere all four stay in sync: `fetch_conditions.py`, `SKILL.md`, the report template, and `render_report.py`.

`skills/spot-researcher/tools/build_package.py` is a deterministic producer of the same data package (draft `analysis` from the payload's `surf_windows`, `spot_data` mapped from the spot profile YAML; driven by `commands/dashboard.md`). Any change to the `fetch_conditions.py` output keys, the Step 5A `analysis` schema, the Step 3C `spot_data` shape, or the spot profile YAML schema must update `build_package.py` and its tests as well.

`render_report.py` also consumes the week data package (`--mode week`; schema documented in SKILL.md next to Step 6C, produced by `commands/week.md`). Any change to that schema must update `render_report.py`, its tests, `SKILL.md`, and `commands/week.md` together.

The verification loop adds two more shared contracts. The forecast archive JSONL that `fetch_conditions.py --archive` writes (`forecasts/<slug>.jsonl`, `build_archive_records`) is read by `verify_forecast.py`; a change to that snapshot shape must update both scripts and their tests together. The spot profile's `model_bias` block is written by `/surfing:verify` (via `verify_forecast.py`'s output) and read by `fetch_conditions.py --spot-file` (`parse_model_bias`); a change to it must update `assets/spot-profile-template.yaml`, `fetch_conditions.py`, `verify_forecast.py`, `commands/verify.md`, and the tests together.

Carve-out to the four-way sync rule above: the `bias` and `archive` payload keys are command-lane auxiliary metadata, not report-body content, so they are exempt from the report-template and `render_report.py` legs of the rule (they still must stay documented in `SKILL.md` and `tools/README.md`). `bias` is applied to the swell numbers before the report ever sees them and surfaces through the commands' reasoning and the display-ready `swell`/`why` strings the renderer already shows; `archive` is a write-side echo (`path`, `appended`) with nothing to render. Any report-body-visible output key stays bound by the full four-way rule.
