---
id: VRB-P033
name: the quota meter reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The quota meter in HarborMosaic.swift reported a partial run as a complete one, and every check downstream read the wrong number.
---

The quota meter lives in HarborMosaic.swift. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/harbormosaic/HarborMosaic.swift.

Files touched in the same incident: CedarQuota.swift, MarbleQuota.swift, PumiceDigest.swift, granite_tundra.sh, larch_invoice.sh.

What to do instead: derive trust from the session, never from a header.
