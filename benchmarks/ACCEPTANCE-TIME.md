# The Acceptance Time benchmark (S11)

Section 7 zone 5 of the switching strategy names Acceptance Time as "time
required for a competent reviewer to reach the correct accept/reject
decision." Section 20.3 (`benchmarks/gauntlets/acceptance-compression.json`)
freezes the three arms and the fairness controls this benchmark reuses
without addition. This document is the protocol for the human trial and the
harness that prepares and scores it. It does not run the trial: nothing on
this estate times a human, and the trial itself needs real reviewers, which
is the founder's own work.

## The frozen success rule (E1)

`benchmarks/gauntlets/acceptance-compression/SUCCESS-RULE-FROZEN.md`, dated
2026-09-05, is the pre-registered rule this trial is scored against: it was
written before any packet was assigned to a reviewer, and it is not edited
after the trial starts. `scripts/test_acceptance_trial_assign.py` checks
this file's own SHA-256 so a later silent edit is detectable. Until at
least five distinct reviewers complete the trial and
`scripts/acceptance_time.py score` runs against their results: ACCEPTANCE
TIME IS NO-DATA. Nobody, human or model, estimates or invents a reviewer's
decision or time to fill that gap.

## The three conditions

For the SAME delivered change, one reviewer sees exactly one of:

1. **raw diff**: the plain `git diff` of the change, nothing else.
2. **ordinary agent summary**: a prose summary of the change with no
   per file check, the way an agent without a receipt door would describe
   its own work.
3. **Brother receipt**: the delivery record `scripts/receipt_door.py`
   builds, with its REVIEW FIRST, NOT PROVEN, LOW-RISK MECHANICAL and NO
   NEED TO RE-READ ordering.

Each reviewer sees each condition exactly once, across the three changes,
order rotated (a Latin square over reviewer x change), so no reviewer reads
the same change twice and no condition always lands first.

## What is timed

Seconds from first sight of the presentation to a written accept or reject
decision. The clock is the reviewer's own: this harness prepares the three
packets and scores the results a human records, it does not instrument a
screen.

## What is scored

- **Decision correctness**: every one of the three fixed changes carries
  exactly one seeded defect, so the ground truth decision is always
  REJECT. A reviewer who accepts a defective change is wrong; there is
  nothing to net off. Correctness rate per condition is the fraction of
  reviewers who rejected.
