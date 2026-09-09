---
id: VRB-P032
name: the parcel tracker reported a partial run as a complete one
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, en]
description: The parcel tracker in ember_parcel.json reported a partial run as a complete one, and every check downstream read the wrong number.
---

The parcel tracker lives in ember_parcel.json. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at alpha-app/ember-parcel/ember_parcel.json.

Files touched in the same incident: tau_sync.json.

What to do instead: release the lock in the same block that took it.
