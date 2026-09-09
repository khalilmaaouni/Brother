---
id: VRB-P017
name: the index quartz counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The index quartz in kappa_roster.yml counted a skipped row as a written one, and every check downstream read the wrong number.
---

The index quartz lives in kappa_roster.yml. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/kappa-roster/kappa_roster.yml.

Files touched in the same incident: amber_ripple.yml, juniper_shard.yml.

What to do instead: carry the identifier through as one field, not two.
