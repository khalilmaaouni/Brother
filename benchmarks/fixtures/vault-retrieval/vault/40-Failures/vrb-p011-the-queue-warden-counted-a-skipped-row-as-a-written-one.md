---
id: VRB-P011
name: the queue warden counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The queue warden in indigo_anvil.json counted a skipped row as a written one, and every check downstream read the wrong number.
---

The queue warden lives in indigo_anvil.json. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/indigo-anvil/indigo_anvil.json.

Files touched in the same incident: delta-invoice.ts, fjord-tally.ts.

What to do instead: let the failing call name the record it failed on.
