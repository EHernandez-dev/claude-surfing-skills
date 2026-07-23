# Fast builds emit mechanically corrected Verdicts

The dashboard's fast mode renders a draft package with no analyst pass, so its go/check/skip calls come from a script. We decided these are true Verdicts, not a lesser "draft verdict" concept: the script corrects the quality-rating band (epic/good to go, fair to check, poor/flat to skip) against the machine-readable works-on fields. Period below `min_period_s` is a hard skip (exact arithmetic against a field the profile owner set as a minimum); swell direction outside a plus/minus 45 degree arc around the profile's compass token is a one-step demotion only (token matching is fuzzy, so it nudges rather than overrules). The prose works-on fields (size range, tide window, wind preference) are never consulted mechanically; that depth difference is disclosed by a "computed call, no analyst pass" tag the script appends to the hero one_liner, which the normal-mode agent removes by always rewriting that line.

## Considered Options

- A new "draft verdict" domain term with page-level marking: rejected as a glossary entry for a transitional artifact.
- No fast mode as a user-facing product (script output only ever an intermediate the agent edits): rejected because it gives up the ~20 s path that motivated the change.
- Hard skip on direction misses: rejected because the compass-token match is crude and a false positive would turn a real Go day into a Skip.

## Consequences

The CONTEXT.md Verdict entry was loosened: spot-corrected when a profile exists, personalized by the surfer profile when present, never presented as the raw quality score alone. Fast-build verdicts for unprofiled spots are rating-only, covered by the existing generic-call disclosure.
