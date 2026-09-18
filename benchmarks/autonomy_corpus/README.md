# The autonomy corpus (roadmap row DOM-20.04)

This directory is the path `docs/plan/ORCH-1020-WBS.json` unit DOM-20.04
names for the corpus. The harness that reads it is
`scripts/autonomy_corpus.py`; read its module docstring for the exact
outcomes it reports.

## What a workload is

A workload is one JSON file describing a long-horizon task an unattended
agent would work on for an extended stretch: it decomposes into more than
one independent unit, and it carries deliberately planted faults, so a
benchmark can later measure whether a run notices and recovers from them
rather than only whether it finishes.

## The schema, one workload per JSON file

```
{
  "workload_id": "unique across the whole corpus, non-empty",
  "title": "non-empty",
  "units": [
    {
      "unit_id": "unique within this workload, non-empty",
      "objective": "non-empty",
      "depends_on": ["other unit_id values in this workload; optional, may be omitted or empty"]
    },
    ... at least 2 entries
  ],
  "injected_failures": [
    {
      "failure_id": "unique within this workload, non-empty",
      "kind": "free text naming the fault category, e.g. stale-cache, wrong-branch",
      "target_unit": "must equal one of this workload's unit_id values",
      "description": "non-empty: what was deliberately broken"
    },
    ... at least 1 entry
  ]
}
```

Two rules decide whether a file is valid, beyond the field-level checks:

- **Independence.** At least 2 of the units must have a completely empty
  (or omitted) `depends_on`, meaning they do not depend on any other unit
  in the same workload. A workload where every unit but one sits in a
  single dependency chain fails this rule, even if it has 3 or more units
  total: a long horizon task made of one serial chain is not the same
  shape as one made of independent units, and the corpus only wants the
  second shape.
- **No empty-handed workload.** `injected_failures` must hold at least one
  entry. A workload with units but zero planted faults is refused, never
  silently accepted as a workload with no faults to find.

## Three outcomes, never conflated

`scripts/autonomy_corpus.py`'s `load_corpus(corpus_dir)` reports one of
three shapes, read `report_line(result, exit_code)` for the exact wording:

1. **NO-DATA** (exit 2 or 3): the directory does not exist, is not a
   directory, holds zero `.json` files, or could not even be listed. This
   is the outcome for this directory as shipped: `workloads/` below is
   empty on purpose.
2. **REFUSED** (exit 4): the directory has content, but at least one file
   violates the schema above, or two files declare the same
   `workload_id`. The whole load stops; no partial, unverified corpus is
   ever returned.
3. **a real pass** (exit 0): every file is valid and every `workload_id`
   is unique, and the parsed, validated workload list comes back.

## Usage

```
python3 scripts/autonomy_corpus.py benchmarks/autonomy_corpus/workloads
```

## What ships here today

`workloads/` is empty except for `workloads/README.md`. No fabricated
workload has been written into this corpus: the row's own note in the WBS
is explicit that a harness over empty fixtures must report NO-DATA rather
than a pass, and that is exactly what running the command above against
this shipped directory does. Freezing a real workload here, with a real
long-horizon shape and real planted faults, is separate future work; this
change is the schema and the loader that will validate it when it exists.
