---
id: VRB-P051
name: the page cache read a stale copy for a whole afternoon
type: reference
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The page cache in larch_invoice.sh read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The page cache lives in larch_invoice.sh. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/larch-invoice/larch_invoice.sh.

Files touched in the same incident: cedar_anvil.sh, gamma_cache.sh, larch_loader.sh.

What to do instead: sum first, round last, and say which one you printed.
