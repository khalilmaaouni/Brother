---
id: VRB-P008
name: the parcel tracker counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The parcel tracker in ember-cache.ts counted a skipped row as a written one, and every check downstream read the wrong number.
---

The parcel tracker lives in ember-cache.ts. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/ember-cache/ember-cache.ts.

Files touched in the same incident: KelpQuota.swift, MarbleQuota.swift, OnyxSync.swift, SigmaQuota.swift, juniper-mosaic.ts.

What to do instead: print the number of rows the writer actually accepted.
