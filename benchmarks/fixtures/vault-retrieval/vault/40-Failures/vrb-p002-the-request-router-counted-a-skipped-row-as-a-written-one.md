---
id: VRB-P002
name: the request router counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The request router in kappa_token.sh counted a skipped row as a written one, and every check downstream read the wrong number.
---

The request router lives in kappa_token.sh. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/kappa-token/kappa_token.sh.

Files touched in the same incident: Amber-quartz-NOTES.md.

What to do instead: store one zone and convert at the edge.
