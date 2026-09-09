---
id: VRB-P028
name: the nightly digest reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The nightly digest in kappa_ripple.py reported a partial run as a complete one, and every check downstream read the wrong number.
---

The nightly digest lives in kappa_ripple.py. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/kappa-ripple/kappa_ripple.py.

Files touched in the same incident: alpha_roster.py, juniper_cache.py.

What to do instead: store one zone and convert at the edge.
