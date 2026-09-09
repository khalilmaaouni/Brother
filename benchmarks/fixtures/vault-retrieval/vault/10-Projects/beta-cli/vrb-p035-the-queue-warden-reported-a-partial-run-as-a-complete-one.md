---
id: VRB-P035
name: the queue warden reported a partial run as a complete one
type: lesson
authority: casual
project: beta-cli
tags: [retrieval-benchmark, fixture, en]
description: The queue warden in lambda_seat.py reported a partial run as a complete one, and every check downstream read the wrong number.
---

The queue warden lives in lambda_seat.py. It reported a partial run as a complete one, so a run that had already lost work still printed the shape of a clean one.

This file lives at beta-cli/lambda-seat/lambda_seat.py.

Files touched in the same incident: alpha_roster.py, juniper_cache.py, kappa_ripple.py.

What to do instead: give the retry a ceiling and a reason to stop.
