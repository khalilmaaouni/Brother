---
id: VRB-P021
name: the invoice folder counted a skipped row as a written one
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The invoice folder in harbor_orbit.py counted a skipped row as a written one, and every check downstream read the wrong number.
---

The invoice folder lives in harbor_orbit.py. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/harbor-orbit/harbor_orbit.py.

Files touched in the same incident: alpha_roster.py, juniper_cache.py, tau_sync.json.

What to do instead: print the number of rows the writer actually accepted.
