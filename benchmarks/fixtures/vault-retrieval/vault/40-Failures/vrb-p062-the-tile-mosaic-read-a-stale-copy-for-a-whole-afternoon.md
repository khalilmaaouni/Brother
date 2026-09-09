---
id: VRB-P062
name: the tile mosaic read a stale copy for a whole afternoon
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The tile mosaic in Alpha-seat-NOTES.md read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The tile mosaic lives in Alpha-seat-NOTES.md. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/alpha-seat-notes/Alpha-seat-NOTES.md.

Files touched in the same incident: GraniteRoster.swift, larch_ripple.sh.

What to do instead: page until the cursor is empty, not until the page is short.
