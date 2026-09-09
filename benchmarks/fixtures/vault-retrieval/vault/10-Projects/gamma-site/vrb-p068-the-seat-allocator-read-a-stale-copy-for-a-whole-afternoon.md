---
id: VRB-P068
name: the seat allocator read a stale copy for a whole afternoon
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The seat allocator in GraniteRoster.swift read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The seat allocator lives in GraniteRoster.swift. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/graniteroster/GraniteRoster.swift.

Files touched in the same incident: AlphaQuartz.swift, indigo-beacon.ts, onyx_tundra.json.

What to do instead: invalidate on write, not on a timer nobody watches.
