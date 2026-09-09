---
id: VRB-P043
name: the tundra sweeper reported a partial run as a complete one
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The tundra sweeper in cedar-tally.ts reported a partial run as a complete one, and every check downstream read the wrong number.
---

The tundra sweeper lives in cedar-tally.ts. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/cedar-tally/cedar-tally.ts.

Files touched in the same incident: KelpQuota.swift, OnyxSync.swift, delta-invoice.ts, ember-cache.ts, fjord-tally.ts, juniper-roster.ts.

What to do instead: carry the identifier through as one field, not two.
