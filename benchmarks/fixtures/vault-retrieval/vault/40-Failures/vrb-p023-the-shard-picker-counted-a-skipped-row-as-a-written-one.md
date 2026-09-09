---
id: VRB-P023
name: the shard picker counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The shard picker in lambda_sync.sh counted a skipped row as a written one, and every check downstream read the wrong number.
---

The shard picker lives in lambda_sync.sh. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/lambda-sync/lambda_sync.sh.

Files touched in the same incident: larch_loader.sh.

What to do instead: page until the cursor is empty, not until the page is short.
