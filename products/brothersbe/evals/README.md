# BrotherSBE regression evals

Each eval is a real failure class as a fixture, a planted defect, and an
assertion that the matching hard gate catches it. Run them:

```
python3 evals/run_evals.py
python3 evals/test_no_data_class.py
```

A release is blocked if any eval regresses (the script exits nonzero). This is
the mechanism behind the claim that the gates are proven, not asserted: they are
tested against the exact defect classes the operating record produced. Every
fixture is generalized and carries no private data.

The meta-test also has a seeded generative mode: `--seed N` composes the same
hollowing operations at random depths and combinations over each check's own
worked example, still asserting never PASS, and prints the seed in every
scenario id so a finding reproduces. The shipped CI runs a small fixed set of
seeds. What this mode does not do: invent new emptiness values or turn the
sweep into a proof; it widens the search over shapes nobody imagined, nothing
more.

The resulting calibration is published rather than implied:
[INVARIANTS.md](../INVARIANTS.md) records one representative defect reinjection
per shipped check, the eval case that caught it, the date and the command. That
record re-runs with every suite run, so it can go stale only by the suite going
red; it does not claim coverage of inputs no fixture plants.

## The honesty meta-test

`test_no_data_class.py` is not a list of cases. It discovers every check
registry by walking every `.py` file under `tools/`, at any depth, rather than by
naming files: naming files by prefix was itself a defect, because a registry
added in a new file or a new package sat outside a fixed list and was never run.
For each check found, it derives its scenarios from that check's own declared
worked fixture (the WORKING example the check ships), hollowed out mechanically
in every way a value can be empty: an empty directory, evidence that declares
zero items, valid JSON of the wrong shape, malformed JSON, and further sweeps
over subtrees, leaves, lists, and booleans. A check cannot be registered without
declaring what it reads and what its empty state is, and `sbe_checks.Check`
refuses to construct one whose empty state is PASS. A check added later is
covered without anyone remembering to add it.

It also accepts `--tools <dir>`, which runs the same scenarios against a
different copy of the tools. That is how the before-list was measured: the
scenarios are data held in the test, the code under test is whatever the flag
points at. A test that can only run against the fixed code proves nothing about
what it caught.

## What a fixture has to earn

A fixture that passes against the code from before the fix pins nothing: it
cannot fail, so it inflates the count without protecting anything. Every fixture
added for a behaviour change is measured against the previous tree, and the two
kinds that are kept anyway are kept on purpose and named here:

- **Positive-path guards.** `two-good-manifests-still-pass` and
  `an-invented-node-is-still-an-orphan-with-components-declared` cannot fail
  against older code, because older code passed everything. They exist because a
  gate that starts blocking honest work gets switched off by its users, which
  destroys the system more thoroughly than a gate that misses a defect. They
  guard the relaxations, not the tightenings.
- **Decision tables.** The `%G?` signature matrix (G, U, B, X, N) documents a
  mapping that was already right around the one case that was wrong (E). Keeping
  the whole matrix is how the mapping stays readable as a set.

Everything else has to fail against the previous tree, and the measurement is
part of the merge note, not a claim.
