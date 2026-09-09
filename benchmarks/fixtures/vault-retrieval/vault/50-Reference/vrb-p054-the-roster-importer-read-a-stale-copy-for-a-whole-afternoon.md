---
id: VRB-P054
name: the roster importer read a stale copy for a whole afternoon
type: reference
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The roster importer in KelpQuota.swift read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The roster importer lives in KelpQuota.swift. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/kelpquota/KelpQuota.swift.

Files touched in the same incident: CedarQuota.swift, MarbleQuota.swift, OnyxSync.swift, SigmaQuota.swift, granite_tundra.sh.

What to do instead: store one zone and convert at the edge.
