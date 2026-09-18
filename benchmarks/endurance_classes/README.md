# Endurance classes (roadmap row DOM-20.05)

This directory is the path `docs/plan/ORCH-1020-WBS.json` unit DOM-20.05
names for the corpus. The harness that reads it is
`scripts/endurance_classes.py`; read its module docstring for the exact
outcomes it reports.

## The objective: score useful units, not elapsed time

An overnight run's wall-clock length says nothing about whether it did
anything. `scripts/safe_unwatched_time.py` and
`scripts/useful_unwatched_work.py` already draw this distinction for a
single span (how long, versus how long backed by verified work); this row
extends it into eight fixed dimensions per run, scored across a corpus of
real run directories:

```
useful_units, idle_minutes, retries, abandoned_approaches,
context_resets, crashes, interventions, scope_incidents
```

## The rule that makes this honest: NO-DATA is never a zero

A field this codebase has no instrument for today (`retries`,
`abandoned_approaches`, `context_resets`, `crashes`, as of this change) is
reported as the literal string `"NO-DATA"`, never as `0`. A field that was
actually counted and genuinely came out to zero is reported as `0`. The
two must never look the same: a run that silently retried five times and
a run that never retried at all must not both read as `0` just because
nothing today records a retry.

`useful_units` and `idle_minutes` come from
`scripts/useful_unwatched_work.py`'s own `measure()` (accepted units, and
the span minutes not backed by any accepted unit). `interventions` comes
from `scripts/intervention_events.py`. `scope_incidents` is counted
directly from a run's own `journal.jsonl`, by `endurance_classes.py`'s own
`scope_incidents_in_journal()`, matching the same `integrate.refused`
scope-violation wording `safe_unwatched_time.py` already recognizes
elsewhere in this codebase.

## What "a run" is, in this directory

A qualifying run is an immediate subdirectory of `runs/` that contains a
file literally named `journal.jsonl`. `scripts/endurance_classes.py`'s
`score_corpus(corpus_dir)` scores every qualifying subdirectory and
silently skips anything else it finds at that level.

## What ships here today

`runs/` is empty except for `runs/README.md`. No run directory has been
fabricated here: freezing a real overnight run into this corpus is future
work once one exists to freeze, and a harness over empty fixtures reports
NO-DATA rather than a pass, exactly as
`python3 scripts/endurance_classes.py benchmarks/endurance_classes/runs`
does today.

## Usage

```
python3 scripts/endurance_classes.py benchmarks/endurance_classes/runs
```
