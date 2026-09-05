# The five Brother owned gauntlets: frozen specifications

Section 20 of the switching strategy
(`~/.claude/evidence/SWITCHING-STRATEGY-2026-09-04.md`) names five gauntlets
this project owns rather than borrows. This directory holds one frozen JSON
specification per gauntlet, plus the validator that keeps them honest. The plain
reading of all five, with the metric and fixture gaps stated, is
`docs/plan/GAUNTLETS-2026-09-05.md`.

This file follows the shape of its sibling
`benchmarks/ja-adversarial/ADVERSARIAL-JA-RESULT-2026-08-31.md` (that directory
has no README of its own): what is here, what state it is in, what is honest
about the scope, and a Reproduce section whose commands run as written.

## What is here

| File | Gauntlet | State |
|---|---|---|
| `delegation-truth.json` | Delegation Truth (20.1) | SPECIFIED, RUN (`scripts/gauntlet_delegation_truth.py`) |
| `long-horizon-recovery.json` | Long-Horizon Recovery (20.2) | SPECIFIED, NOT YET RUN |
| `acceptance-compression.json` | Acceptance Compression (20.3) | SPECIFIED, NOT YET RUN |
| `memory-recurrence.json` | Memory Recurrence (20.4) | SPECIFIED, NOT YET RUN |
| `hostile-japanese-identity.json` | Hostile Japanese Identity (20.5) | SPECIFIED, PARTLY RUN |
| `validate.py` | the checker below | runs today |

Every spec carries the same fields: the workload families it draws from by
section 19's own numbering, the seeded conditions copied from section 20 without
addition, the fairness controls that apply, the raw metrics it measures from
section 19's fifteen, a scoring rubric marked fixed before execution, the win
condition from section 7, and for every metric either the instrument on this
estate that measures it today (a file path and the roadmap row that proved it)
or the exact sentence NO INSTRUMENT YET with what would have to exist.

## The state of the specifications, honestly

No gauntlet has been run under this specification. The Japanese one reads PARTLY
RUN because its corpus and harness exist and scored once on 2026-08-31 and again
after the fix (row E4), but its mutation arm and its fresh qualification arm
have no harness at all.

Of the 27 metric entries across the five specs, 22 name a real instrument and 5
say NO INSTRUMENT YET. Counted over section 19's fifteen distinct metrics rather
than over entries, six are measured today, five are partially measured, and four
have no instrument: HUMAN INTERVENTIONS, ACCEPTANCE TIME, ACCEPT/REJECT ACCURACY
and SAFE UNWATCHED DURATION. Three of those four need a human being timed and
scored, which nothing on this estate does.

## How a run is executed

A gauntlet run is a sequence of rounds, one per workload family the spec names,
and it is driven by hand and recorded as it happens, the way
`docs/plan/HEAD-TO-HEAD-PROTOCOL-2026-08-30.md` drove the head-to-head rounds.
Four of the five have no runner script, and inventing one before a first
run would be guessing at the shape. The exception is Delegation Truth,
which has one: `scripts/gauntlet_delegation_truth.py` (row S9) seeds its
cases as throwaway repositories, runs the estate's own door on each one
and reports a false-green rate with a dated record under
`benchmarks/results/`. Its seeded-condition arms are automated; the
hand-driven rounds below still govern the rival comparison, which no
script here runs.

1. Freeze first. Read the spec. The task instruction and the scoring rubric are
   already in it and must not be edited from here on. A rubric edited after a
   round invalidates that round.
2. Record the fairness controls, all seven, into the run record before anything
   runs: the starting repository commit, the model, the token budget, the
   machine, the maximum human interventions, and that the rubric was already
   frozen. A control the round cannot honour is written down as unhonoured.
3. Run each round through the product's own public surface only. No internal
   command, no hand editing of the code under test.
4. Capture everything, read a slice. Use `python3 ~/Brother/scripts/run_evidence.py
   <command>`, which writes the whole output to `~/.claude/evidence` and returns
   the command's own exit code, rather than trimming output at capture time.
5. Score against the rubric, rule by rule. Any measure the round cannot fill is
   written NO-DATA with its reason, never left blank and never assumed.
6. Write the record. Then, and only then, write any summary.

## Where the raw artifacts land

Each spec names its own root under `raw_artifacts`. The convention is
`docs/plan/runs/gauntlet-<id>-<date>/`, beside the adversity runs that already
live there, and the Japanese one also keeps its corpus and its result document
in `benchmarks/ja-adversarial/`.

A run directory holds, at minimum, a `RECORD.md` in the shape of
`docs/plan/runs/live-autonomous-adversity-2026-09-04/RECORD.md` (the pinned
harness, the exact command, the injected fault, the observed behaviour, and a
verdict that states its own caveats), plus the run's own untouched artifacts:
`run.log`, `journal.jsonl`, `claims.json` and the delivery report. The specs list
what else each gauntlet needs.

The rule that governs publication is section 19's, and it is quoted in every
spec: publish raw artifacts, do not publish only a synthetic score.

## Reproduce

From the repository root:

```
python3 benchmarks/gauntlets/validate.py
# -> 5 spec(s), 27 metric entr(ies): 22 with an instrument, 5 NO INSTRUMENT YET
# -> PASS: every spec is complete and every cited path exists

python3 benchmarks/gauntlets/validate.py --selftest
# -> PASS: 9 broken spec(s) refused, one clean spec accepted
```

The validator refuses a spec that has lost a required field, that names a metric
outside section 19's fifteen, that drops a fairness control without a reason,
that claims an instrument without naming the row that proved it, that says a
metric is unmeasured without the exact sentence NO INSTRUMENT YET and what would
have to exist, or that cites any file path which is not on disk. It exits 1 and
names each problem. `--selftest` drives all of those backwards against a spec
broken on purpose, because a control nobody drove backwards is a claim rather
than a control.
