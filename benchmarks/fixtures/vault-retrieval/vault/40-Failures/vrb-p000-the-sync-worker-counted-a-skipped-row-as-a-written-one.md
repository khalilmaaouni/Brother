---
id: VRB-P000
name: the sync worker counted a skipped row as a written one
type: failure
authority: derived
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The sync worker in dune_quartz.py counted a skipped row as a written one, and every check downstream read the wrong number.
---

The sync worker lives in dune_quartz.py. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/dune-quartz/dune_quartz.py.

Files touched in the same incident: marble_invoice.json, tau_sync.json.

What to do instead: count what was written, never what was offered.
