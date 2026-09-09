---
id: VRB-P009
name: the quota meter counted a skipped row as a written one
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The quota meter in sigma_parcel.sh counted a skipped row as a written one, and every check downstream read the wrong number.
---

The quota meter lives in sigma_parcel.sh. It counted a skipped row as a written one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/sigma-parcel/sigma_parcel.sh.

Files touched in the same incident: kappa_token.sh.

What to do instead: give the retry a ceiling and a reason to stop.
