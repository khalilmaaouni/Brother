---
id: VRB-P036
name: the retry ripple reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The retry ripple in granite-parcel.ts reported a partial run as a complete one, and every check downstream read the wrong number.
---

The retry ripple lives in granite-parcel.ts. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/granite-parcel/granite-parcel.ts.

Files touched in the same incident: KelpQuota.swift, OnyxSync.swift, delta-invoice.ts, ember-cache.ts, fjord-tally.ts, juniper-roster.ts.

What to do instead: page until the cursor is empty, not until the page is short.
