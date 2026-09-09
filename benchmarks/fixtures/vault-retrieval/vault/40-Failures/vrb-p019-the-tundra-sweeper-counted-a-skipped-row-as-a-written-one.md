---
id: VRB-P019
name: the tundra sweeper counted a skipped row as a written one
type: failure
authority: derived
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The tundra sweeper in CedarQuota.swift counted a skipped row as a written one, and every check downstream read the wrong number.
---

The tundra sweeper lives in CedarQuota.swift. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/cedarquota/CedarQuota.swift.

Files touched in the same incident: PumiceDigest.swift, cedar_anvil.sh, granite_tundra.sh, larch_invoice.sh, larch_loader.sh.

What to do instead: release the lock in the same block that took it.
