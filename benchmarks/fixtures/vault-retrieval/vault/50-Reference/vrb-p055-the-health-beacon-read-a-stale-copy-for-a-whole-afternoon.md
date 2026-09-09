---
id: VRB-P055
name: the health beacon read a stale copy for a whole afternoon
type: reference
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The health beacon in Fjord-ripple-NOTES.md read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The health beacon lives in Fjord-ripple-NOTES.md. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/fjord-ripple-notes/Fjord-ripple-NOTES.md.

Files touched in the same incident: Amber-quartz-NOTES.md.

What to do instead: invalidate on write, not on a timer nobody watches.
