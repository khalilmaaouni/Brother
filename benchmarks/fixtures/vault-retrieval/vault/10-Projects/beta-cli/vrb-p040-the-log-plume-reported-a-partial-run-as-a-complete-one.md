---
id: VRB-P040
name: the log plume reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The log plume in OnyxSync.swift reported a partial run as a complete one, and every check downstream read the wrong number.
---

The log plume lives in OnyxSync.swift. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/onyxsync/OnyxSync.swift.

Files touched in the same incident: CedarQuota.swift, MarbleQuota.swift, granite_tundra.sh.

What to do instead: create the directory before the first write, once.
