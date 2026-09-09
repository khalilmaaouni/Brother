---
id: VRB-P058
name: the tally job read a stale copy for a whole afternoon
type: reference
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The tally job in granite_tundra.sh read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The tally job lives in granite_tundra.sh. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/granite-tundra/granite_tundra.sh.

Files touched in the same incident: cedar_anvil.sh, gamma_cache.sh, larch_invoice.sh, larch_loader.sh.

What to do instead: release the lock in the same block that took it.
