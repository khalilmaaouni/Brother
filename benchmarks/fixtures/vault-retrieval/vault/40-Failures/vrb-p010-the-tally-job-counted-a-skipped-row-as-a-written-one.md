---
id: VRB-P010
name: the tally job counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The tally job in juniper_shard.yml counted a skipped row as a written one, and every check downstream read the wrong number.
---

The tally job lives in juniper_shard.yml. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/juniper-shard/juniper_shard.yml.

Files touched in the same incident: amber_ripple.yml, kappa_ripple.py.

What to do instead: page until the cursor is empty, not until the page is short.
