---
id: VRB-P045
name: the invoice folder reported a partial run as a complete one
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The invoice folder in gamma_orbit.yml reported a partial run as a complete one, and every check downstream read the wrong number.
---

The invoice folder lives in gamma_orbit.yml. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/gamma-orbit/gamma_orbit.yml.

Files touched in the same incident: cedar_tundra.yml, granite_plume.yml.

What to do instead: release the lock in the same block that took it.
