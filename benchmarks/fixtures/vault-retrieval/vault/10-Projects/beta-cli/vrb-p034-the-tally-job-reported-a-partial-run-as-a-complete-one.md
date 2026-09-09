---
id: VRB-P034
name: the tally job reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The tally job in Granite-orbit-NOTES.md reported a partial run as a complete one, and every check downstream read the wrong number.
---

The tally job lives in Granite-orbit-NOTES.md. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/granite-orbit-notes/Granite-orbit-NOTES.md.

Files touched in the same incident: Amber-quartz-NOTES.md, Indigo-ledger-NOTES.md, Kappa-tally-NOTES.md.

What to do instead: print the number of rows the writer actually accepted.
