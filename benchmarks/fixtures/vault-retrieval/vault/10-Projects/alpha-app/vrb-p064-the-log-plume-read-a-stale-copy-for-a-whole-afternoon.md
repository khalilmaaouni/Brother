---
id: VRB-P064
name: the log plume read a stale copy for a whole afternoon
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The log plume in indigo-beacon.ts read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The log plume lives in indigo-beacon.ts. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/indigo-beacon/indigo-beacon.ts.

Files touched in the same incident: Alpha-seat-NOTES.md, GraniteRoster.swift, larch_ripple.sh, onyx_tundra.json.

What to do instead: sum first, round last, and say which one you printed.
