# Prototype verdict: HTML visual report design (Increment 2)

Question: what should render_report.py's page look like?
Answered 2026-07-10 by flipping prototype-visual-report.html variants A/B/C with the user.

**Winner: A/C hybrid on C's light color scheme.**

- Map hero as in variant C: half-screen Leaflet + OpenStreetMap (keyless; Google embed from variant B loses), verdict chip + spot name + one-liner overlaid bottom-left.
- Tide curve with shaded session windows below the hero (same in A and C).
- Week section as in variant A: full-width rows (day, verdict chip, swell/wind, one-line reasoning), not C's horizontal day cards.
- Hazards as in variant A: always-visible list, not accordions.
- Color scheme: C's light palette as the base, plus a dark mode carrying A's look (prefers-color-scheme, both maintained).
- Leaflet JS/CSS inlined into the generated file; only map tiles are remote, and the page must degrade gracefully offline (everything but the map renders).

Both the prototype HTML and this note get deleted when render_report.py lands (reference them from the Increment 2 issue until then).
