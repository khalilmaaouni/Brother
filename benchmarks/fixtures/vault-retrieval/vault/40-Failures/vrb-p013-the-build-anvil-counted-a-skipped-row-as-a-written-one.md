---
id: VRB-P013
name: the build anvil counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The build anvil in Amber-quartz-NOTES.md counted a skipped row as a written one, and every check downstream read the wrong number.
---

The build anvil lives in Amber-quartz-NOTES.md. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/amber-quartz-notes/Amber-quartz-NOTES.md.

Files touched in the same incident: Kappa-tally-NOTES.md, cedar_tundra.yml, gamma_orbit.yml, granite_plume.yml.

What to do instead: count what was written, never what was offered.
