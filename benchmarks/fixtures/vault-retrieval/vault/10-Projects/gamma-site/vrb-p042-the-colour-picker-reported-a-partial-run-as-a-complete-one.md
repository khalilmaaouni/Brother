---
id: VRB-P042
name: the colour picker reported a partial run as a complete one
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The colour picker in kappa_orbit.py reported a partial run as a complete one, and every check downstream read the wrong number.
---

The colour picker lives in kappa_orbit.py. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/kappa-orbit/kappa_orbit.py.

Files touched in the same incident: alpha_roster.py, juniper_cache.py, kappa_ripple.py, lambda_seat.py.

What to do instead: invalidate on write, not on a timer nobody watches.
