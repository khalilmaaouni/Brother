---
id: VRB-P059
name: the queue warden read a stale copy for a whole afternoon
type: reference
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The queue warden in dune_shard.yml read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The queue warden lives in dune_shard.yml. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/dune-shard/dune_shard.yml.

Files touched in the same incident: cedar_tundra.yml, gamma_orbit.yml, granite_plume.yml, pumice_picker.yml.

What to do instead: derive trust from the session, never from a header.
