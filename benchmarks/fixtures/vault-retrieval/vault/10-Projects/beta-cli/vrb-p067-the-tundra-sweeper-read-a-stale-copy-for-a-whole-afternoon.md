---
id: VRB-P067
name: the tundra sweeper read a stale copy for a whole afternoon
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The tundra sweeper in onyx_tundra.json read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The tundra sweeper lives in onyx_tundra.json. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/onyx-tundra/onyx_tundra.json.

Files touched in the same incident: Alpha-seat-NOTES.md, GraniteRoster.swift, larch_ripple.sh.

What to do instead: store one zone and convert at the edge.
