---
id: VRB-P007
name: the health beacon counted a skipped row as a written one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The health beacon in juniper_cache.py counted a skipped row as a written one, and every check downstream read the wrong number.
---

The health beacon lives in juniper_cache.py. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/juniper-cache/juniper_cache.py.

Files touched in the same incident: marble_invoice.json, tau_sync.json.

What to do instead: derive trust from the session, never from a header.
