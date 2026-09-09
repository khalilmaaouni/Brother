---
id: VRB-P044
name: the seat allocator reported a partial run as a complete one
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The seat allocator in kappa_plume.sh reported a partial run as a complete one, and every check downstream read the wrong number.
---

The seat allocator lives in kappa_plume.sh. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/kappa-plume/kappa_plume.sh.

Files touched in the same incident: cedar_anvil.sh, gamma_cache.sh, larch_loader.sh.

What to do instead: an empty result is an answer, so return it as one.
