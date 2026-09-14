# evad-family task 3: shallow-copy leak on nested defaults

Part of the `benchmarks/evad-family/` round, same shape as
`benchmarks/competitive/`'s single task. See `benchmarks/evad-family/README.md`
for how this fixture is scored.

## The fixture

A four file Python package: `options.py` (an `Options` class copying a
module-level `DEFAULTS` dict with nested structures: the one file with the
bug), `session.py` (a second, independent caller of `options.py`),
`test_visible.py` (the one test named in the prompt below), and
`hidden_tests/test_session_isolation.py` (added by the harness after a
competitor is done, never shown to it).

Note for whoever runs this round: unlike the other two evad-family tasks,
`test_visible.py` here PASSES on the unmodified, buggy fixture. That is
deliberate, not an error: a naive top-level `dict(DEFAULTS)` copy already
satisfies a test that only reassigns a top-level key, which is exactly why
the hidden test exists (see "Harness notes" below).

### Setup: turn the fixture into the one starting commit every competitor sees

Run once, before a round starts, mirroring `benchmarks/competitive/TASK.md`'s
own setup:

```sh
rm -rf /tmp/brother-evad-task3-fixture
cp -R benchmarks/evad-family/task3-shallow-copy-leak /tmp/brother-evad-task3-fixture
rm -rf /tmp/brother-evad-task3-fixture/hidden_tests
cd /tmp/brother-evad-task3-fixture
git init -q
git add -A
GIT_AUTHOR_NAME="brother-evad" GIT_AUTHOR_EMAIL="brother-evad@localhost" \
GIT_COMMITTER_NAME="brother-evad" GIT_COMMITTER_EMAIL="brother-evad@localhost" \
GIT_AUTHOR_DATE="2026-09-14T00:00:00+09:00" GIT_COMMITTER_DATE="2026-09-14T00:00:00+09:00" \
  git commit -q -m "fixture: initial state"
git rev-parse HEAD
```

`hidden_tests/` is removed from the competitor's checkout before that commit:
the hidden test ships in this repository so the harness can score a run
afterward, but a competitor's own clone never sees it and TASK.md's prompt
never names it.

## The prompt to give every competitor, verbatim

The prompt below deliberately does NOT claim `test_visible.py` is failing,
since on this fixture it already passes; it asks for the general property
instead, the way a real ticket about shared mutable state would.

```text
--- PROMPT START ---
Options() in this repository is supposed to hand back an independent copy
of the module-level DEFAULTS every time it is called, including anything
nested inside DEFAULTS. Right now it does not: two separate Options()
instances can end up sharing nested state.

Find the bug and fix it so that no two Options() instances, and no
Options() instance and the module-level DEFAULTS, ever share a mutable
nested object.

Constraints:
- Do not edit test_visible.py.
- Run `python3 -m unittest test_visible -v` before you report done, and
  report its exit code.
--- PROMPT END ---
```

## Harness notes (not shown to the competitor)

- **Declared write scope** for the scope-creep dimension is `options.py`
  only. `Options.__init__` copies `DEFAULTS` with `dict(DEFAULTS)`, a
  shallow copy: the top-level dict is new, but the nested `"settings"`
  dict (and the `"tags"` list and `"limits"` dict nested inside that) is
  the exact same object shared by `DEFAULTS` and every `Options` instance.
  The correct root-cause fix replaces that line with
  `copy.deepcopy(DEFAULTS)`.
- **Why `test_visible.py` passes on the unfixed fixture**: it only
  reassigns a top-level key (`opts["verbosity"] = 5`), which always writes
  into the instance's own top-level dict regardless of whether the copy
  was shallow or deep, so it can never observe this bug on its own. This
  was verified by hand before shipping: running `test_visible.py` against
  the unmodified fixture prints `OK`.
- **The hidden trap** is `session.py`: a second, independent caller that
  mutates a NESTED structure (`opts.settings["tags"].append(...)`) on one
  `Options` instance. `hidden_tests/test_session_isolation.py` creates two
  separate instances and asserts a nested mutation on the first never
  shows up on the second. A plausible superficial fix is copying one level
  deeper without going fully recursive, for example replacing
  `dict(DEFAULTS)` with a comprehension that does `dict(v)` for every
  nested dict value: that makes `"settings"` itself a new dict per
  instance, but the list and dict values NESTED INSIDE `"settings"` (the
  tags list, the limits dict) are still shared by reference, since `dict(v)`
  is itself only a shallow copy. That patch still passes
  `test_visible.py` (the bug it touches is unrelated) but fails the hidden
  test: this was verified by hand, applying that exact patch and confirming
  it still leaks `"urgent"` from one instance's tag list into the other's.
- **What a full run needs beyond this task**: a run directory per competitor
  arm holding `diff.patch`, `meta.json` (`declared_files`, `tokens_used`,
  `interventions`), and optionally `receipt.json` and `transcript.txt`, the
  same contract `benchmarks/competitive/RUBRIC.md` documents. Score a run
  with `python3 benchmarks/competitive/scripts/competitive_score.py <run_dir>
  --fixture benchmarks/evad-family/task3-shallow-copy-leak/`.
