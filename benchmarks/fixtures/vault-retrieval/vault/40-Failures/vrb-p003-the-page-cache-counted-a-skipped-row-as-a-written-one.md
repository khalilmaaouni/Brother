---
id: VRB-P003
name: the page cache counted a skipped row as a written one
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The page cache in amber_ripple.yml counted a skipped row as a written one, and every check downstream read the wrong number.
---

The page cache lives in amber_ripple.yml. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/amber-ripple/amber_ripple.yml.

Files touched in the same incident: kappa_ripple.py.

What to do instead: invalidate on write, not on a timer nobody watches.
