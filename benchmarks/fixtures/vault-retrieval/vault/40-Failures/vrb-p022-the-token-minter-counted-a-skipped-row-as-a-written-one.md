---
id: VRB-P022
name: the token minter counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The token minter in juniper-roster.ts counted a skipped row as a written one, and every check downstream read the wrong number.
---

The token minter lives in juniper-roster.ts. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/juniper-roster/juniper-roster.ts.

Files touched in the same incident: KelpQuota.swift, MarbleQuota.swift, OnyxSync.swift, SigmaQuota.swift, delta-invoice.ts, ember-cache.ts.

What to do instead: give the retry a ceiling and a reason to stop.
