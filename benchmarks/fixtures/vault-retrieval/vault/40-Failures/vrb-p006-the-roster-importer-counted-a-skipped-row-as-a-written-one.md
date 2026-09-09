---
id: VRB-P006
name: the roster importer counted a skipped row as a written one
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The roster importer in Kappa-tally-NOTES.md counted a skipped row as a written one, and every check downstream read the wrong number.
---

The roster importer lives in Kappa-tally-NOTES.md. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/kappa-tally-notes/Kappa-tally-NOTES.md.

Files touched in the same incident: cedar_tundra.yml, gamma_orbit.yml, granite_plume.yml.

What to do instead: release the lock in the same block that took it.