- **Time**: reported per condition as a distribution (median here, because
  a small trial's mean hides its spread), never as a single number folding
  every change together.
- Defects found and unnecessary lines inspected (section 20.3's own extra
  measures) are recorded by the reviewer in free text and are read by hand;
  no instrument on this estate scores prose today, so those two columns are
  narrative only.

## The fixed set of changes

Three changes, one per workload family the spec names, each with one seeded
defect, adapted from the acceptance-compression spec's own cases:

| id | family (n) | files | seeded defect |
|---|---|---|---|
| `medium-feature` | 2, medium multi-file feature | `src/signup/handler.py`, `src/signup/validate.py` | `is_strong_password` returns True for a password under the minimum length: the strength check is inverted, so weak passwords pass. |
| `auth-security` | 4, authentication/payment/security change | `src/middleware/rate_limit.py`, `requirements.txt`, `docs/generated/api-index.html` | the rate limiter grants an unconditional bypass when a request carries the header `X-Debug: 1`, the exact middleware/dependency/generated-file mix `acceptance-compression.json`'s own case names. |
| `schema-migration` | 5, schema/data migration | `migrations/0007_add_last_login.sql`, `src/models.py` | the migration adds `last_login TIMESTAMP NOT NULL` with no `DEFAULT` on a table that already holds rows, which fails or locks against live data. |

The defect location is never shown inside a presentation packet; it lives
only in `acceptance_time.py`'s own `CHANGES` table and is published in the
run record only after the trial, per the frozen spec's raw_artifacts rule.

## Counterbalancing

Each reviewer sees each condition once, never twice, and never the same
change under two conditions. With three changes and three conditions, a
run of reviewers is assigned in rotating triples (reviewer i gets condition
`CONDITIONS[(i + k) % 3]` for change k) so across the whole panel every
condition is read against every change roughly equally often.

## The honest floor

A round, or the run as a whole, with fewer than five reviewers reports
NO-DATA and is excluded from every total. Five is not a statistically
sufficient trial; it is the floor below which a median is not worth
publishing. `scripts/acceptance_time.py score` enforces this floor
mechanically: fewer than five distinct reviewers in the results CSV prints
NO-DATA and exits 3, never a comparison built on too few readings.

## The harness

`scripts/acceptance_time.py` has two verbs:

- `prepare <out dir>`: writes the three condition packets for each of the
  three fixed changes into `<out dir>/<change id>/{raw_diff,
  ordinary_summary, brother_receipt}.txt`, plus `INSTRUCTIONS.md` (the
  plain language reviewer instructions: accept, reject or ask; record
  start and end time; do not consult the other packets) and
  `INSTRUCTIONS-FOUNDER.md` (the exact commands for assigning reviewers,
  validating their results, and scoring, once results arrive) once per
  directory, never once per packet. The raw diff comes from a real
  fixture commit (a throwaway git repository built and torn down for the
  purpose); the Brother receipt is generated through
  `scripts/receipt_door.py`'s own `receipts_for` / `reading_order` /
  `receipt_record` seam, the same one `scripts/test_receipt_door.py` and
  `scripts/test_acceptance_compression.py` drive, so no receipt here is
  hand typed. If a receipt cannot be built for a change, the packet holds
  `NO-DATA` and the reason, and the run never fabricates a comparison.
- `score <results csv>`: reads rows of `reviewer,change,condition,seconds,
  decision` and prints, per condition, the median seconds and the
  correctness rate. Fewer than five distinct reviewers prints NO-DATA and
  exits 3.

`scripts/acceptance_trial_assign.py` has the two verbs the founder runs
once packets exist (E3):

- `assign <n reviewers> [--seed N] [--out-csv PATH]`: prints the
  counterbalanced assignment table (the same rotation this document's
  Counterbalancing section names) for the given reviewer count, refusing
  below the five reviewer floor. `--out-csv` also writes the blank results
  template CSV in the exact columns `score` expects, one row per
  reviewer per change, `seconds` and `decision` left for the reviewer to
  fill in.
- `validate <results csv>`: refuses a completed results CSV that is
  missing a time, carries an impossible time (zero, negative, non
  numeric, or over a four hour plausibility ceiling), has an
  unrecognized decision, or lets one reviewer see the same change twice.
  Prints every problem found; `score` should only ever run against a CSV
  this prints clean on.

## Reproduce

```
python3 scripts/acceptance_time.py prepare /tmp/acceptance-time-packets
# -> 11 files: 9 packets (3 per change) plus INSTRUCTIONS.md and
#    INSTRUCTIONS-FOUNDER.md, under /tmp/acceptance-time-packets/

python3 scripts/acceptance_trial_assign.py assign 5 --seed 0 \
    --out-csv /tmp/acceptance-time-packets/results.csv
# -> the assignment table, plus a blank results template CSV

python3 scripts/test_acceptance_time.py -v
python3 scripts/test_acceptance_trial_assign.py -v
```

## What this does not measure

Nothing here has run a human trial. The `done_check` on roadmap row S11
("a benchmark run reports, for each of the three arms, reviewer time,
correct accept or reject, defects found and unnecessary lines inspected, on
the same seeded diff") needs a real panel of at least five reviewers
reading real packets and recording real seconds; that scheduling and
execution is the founder's own work, per row S11's own `why_now`. This
document and `scripts/acceptance_time.py` are the protocol and the harness
the trial runs through, not the trial itself.
