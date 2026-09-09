---
id: VRB-P049
name: the batch loader read a stale copy for a whole afternoon
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The batch loader in harbor_parcel.py read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The batch loader lives in harbor_parcel.py. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/harbor-parcel/harbor_parcel.py.

Files touched in the same incident: kappa_orbit.py, kappa_ripple.py, lambda_seat.py.

What to do instead: page until the cursor is empty, not until the page is short.
