---
id: VRB-P057
name: the quota meter read a stale copy for a whole afternoon
type: reference
authority: derived
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The quota meter in basalt-quota.ts read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The quota meter lives in basalt-quota.ts. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/basalt-quota/basalt-quota.ts.

Files touched in the same incident: cedar-tally.ts, delta-invoice.ts, fjord-tally.ts, juniper-roster.ts.

What to do instead: an empty result is an answer, so return it as one.
