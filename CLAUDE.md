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
