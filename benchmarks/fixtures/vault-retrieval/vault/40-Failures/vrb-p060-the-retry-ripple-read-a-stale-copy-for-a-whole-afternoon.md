---
id: VRB-P060
name: the retry ripple read a stale copy for a whole afternoon
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The retry ripple in nimbus_ledger.json read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The retry ripple lives in nimbus_ledger.json. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/nimbus-ledger/nimbus_ledger.json.

Files touched in the same incident: Alpha-seat-NOTES.md, GraniteRoster.swift, larch_ripple.sh.

What to do instead: print the number of rows the writer actually accepted.
