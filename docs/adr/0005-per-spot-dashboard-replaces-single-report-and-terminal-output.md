# Per-spot dashboard replaces the single-spot report and terminal output

The per-spot commands (`/surfing:conditions`, `/surfing:windows`, `/surfing:research`) and a new `/surfing:dashboard` now all build one self-contained, tabbed HTML **Dashboard** (Today, Forecast, Windows, Spot info) and open it, printing nothing else to chat beyond the file path (chat still speaks on failure or missing setup, when there is no HTML to open). This inverts the previous principle that chat is canonical and HTML is a companion, and it retires `render_report.py`'s standalone `single` mode in favour of a single per-spot renderer.

## Considered options

- **Keep chat canonical, HTML as companion (status quo).** Rejected: the whole request was to read these views in HTML, not the terminal.
- **Keep the standalone single-spot report alongside a separate dashboard.** Rejected: two overlapping per-spot HTML layouts (Today/Spot-info content) to keep in sync.

## Consequences

- Cross-spot surfaces (`/surfing:briefing`, `/surfing:week`) are explicitly out of scope here and keep printing to the terminal; they would get their own multi-spot HTML in a later, separate effort.
- An unprofiled spot still renders (full conditions/tides/forecast/windows) but with a visible "generic, not spot-corrected" banner and a stub Spot-info tab, matching how the commands already degrade.
