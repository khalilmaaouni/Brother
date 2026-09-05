# Acceptance Compression: the frozen success rule (E1)

Frozen 2026-09-05, before any packet was assigned to a reviewer and before
any reviewer was recruited. This rule governs the human trial described in
`benchmarks/ACCEPTANCE-TIME.md` and `benchmarks/gauntlets/acceptance-compression.json`
(section 20.3). It is written once, here, and is not edited after the trial
starts. A test in `scripts/test_acceptance_trial_assign.py` checks this
file's own SHA-256 so a later silent edit is detectable.

## PRIMARY

The Brother receipt condition wins the trial when, on the same rounds and
the same reviewers:

1. Brother receipt median correct-decision time < raw diff median
   correct-decision time, AND
2. Brother receipt median correct-decision time < ordinary agent summary
   median correct-decision time, AND
3. Brother receipt decision correctness is greater than or equal to the
   best baseline's decision correctness (the higher of raw diff and
   ordinary summary), AND
4. Brother receipt does not increase false acceptance (the rate at which a
   reviewer accepts a change that should have been rejected is not higher
   under the Brother receipt than under either baseline).

All four hold together. Any one failing means the primary rule is not met.

## SECONDARY (pre-registered minimum effect)

Even where the primary rule is met, the effect is only reported as
practically meaningful when the Brother receipt's median correct-decision
time is at least 20 percent lower than the better of the two baselines'
median correct-decision times. A win that clears the primary rule by less
than 20 percent is reported as a win, but a small one, not rounded up to
the headline claim.

## If Brother misses the rule

If the trial runs, at least five distinct reviewers complete it, and the
primary rule above is not met: the old trial (this one, as specified) is
never changed after the fact to manufacture a pass. Instead the product is
improved on its own merits and a new, independently pre-registered trial is
authored later, with its own frozen rule written before it runs. Nobody
edits this file, the packets, or the scoring rubric to fit a result that
already happened.

## Until then

Until at least five distinct reviewers have completed the trial and
`scripts/acceptance_time.py score` has been run against their results:
ACCEPTANCE TIME IS NO-DATA. No number from this rule may be reported as a
result before that run exists, and no session, human or model, may
estimate, sample, or invent reviewer behaviour to fill the gap.
