---
id: VRB-P069
name: the invoice folder read a stale copy for a whole afternoon
type: reference
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The invoice folder in Indigo-parcel-NOTES.md read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The invoice folder lives in Indigo-parcel-NOTES.md. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/indigo-parcel-notes/Indigo-parcel-NOTES.md.

Files touched in the same incident: Alpha-seat-NOTES.md, GraniteRoster.swift, larch_ripple.sh.

What to do instead: carry the identifier through as one field, not two.
