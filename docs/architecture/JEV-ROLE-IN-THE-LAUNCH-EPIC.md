# Jev's role in the 1.1.0 launch epic

Jev answers typed questions with a probability. It does not review, draft or
decide. Its job here is to be the cheap, calibrated second reader on every
claim, through the control plane that already exists (jev_registry,
jev_decide, jev_seam, jev_calibration, jev_cascade, jev_canary). Nothing in
this epic calls Jev outside that plane.

## The six seams, all in scripts/jev_checks.py

| Step | Seam | Asked | When |
|---|---|---|---|
| 1 | J064 check_gate_log_lines | is this gate line a pass, a real fail, or noise | after every gate run |
| 2 | J030 check_row_claim_support | does the quoted source support this finding | on every Muse or Deepseek finding before it is acted on |
| 3 | J063 check_worker_completion_claim | does this completion note match the changed paths | on every worker result |
| 4 | J102 check_done_claim_verification | does this DONE entry quote a verification | on every status entry before the board ticks it |
| 5 | J117 check_scope_audit | does this changed path belong to the unit's objective | on every commit's path list |
| 6 | outcomes | the orchestrator writes the true outcome back with jev_calibration.append_outcome | whenever a deterministic check settles the question |

## Mode

All five entries run in `shadow` (data/jev-seams.json): Jev is asked, the
answer lands in the calibration ledger, behaviour does not change. A seam
moves to `advise` or `act` only when jev_calibration.threshold() shows, from
recorded outcomes, that its confidence meets the precision its risk class
needs. Jev never gates a merge or a release.

## First measurement, 2026-09-20

J064 over 12 real lines of scripts/required_fast.sh output: 10 answers at
confidence 0.94 or above, 10 of 10 correct. 2 answers below the lowest band
(0.34 and 0.45), both wrong, both on lines where the gate summary prints a
test fixture's own "FAIL:" text under a passing check. The cascade sends
low confidence to a human, which is the right outcome: those are the two
lines a human misread earlier the same day. Ledger anomalies: 0.

Finding for the gate itself: required_fast.sh prints a suite's last stdout
line as its summary, so a passing suite whose fixtures print "FAIL:" reads
as a failure at a glance. The summary should come from the verdict line.
