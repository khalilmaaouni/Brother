# Safe Unwatched Time benchmark harness (row S10)

Row S10's done_check: "a benchmark run reports a Safe Unwatched Time figure
per workload family together with the four preservation checks (false
greens, scope drift, unrecoverable state, repeated mistakes), and refuses to
report a duration for any family where one of the four is unmeasured."

This directory is that harness. It does not reimplement the metric:
`scripts/safe_unwatched_time.py` already computes a span, a break kind and a
unit count from one run directory's own `journal.jsonl`, `claims.json` and
receipt record (definition and the four BREAK conditions it reads are in
`benchmarks/SAFE-UNWATCHED-TIME.md`). `run_benchmark.py` calls that
instrument once per workload family and adds the layer the row's done_check
asks for on top: grouping runs by family and gating the reported duration on
four PRESERVATION checks.

## Run it

```
python3 benchmarks/safe-unwatched-time/run_benchmark.py
python3 benchmarks/safe-unwatched-time/test_run_benchmark.py -v
```

Exit 0 always from `run_benchmark.py`: like the instrument it wraps, this is
a reporter, not a gate, and NO-DATA is a complete, correct answer.

## The twelve workload families

`docs/plan/SWITCHING-STRATEGY-2026-09-04.md` section 19 names twelve
workload families for the Competitive Certification Suite. This harness reads
that numbered list once into `FAMILIES` in `run_benchmark.py`, and reads
`docs/plan/GAUNTLETS-2026-09-05.md`'s "twelve workload families against
fixtures that exist" table for which family, if any, that document says is
backed by a real run directory under `docs/plan/runs/`.

Only two are: family 6 (parallelizable multi-component change) by
`parallel-scheduling-adversity-2026-09-04`, and family 7 (crash during
execution) by `live-autonomous-adversity-2026-09-04`. The other ten cite a
script, a corpus, or (family 3) a discarded trial the table itself calls "a
run record, not a fixture", or nothing at all. This harness never invents a
mapping GAUNTLETS-2026-09-05.md does not state, so those ten always read
NO-DATA with the reason "no real run directory is documented for this
workload family".

Five more real run directories exist on this machine with a complete
`journal.jsonl` + `claims.json` + receipt record
(`decomposition-adversity-2026-09-04`, `scope-auditing-adversity-2026-09-04`,
`serial-integration-adversity-2026-09-04`,
`verification-repair-adversity-2026-09-04`, `i3-loom-2026-09-04/run`), but
GAUNTLETS-2026-09-05.md's table does not tie any of them to a numbered
family either. The harness reports them in a separate "additional real run
directories" section, never folded into a family they were not assigned to.

## The four preservation checks, and why every one of them refuses today

The done_check names four checks, not the instrument's own four break
conditions (they overlap but are not the same list). Each is implemented in
`run_benchmark.py` as its own function, with the reasoning inline:

| Check | Measurable from a real run today? | Why |
|---|---|---|
| **false_greens** | Yes | `claims.json` gives a complete census of every closed unit's own exit code, and a receipt tally gives a complete proven/unproven count. Both are full counts, not samples, so their presence really does mean the whole record was checked. Maps to break kinds 1 (REFUTED) and 2 (UNPROVEN) of `SAFE-UNWATCHED-TIME.md`. |
| **scope_drift** | Only when it fires | `integrate.refused` records a REAL violation when one happens (break kind 3, SCOPE). But zero such events is silence, not proof: no event kind in this journal vocabulary records "scope was checked and stayed clean", so an unviolated run cannot be told apart from one where scope tracking never engaged. Confirmed empirically: `decomposition-adversity-2026-09-04` has zero `integrate.refused` events in its journal, and nothing in the record says whether that is because nothing was checked or because everything passed. |
| **unrecoverable_state** | No | `benchmarks/gauntlets/long-horizon-recovery.json` states this directly, in its own entries: RECOVERY TIME reads `"status": "partial"`, and HUMAN INTERVENTIONS reads `"instrument": "NO INSTRUMENT YET"`. No journal event kind or claim field carries a recoverability verdict anywhere in this repo. |
| **repeated_mistakes** | No | REPEATED FAILURE is named as a raw metric in `docs/plan/SWITCHING-STRATEGY-2026-09-04.md` section 19, but no script, journal event kind, or claim field on this estate computes it per run. Grepped for and not found in `scripts/journal.py`'s event vocabulary. |

Because at least one of the four is always unmeasured for every real run on
this machine today, **every family this harness can reach reports NO-DATA**,
never a duration. That is the honest state of the instrumentation on
2026-09-13, not a bug in the harness: closing the gap means building a
recoverability instrument and a repeated-mistake counter first, and giving
scope tracking a positive "checked and clean" event, none of which exist yet.

## The fixture

`fixtures/synthetic-complete-example/` is a hand-built, clearly-labeled
**synthetic** run directory: 90 simulated minutes, two units, no breaks. It
is never read by `scripts/safe_unwatched_time.py`'s own test suite and is
not a real Brother run. Its `preservation.json` (a file this harness invents
and no real Brother engine writes; grep confirms no directory under
`docs/plan/runs` has one) supplies the recoverability and repeated-mistakes
signal no real run can supply today, purely to prove the computation path:
when all four checks DO have a signal, `run_benchmark.py` correctly reports
a real duration (`90.0 min over 2 units`) with `all_preserved: true`.

`test_run_benchmark.py`'s backwards-drive test removes one key from that
fixture's `preservation.json` and confirms the SAME fixture then refuses,
proving the pass is a property of the signal, not of the directory merely
existing.

## What this harness does not do

- It does not run a new Brother session to generate fresh evidence. It reads
  what already exists under `docs/plan/runs`.
- It does not compute a "consequence level" per family (`SAFE-UNWATCHED-TIME.md`
  states plainly that consequence is a property of the workload family
  declaration, not derivable from a journal, and is not measured here).
- It does not guess a family-to-run mapping beyond what
  `docs/plan/GAUNTLETS-2026-09-05.md` already states in writing.
