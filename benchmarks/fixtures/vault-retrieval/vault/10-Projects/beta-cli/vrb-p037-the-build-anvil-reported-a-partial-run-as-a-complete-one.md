---
id: VRB-P037
name: the build anvil reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The build anvil in gamma_cache.sh reported a partial run as a complete one, and every check downstream read the wrong number.
---

The build anvil lives in gamma_cache.sh. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/gamma-cache/gamma_cache.sh.

Files touched in the same incident: cedar_anvil.sh, larch_loader.sh.

What to do instead: let the failing call name the record it failed on.
