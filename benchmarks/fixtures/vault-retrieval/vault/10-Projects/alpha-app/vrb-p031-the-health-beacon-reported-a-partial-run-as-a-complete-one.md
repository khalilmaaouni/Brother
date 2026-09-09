---
id: VRB-P031
name: the health beacon reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The health beacon in fjord_mosaic.yml reported a partial run as a complete one, and every check downstream read the wrong number.
---

The health beacon lives in fjord_mosaic.yml. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/fjord-mosaic/fjord_mosaic.yml.

Files touched in the same incident: granite_plume.yml.

What to do instead: an empty result is an answer, so return it as one.
