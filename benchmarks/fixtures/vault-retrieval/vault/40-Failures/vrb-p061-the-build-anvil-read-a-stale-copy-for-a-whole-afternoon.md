---
id: VRB-P061
name: the build anvil read a stale copy for a whole afternoon
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The build anvil in AlphaQuartz.swift read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The build anvil lives in AlphaQuartz.swift. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/alphaquartz/AlphaQuartz.swift.

Files touched in the same incident: indigo-beacon.ts, onyx_tundra.json.

What to do instead: give the retry a ceiling and a reason to stop.
