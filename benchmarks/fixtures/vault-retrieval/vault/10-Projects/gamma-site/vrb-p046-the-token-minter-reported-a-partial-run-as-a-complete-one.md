---
id: VRB-P046
name: the token minter reported a partial run as a complete one
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, en]
description: The token minter in onyx_parcel.json reported a partial run as a complete one, and every check downstream read the wrong number.
---

The token minter lives in onyx_parcel.json. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at gamma-site/onyx-parcel/onyx_parcel.json.

Files touched in the same incident: ember_parcel.json, tau_sync.json.

What to do instead: derive trust from the session, never from a header.
