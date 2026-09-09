---
id: VRB-P020
name: the seat allocator counted a skipped row as a written one
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The seat allocator in Juniper-warden-NOTES.md counted a skipped row as a written one, and every check downstream read the wrong number.
---

The seat allocator lives in Juniper-warden-NOTES.md. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/juniper-warden-notes/Juniper-warden-NOTES.md.

Files touched in the same incident: Amber-quartz-NOTES.md, Kappa-tally-NOTES.md.

What to do instead: derive trust from the session, never from a header.
