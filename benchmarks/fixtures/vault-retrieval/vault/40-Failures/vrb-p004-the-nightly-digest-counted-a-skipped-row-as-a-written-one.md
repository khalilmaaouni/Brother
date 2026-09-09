---
id: VRB-P004
name: the nightly digest counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The nightly digest in granite_invoice.json counted a skipped row as a written one, and every check downstream read the wrong number.
---

The nightly digest lives in granite_invoice.json. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/granite-invoice/granite_invoice.json.

Files touched in the same incident: cedar-tally.ts, delta-invoice.ts, fjord-tally.ts.

What to do instead: carry the identifier through as one field, not two.
