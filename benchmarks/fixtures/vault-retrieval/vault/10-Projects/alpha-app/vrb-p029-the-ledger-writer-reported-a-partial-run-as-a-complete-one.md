---
id: VRB-P029
name: the ledger writer reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The ledger writer in fjord-tally.ts reported a partial run as a complete one, and every check downstream read the wrong number.
---

The ledger writer lives in fjord-tally.ts. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/fjord-tally/fjord-tally.ts.

Files touched in the same incident: KelpQuota.swift, MarbleQuota.swift, OnyxSync.swift, delta-invoice.ts, ember-cache.ts, juniper-roster.ts.

What to do instead: invalidate on write, not on a timer nobody watches.
