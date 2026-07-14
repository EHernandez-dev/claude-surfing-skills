# Forecast tab: interactive week overview plus per-day drilldown

The Forecast tab is interactive. It stacks two boxes: a compact **Week at a glance** tide overview (one daylight-clipped column per day, its fill split at a mid-tide reference line, one colour above the line for high water and another below for low water), then a **By day** box, a list of day-selector rows (weekday, verdict chip, swell, a one-line description) where clicking a row swaps in that day's full tide chart below it. The per-day chart is the same one the Today tab draws: the mid-tide two-tone fill, session-window shading, a per-hour x-axis (a light gridline every hour, every second line stronger), and the aligned hourly Swell / Period / Wind strip.

This grows issue #16 beyond its original "7 day rows + one compressed chart" scope, settled by the `prototype_forecast_interaction.py` prototype (variant C). No `fetch_conditions.py` change is needed: `build_marine_days` already emits per-day `hours[]` for all seven days, so every day's detail chart runs on existing payload.

## Considered options

- **Original #16 (static 7 rows + one compressed chart).** Rejected: the squished 7-day chart is hard to read for timing a session; a full per-day chart is what a surfer actually reads.
- **Variant A (day buttons + one detail chart, no week overview).** Rejected: loses the at-a-glance week tide shape.
- **Variant B (accordion rows, chart expands in place).** Rejected: expanding a mid-week row shoves the lower days far down the page.
- **Variant C (chosen): week overview kept in its own box for context, plus a day-selector drilldown.**

## Consequences

- The mid-tide two-tone fill and the per-hour axis live in the shared `tide_svg`, so the **Today** tab's chart gains them too, for consistency; the golden files regenerate.
- `render_report.py` grows a refined `week_tide_svg` (halved height, mid line, two-tone, hour ticks, high/low times, top labels, thin day separators) and a small day-selector toggle script; the flat `.md` twin has no interaction but still lists the seven days.
- The prototype `prototype_forecast_interaction.py` holds the locked visuals until they are folded into `render_report.py`, then it is deleted.
