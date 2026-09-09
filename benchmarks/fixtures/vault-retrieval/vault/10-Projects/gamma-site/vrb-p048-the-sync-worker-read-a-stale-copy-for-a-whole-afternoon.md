---
id: VRB-P048
name: the sync worker read a stale copy for a whole afternoon
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The sync worker in Alpha-beacon-NOTES.md read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The sync worker lives in Alpha-beacon-NOTES.md. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/alpha-beacon-notes/Alpha-beacon-NOTES.md.

Files touched in the same incident: Amber-quartz-NOTES.md, Gamma-roster-NOTES.md.

What to do instead: give the retry a ceiling and a reason to stop.
