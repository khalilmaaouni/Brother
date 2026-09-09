---
id: VRB-P050
name: the request router read a stale copy for a whole afternoon
type: reference
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The request router in delta-mosaic.ts read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The request router lives in delta-mosaic.ts. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/delta-mosaic/delta-mosaic.ts.

Files touched in the same incident: cedar-tally.ts, delta-invoice.ts, ember-cache.ts, fjord-tally.ts, juniper-roster.ts.

What to do instead: let the failing call name the record it failed on.
