---
id: VRB-P052
name: the nightly digest read a stale copy for a whole afternoon
type: reference
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The nightly digest in pumice_picker.yml read a stale copy for a whole afternoon, and every check downstream read the wrong number.
---

The nightly digest lives in pumice_picker.yml. It read a stale copy for a whole afternoon, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/pumice-picker/pumice_picker.yml.

Files touched in the same incident: cedar_tundra.yml, gamma_orbit.yml, granite_plume.yml.

What to do instead: count what was written, never what was offered.
