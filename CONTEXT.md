# Claude Surfing Skills

Domain language for the surf companion: researching surf spots, judging conditions, and planning sessions. One context for the whole plugin.

## Language

**Verdict**:
The per-day call for one spot: Go, Worth a check, or Skip. Spot-corrected: judged against the spot's works-on profile when one exists, and personalized by the surfer profile when present. Never presented as the raw quality score alone. Filename slugs: `-go`, `-check`, `-skip`.
_Avoid_: rating, go/no-go, caution

**Quality score**:
The spot-agnostic 0-10 heuristic for a forecast block, computed from period, size, and wind only. Input to a verdict, never shown as one.
_Avoid_: rating (as a user-facing term), stars

**Draft package**:
The render-ready data package assembled mechanically from the fetch payload and the spot profile, with no analyst pass: verdicts from quality-rating bands plus works-on demotions (swell direction, minimum period), formulaic display strings, windows clipped to daylight. A fast build renders it as-is; otherwise an analyst pass edits only its judgment layer (one-liners, why strings, verdict overrides against the prose works-on fields, window re-ranking).
_Avoid_: draft report, package draft

**Fast build**:
A Dashboard rendered straight from the draft package. Its verdicts are true (mechanically spot-corrected) Verdicts, but shallower than an analyst's: the prose works-on fields (size range, tide window, wind preference) are never consulted. Always marked in the hero line as a computed call with no analyst pass.
_Avoid_: quick mode, draft dashboard

**Works-on profile**:
The conditions under which a spot works: swell direction window, size range, minimum period, ideal wind, ideal tide.
_Avoid_: spot conditions, spot settings

**Spot profile**:
The persistent record of one spot (`spots/<slug>.yaml` in the surf folder): its works-on profile plus logistics (coordinates, facing, tide source, buoy, webcams, hazards) and `last_researched`. Never expires; its age is always shown when used, and past ~6 months reports suggest re-research.
_Avoid_: spot cache, spot config

**Surfer profile**:
The standing description of the surfer (`surfer.yaml`): skill level, boards, home spots, unit preference, target-day defaults. What turns a quality score into a personal verdict.
_Avoid_: user config, preferences file

**Surf folder**:
The working directory the plugin reads and writes: `surfer.yaml`, `spots/`, `reports/`, `sessions/`, `forecasts/`. No hidden state elsewhere.
_Avoid_: workspace, data dir

**Home spots**:
The spots listed in the surfer profile that the week planner and briefing sweep by default.
_Avoid_: favorites, my spots

**Community notes**:
The report section for recent first-hand accounts from any web-searchable surf community. Shows an explicit "no recent first-hand reports found" state when empty; an empty section means checked-and-absent, never broken.
_Avoid_: session reports (as a section name; a session log is the surfer's own record)

**Session log**:
The surfer's own record of one surfed session (`sessions/<date>-<slug>.md`), later compared against archived forecasts by the verification loop.
_Avoid_: session report

**Forecast log**:
Append-only archive of daily forecast snapshots for one spot (`forecasts/<slug>.jsonl`), the forecast side of the verification loop.
_Avoid_: forecast history, archive (alone)

**Target day**:
The specific day the surfer intends to surf. Reports, verdicts, and report filenames key to it; when none is given, the forecast window's first day stands in.
_Avoid_: session day, run date (that is when the research executed)

**Dashboard**:
The single self-contained HTML surface for one spot, with in-page tabs (Today, Forecast, Windows, Spot info). It is where a spot's forecast is read, so the terminal stays quiet. Built by `/surfing:dashboard`, and opened on the relevant tab by `/surfing:conditions`, `/surfing:windows`, and `/surfing:research`. One per spot per day (`reports/<date>-<slug>-dashboard.html`); it supersedes the old standalone single-spot report.
_Avoid_: report, single report, spot page

**Forecast** (Dashboard tab):
One spot's next 7 days, interactive. A **Week at a glance** overview (a horizontally-compressed 7-day tide chart, each day clipped to daylight, first light to last light) sits above a **By day** list of day rows (weekday, verdict, swell, one-line description); picking a row swaps in that day's full tide chart with its hourly strip. About one spot over the week, as opposed to the week planner's ranking across many spots.
_Avoid_: outlook, week (for a single spot)

**Mid-tide split** (tide-chart fill):
The two-tone fill on a tide chart: the band between the curve and a mid-tide reference line is coloured one way above the line (high water) and another below (low water). Shared by the Forecast overview and every per-day/Today chart.
_Avoid_: gradient, tide shading

**Week planner**:
The cross-spot view (`/surfing:week`) that ranks the best session windows across all home spots for the coming week, best first. About which spot to surf, as opposed to the Forecast tab's single-spot week.
_Avoid_: weekly report, week view
