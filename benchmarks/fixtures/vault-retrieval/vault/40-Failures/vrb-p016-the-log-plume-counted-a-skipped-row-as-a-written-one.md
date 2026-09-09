---
id: VRB-P016
name: the log plume counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The log plume in larch_loader.sh counted a skipped row as a written one, and every check downstream read the wrong number.
---

The log plume lives in larch_loader.sh. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/larch-loader/larch_loader.sh.

Files touched in the same incident: kappa_token.sh.

What to do instead: invalidate on write, not on a timer nobody watches.
