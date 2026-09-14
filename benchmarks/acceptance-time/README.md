# The Acceptance Time benchmark harness (roadmap row S11)

This directory is the path roadmap row S11 names for the harness. The
protocol it implements is `benchmarks/ACCEPTANCE-TIME.md` (read that first);
the frozen success rule is
`benchmarks/gauntlets/acceptance-compression/SUCCESS-RULE-FROZEN.md`. Read
both before touching anything here.

**This harness does not run the trial.** Nothing on this estate times a
human, and the trial needs real reviewers, which is the founder's own work.
What lives here prepares the packets a reviewer reads and scores the results
CSV a reviewer fills in. As of today no real reviewer has run this trial, so
ACCEPTANCE TIME IS NO-DATA and stays NO-DATA until at least five distinct
reviewers complete it.

## Reuse, not a second implementation

Every piece of logic that already exists is imported here, never
retyped:

- `scripts/acceptance_time.py` (`prepare`, `score`): the three fixed seeded
  changes, packet writing (real git diff, generated ordinary summary,
  receipt via `scripts/receipt_door.py`), and the honest-floor / missing-arm
  refusal logic.
- `scripts/acceptance_trial_assign.py` (`assign`, `validate`): the
  Latin-square rotation (each reviewer sees each change exactly once, under
  exactly one condition, conditions balanced by rotation) and results-CSV
  validation.

`run.py` in this directory is a thin wrapper that calls both in one command
and adds exactly one thing neither already does: reading out the two
narrative measures (defects found, unnecessary lines inspected) per
condition once a real comparison is being reported. It duplicates none of
their decision logic.

## The results-file format

A results CSV has one row per reviewer per change, and these columns
(`scripts/acceptance_trial_assign.py`'s `CSV_COLUMNS`):

| column | meaning | filled by |
|---|---|---|
| `reviewer` | reviewer id, e.g. `reviewer-3` | `assign` |
| `change` | one of `medium-feature`, `auth-security`, `schema-migration` | `assign` |
| `condition` | one of `raw_diff`, `ordinary_summary`, `brother_receipt` | `assign` |
| `seconds` | elapsed seconds from first sight to written decision | reviewer, by hand |
| `decision` | one of `accept`, `reject`, `ask` | reviewer, by hand |
| `defects_found` | free text, narrative only, never scored | reviewer, by hand |
| `lines_inspected` | free text, narrative only, never scored | reviewer, by hand |

`assign --out-csv` (or `run.py prepare`) writes this file with the first
five columns filled in and the last four left blank for the reviewer.
`defects_found` and `lines_inspected` are narrative because
`benchmarks/ACCEPTANCE-TIME.md` is explicit that no instrument on this
estate scores prose today; they are read by hand, and `run.py score` reads
them back out verbatim, never aggregates them into a number.

## Usage

```
# From the repository root.

# Write the nine packets plus a 5-reviewer Latin-square assignment and
# blank results template into one directory:
python3 benchmarks/acceptance-time/run.py prepare /tmp/acceptance-time-packets 5 --seed 0

# Hand each reviewer only the one packet the table names for them, per
# change, plus benchmarks/acceptance-time/../ACCEPTANCE-TIME.md's reviewer
# instructions (also written as INSTRUCTIONS.md in the output directory).

# Once every reviewer has filled in seconds/decision (and, if they want,
# the two narrative columns) in results.csv, validate before scoring:
python3 scripts/acceptance_trial_assign.py validate /tmp/acceptance-time-packets/results.csv

# Only once that prints "clean", score it:
python3 benchmarks/acceptance-time/run.py score /tmp/acceptance-time-packets/results.csv
```

`score` refuses (non-zero exit, `NO-DATA` printed, no comparison reported)
when the results CSV holds fewer than five distinct reviewers, or when any
one of the three conditions has no rows at all. A missing arm is a refusal,
not a footnote: printing NO-DATA next to two working arms and exiting 0
would look, to anything reading exit codes, exactly like a real three-arm
comparison.

## Proving the refusal and the reporting, both

`test_run.py` in this directory is the runnable self-check. It proves two
things against fixtures under `fixtures/` (all clearly labeled synthetic,
never real trial data, see `fixtures/README.md`):

1. `score` on a results CSV missing all three arms, missing two of three,
   and missing one of three all refuse: non-zero exit, `NO-DATA` printed,
   no per-arm comparison claimed.
2. `score` on a fully synthetic, fully labeled five-reviewers-per-arm CSV
   computes and prints all four fields per condition: median reviewer time,
   correctness rate, and the narrative defects-found / lines-inspected
   entries, read out per reviewer.

Run it: `python3 benchmarks/acceptance-time/test_run.py -v`

## What this does not measure

Nothing here has run a human trial. See `benchmarks/ACCEPTANCE-TIME.md`'s
own "What this does not measure" section: the founder scheduling and
running a real panel of at least five reviewers is what turns Acceptance
Time from NO-DATA into a real comparison. This harness is the protocol and
the tooling the trial runs through, not the trial itself.
