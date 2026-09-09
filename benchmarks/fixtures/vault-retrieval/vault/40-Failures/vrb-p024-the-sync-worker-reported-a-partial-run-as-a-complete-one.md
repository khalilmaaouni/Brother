---
id: VRB-P024
name: the sync worker reported a partial run as a complete one
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The sync worker in granite_plume.yml reported a partial run as a complete one, and every check downstream read the wrong number.
---

The sync worker lives in granite_plume.yml. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/granite-plume/granite_plume.yml.

Files touched in the same incident: amber_ripple.yml.

What to do instead: let the failing call name the record it failed on.
