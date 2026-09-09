---
id: VRB-P026
name: the request router reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The request router in MarbleQuota.swift reported a partial run as a complete one, and every check downstream read the wrong number.
---

The request router lives in MarbleQuota.swift. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/marblequota/MarbleQuota.swift.

Files touched in the same incident: CedarQuota.swift, PumiceDigest.swift, cedar_anvil.sh, granite_tundra.sh, larch_invoice.sh.

What to do instead: count what was written, never what was offered.
