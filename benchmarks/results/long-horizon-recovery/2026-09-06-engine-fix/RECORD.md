# Long-Horizon Recovery: rerun of PR 383's own driver, against the fixed engine, 2026-09-06

Status: rerun of the exact same benchmark driver PR 383 recorded at
benchmarks/results/long-horizon-recovery/2026-09-06-resume/driver.py, run
here without any change to driver.py or product_acceptance.py, against
scripts/brother_run.py after the three row P1-2 engine fixes in this same
change (branch wbs/lhr-engine-resume-defects). This directory is the
proof that the fixes move the driver's own measures; it is not a
replacement for the 2026-09-06-resume record, which stays as PR 383 left
it.

## What this is

`driver.py` in this directory is a byte-for-byte copy of
`benchmarks/results/long-horizon-recovery/2026-09-06-resume/driver.py`
(git show origin/wbs/lhr-resume-2026-09-06:benchmarks/results/long-horizon-recovery/2026-09-06-resume/driver.py).
Its own SCRIPTS auto-resolution (walking up from its own file location
until it finds a `scripts/` directory) picked up this branch's own
`scripts/brother_run.py`, so it exercised the fixed engine without any
argument or environment variable naming it. Run as:

```
<PYTHON3> driver.py <SCRATCH_ROOT>
```

## The three defects PR 383 measured, and what this rerun shows

1. **lost_decisions**: PR 383 measured 1 (the intent screen's recorded
   default was re-taken on resume). This rerun measures **0**. The
   killed run's own log (`pre_kill_intent_lines`) still carries
   `"brother_run: intent resolved: chose 'Proceed as decomposed', ..."`;
   the resumed run's log (`post_resume_intent_lines`) carries nothing
   matching that pattern at all, because the resumed process printed
   `"brother_run: intent resolution reused from ..."` instead of
   re-deciding, exactly as scripts/brother_run.py's fix 1 (a stored
   `intent_resolution` field on the Work document, reused when the run
   is resumed) intends. The driver's own regex only matches the
   `"intent resolved:"` shape, so a reused decision correctly reads as
   "not re-taken" rather than "carried forward under a new label".

2. **false_claims_after_recovery**: PR 383 measured one named entry,
   `"A2 (printed as verified by, but its own verdict reads NO-DATA)"`.
   This rerun measures **an empty list**. The driver's own log line
   shows why: `reported=['A1'] git_merges=['A1', 'A2']`, i.e. A2 is no
   longer in the report's own `reported_ids` at all, because
   scripts/brother_run.py's fix 2 changed the delivery report's
   "integrated" block to print A2 as `"A2 integrated, NOT PROVEN: the
   check already passed before the work began, so it cannot prove the
   work"` rather than `"A2 verified by: <check>"`. The driver's
   `_integrated_ids_from_report` (product_acceptance.py, unchanged) only
   ever counted a unit as "reported integrated" from a `"verified by:"`
   line, so the fix removes the false claim at its source rather than
   requiring the driver's own after-the-fact flag to catch it. A1, which
   really is PASS, still reads `"verified by:"` and is still correctly
   counted as reported and merged.

3. **drift_detection**: PR 383 measured NO-DATA ("no engine output or
   journal event names the repository change"). This rerun measures
   `"the engine named the drift commit: 'brother_run: repository moved
   since the checkpoint: c88a366d5aea to 62c3fe249381, 1 commit(s);
   resumed units build on the new revision'"`. scripts/brother_run.py's
   fix 3 reads the checkpoint's own last-recorded canonical revision off
   `capsule.json` before anything in the resumed process can overwrite
   it, compares it against the target's live HEAD, and both prints the
   line (`log.say`, so it lands in `run.log` and on stdout) and journals
   a `run.drift_detected` event (`{"before": <checkpoint>, "after":
   <current>}`) when they differ. The driver's own drift-detection check
   (fix 6 in the original PR 383 audit) excludes the known HEAD-readback
   shapes (`"Rollback:"`, `"canonical revision before/after:"`) by name;
   the new line matches neither exclusion and carries the drift commit's
   own hash, so it is picked up as a genuine detection, not a readback.

## Measures (full table)

| Measure | PR 383 (2026-09-06-resume) | This rerun (2026-09-06-engine-fix) |
|---|---|---|
| repeated_work | 0 | 0 |
| lost_decisions | 1 | **0** |
| lost_evidence | 0 | 0 |
| wrong_resumed_state | false | false |
| human_interventions | 1 | 1 |
| recovery_time_seconds | 2.686 | 2.684 |
| false_claims_after_recovery | ["A2 (printed as verified by, but its own verdict reads NO-DATA)"] | **[]** |
| drift_detection | NO-DATA: no engine output or journal event names the repository change | **"the engine named the drift commit: ...c88a366d5aea to 62c3fe249381, 1 commit(s)..."** |
| verdict | PARTIAL | PARTIAL (unchanged: the 24 to 72 hour temporal arm, workload family 8, stays NO-DATA per the gauntlet's own scoring rubric, which this fix does not touch) |

The verdict stays PARTIAL on purpose: the temporal arm's real 24 to 72
hour gap is out of scope for this fix (this rerun's gap is the same 60
second wall-clock stand-in the original record already named as exactly
that, never a claim of the real span). Everything the three named
defects measured moved to the honest, fixed value.

## Files

`driver.py`: the unmodified copy described above.
`driver.log`: this run's own timeline, real paths scrubbed to
`<PYTHON3>`, `<BROTHER_SCRIPTS>`, `<SCRATCH_ROOT>` and `<OS_TMPDIR>`, the
same convention the 2026-09-06-resume record already used.
`result.json`: the driver's own structured output, same scrubbing.

## What this rerun does not attempt

Never a merge into main, never a change to
benchmarks/results/long-horizon-recovery/2026-09-06-resume/ (that record
is PR 383's own and is left exactly as it landed), and never a claim
about the temporal arm this fix did not touch.
