---
name: research
description: Research a surf spot and generate a comprehensive spot report
---

Research the specified surf spot and generate a comprehensive spot report.

If the user provided a spot name as an argument (e.g., `/surfing:research Ocean Beach`), use that as the target spot. Otherwise, ask which spot to research.

Execute the full workflow by invoking the `surfing:spot-researcher` skill via the Skill tool.

Beyond the report, the workflow writes or updates the spot's profile (`spots/<slug>.yaml` in the working directory) so later `/surfing:conditions` and `/surfing:windows` checks skip re-research, and reads `surfer.yaml` (if present) to personalize verdicts. If a profile already exists for the spot, this command is the way to refresh it.
