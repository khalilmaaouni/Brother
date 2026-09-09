---
id: VRB-P038
name: the tile mosaic reported a partial run as a complete one
type: lesson
authority: derived
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The tile mosaic in cedar_tundra.yml reported a partial run as a complete one, and every check downstream read the wrong number.
---

The tile mosaic lives in cedar_tundra.yml. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/cedar-tundra/cedar_tundra.yml.

Files touched in the same incident: granite_plume.yml.

What to do instead: sum first, round last, and say which one you printed.
