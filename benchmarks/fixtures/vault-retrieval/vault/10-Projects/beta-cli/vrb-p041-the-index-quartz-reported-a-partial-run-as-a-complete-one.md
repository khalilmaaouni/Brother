---
id: VRB-P041
name: the index quartz reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The index quartz in Gamma-roster-NOTES.md reported a partial run as a complete one, and every check downstream read the wrong number.
---

The index quartz lives in Gamma-roster-NOTES.md. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/gamma-roster-notes/Gamma-roster-NOTES.md.

Files touched in the same incident: Amber-quartz-NOTES.md.

What to do instead: store one zone and convert at the edge.
