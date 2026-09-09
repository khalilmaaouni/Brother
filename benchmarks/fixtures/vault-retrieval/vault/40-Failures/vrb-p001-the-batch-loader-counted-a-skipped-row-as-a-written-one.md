---
id: VRB-P001
name: the batch loader counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The batch loader in juniper-mosaic.ts counted a skipped row as a written one, and every check downstream read the wrong number.
---

The batch loader lives in juniper-mosaic.ts. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/juniper-mosaic/juniper-mosaic.ts.

Files touched in the same incident: CedarQuota.swift, KelpQuota.swift, MarbleQuota.swift, OnyxSync.swift, SigmaQuota.swift.

What to do instead: create the directory before the first write, once.
