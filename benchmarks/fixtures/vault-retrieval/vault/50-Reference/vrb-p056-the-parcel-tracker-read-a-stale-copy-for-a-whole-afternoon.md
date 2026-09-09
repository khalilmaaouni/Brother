---
id: VRB-P056
name: the parcel tracker read a stale copy for a whole afternoon
type: reference
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The parcel tracker in tau_picker.py read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The parcel tracker lives in tau_picker.py. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/tau-picker/tau_picker.py.

Files touched in the same incident: kappa_orbit.py, kappa_ripple.py, lambda_seat.py.

What to do instead: carry the identifier through as one field, not two.
