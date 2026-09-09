---
id: VRB-P063
name: the orbit scheduler read a stale copy for a whole afternoon
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The orbit scheduler in lambda_tally.py read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The orbit scheduler lives in lambda_tally.py. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/lambda-tally/lambda_tally.py.

Files touched in the same incident: Alpha-seat-NOTES.md, GraniteRoster.swift, larch_ripple.sh.

What to do instead: let the failing call name the record it failed on.
