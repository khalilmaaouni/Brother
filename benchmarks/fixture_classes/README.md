# Acceptance fixture classes (DOM-30.02)

This directory holds the schema for the acceptance fixture corpus and
nothing else tonight. `scripts/fixture_classes.py` is the harness that reads
it; `scripts/test_fixture_classes.py` is its test.

## Why the corpus is empty

DOM-30.02's own note says it plainly: the harness is dispatchable now, but
the row only means something once the fixtures are real. Inventing seven
plausible-looking fixture.json files here to make a count go green would be
exactly the fabrication the estate's own rules forbid: a fixture is only
real when it carries an actual, working example of its class (a real
frontend change, a real mobile change, and so on) with a real defect seeded
into it on purpose. Nobody has built those seven real cases yet, so
`corpus/` does not exist in this commit, and the harness reports NO-DATA
against every class rather than a fabricated PASS.

## Layout, once real fixtures exist

```
benchmarks/fixture_classes/
  SCHEMA.json          the manifest schema, read by scripts/fixture_classes.py
  README.md            this file
  corpus/
    <fixture-id>/
      fixture.json      the manifest: class, seeded_defect, and any supporting fields
      ...               the fixture's own files (whatever a real example of its class needs)
```

Each `<fixture-id>` directory under `corpus/` is one fixture. Its
`fixture.json` must validate against `SCHEMA.json`: a `class` field that is
one of the seven enum values, and a non-blank `seeded_defect` describing the
real defect planted in that fixture. A directory under `corpus/` with no
`fixture.json`, or a `fixture.json` with an unrecognised class or a blank
`seeded_defect`, is a corpus defect: the harness raises rather than silently
skipping it, so a bad entry cannot quietly stop counting toward coverage.

## The seven classes

frontend, mobile, api_contract, data_pipeline, dependency_update, refactor,
matching_logic. This list is typed once, in `SCHEMA.json`, and
`scripts/fixture_classes.py` reads it from there rather than retyping it.

## Running the harness

```
python3 scripts/fixture_classes.py
```

Prints one line per class and an overall verdict: PASS only when every one
of the seven classes has at least one valid fixture, NO-DATA otherwise
(missing classes are named). NO-DATA is not a pass; the harness exits
non-zero when it reports NO-DATA, the same way it exits non-zero when it
reports NO-DATA for an entirely empty or missing `corpus/` directory.

## Adding a real fixture

Add a directory under `corpus/`, add its `fixture.json`, then re-run the
harness. Do not add a fixture whose class or seeded defect is invented for
the sake of a passing count; an entry that does not carry a real working
example of its class is worse than no entry, because it reads as covered
when nothing was actually tested.
