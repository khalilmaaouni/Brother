---
id: VRB-P018
name: the colour picker counted a skipped row as a written one
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The colour picker in marble_seat.json counted a skipped row as a written one, and every check downstream read the wrong number.
---

The colour picker lives in marble_seat.json. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/marble-seat/marble_seat.json.

Files touched in the same incident: fjord-tally.ts, indigo_anvil.json.

What to do instead: an empty result is an answer, so return it as one.
