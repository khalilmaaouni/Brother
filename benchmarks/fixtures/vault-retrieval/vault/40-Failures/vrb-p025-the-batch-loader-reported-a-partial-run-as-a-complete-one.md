---
id: VRB-P025
name: the batch loader reported a partial run as a complete one
type: failure
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The batch loader in tau_sync.json reported a partial run as a complete one, and every check downstream read the wrong number.
---

The batch loader lives in tau_sync.json. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/tau-sync/tau_sync.json.

Files touched in the same incident: indigo_anvil.json.

What to do instead: sum first, round last, and say which one you printed.
