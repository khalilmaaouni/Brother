---
id: VRB-P053
name: the ledger writer read a stale copy for a whole afternoon
type: reference
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The ledger writer in marble_invoice.json read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The ledger writer lives in marble_invoice.json. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/marble-invoice/marble_invoice.json.

Files touched in the same incident: tau_sync.json.

What to do instead: create the directory before the first write, once.
