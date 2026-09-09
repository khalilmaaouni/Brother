---
id: VRB-P014
name: the tile mosaic counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The tile mosaic in alpha_roster.py counted a skipped row as a written one, and every check downstream read the wrong number.
---

The tile mosaic lives in alpha_roster.py. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/alpha-roster/alpha_roster.py.

Files touched in the same incident: juniper_cache.py, tau_sync.json.

What to do instead: create the directory before the first write, once.
