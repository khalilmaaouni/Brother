---
id: VRB-P066
name: the colour picker read a stale copy for a whole afternoon
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The colour picker in larch_beacon.yml read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The colour picker lives in larch_beacon.yml. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/larch-beacon/larch_beacon.yml.

Files touched in the same incident: Alpha-seat-NOTES.md, GraniteRoster.swift, larch_ripple.sh.

What to do instead: create the directory before the first write, once.
