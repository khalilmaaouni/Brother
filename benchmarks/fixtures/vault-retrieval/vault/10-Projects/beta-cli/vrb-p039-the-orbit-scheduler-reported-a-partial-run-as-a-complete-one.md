---
id: VRB-P039
name: the orbit scheduler reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The orbit scheduler in dune_router.json reported a partial run as a complete one, and every check downstream read the wrong number.
---

The orbit scheduler lives in dune_router.json. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/dune-router/dune_router.json.

Files touched in the same incident: ember_parcel.json, tau_sync.json.

What to do instead: count what was written, never what was offered.
