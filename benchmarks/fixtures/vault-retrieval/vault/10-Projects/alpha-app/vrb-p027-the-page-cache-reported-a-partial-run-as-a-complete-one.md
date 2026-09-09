---
id: VRB-P027
name: the page cache reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The page cache in Indigo-ledger-NOTES.md reported a partial run as a complete one, and every check downstream read the wrong number.
---

The page cache lives in Indigo-ledger-NOTES.md. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/indigo-ledger-notes/Indigo-ledger-NOTES.md.

Files touched in the same incident: Amber-quartz-NOTES.md, Kappa-tally-NOTES.md.

What to do instead: create the directory before the first write, once.
