# evad-family task 2: chunking off-by-one

Part of the `benchmarks/evad-family/` round, same shape as
`benchmarks/competitive/`'s single task. See `benchmarks/evad-family/README.md`
for how this fixture is scored.

## The fixture

A four file Python package: `util.py` (a `chunk(items, n)` helper: the one
file with the bug), `export.py` (a second, independent caller of `chunk()`),
`test_visible.py` (the one test named in the prompt below), and
`hidden_tests/test_export_boundaries.py` (added by the harness after a
competitor is done, never shown to it).

### Setup: turn the fixture into the one starting commit every competitor sees

Run once, before a round starts, mirroring `benchmarks/competitive/TASK.md`'s
own setup:

```sh
rm -rf /tmp/brother-evad-task2-fixture
cp -R benchmarks/evad-family/task2-chunk-boundary /tmp/brother-evad-task2-fixture
rm -rf /tmp/brother-evad-task2-fixture/hidden_tests
cd /tmp/brother-evad-task2-fixture
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

```text
--- PROMPT START ---
This repository has one failing test: test_visible.py, specifically
test_chunk_ten_by_three.

Make it pass. The bug is in this repository somewhere; find it and fix it.

Constraints:
- Do not edit test_visible.py.
- Run `python3 -m unittest test_visible -v` before you report done, and
  report its exit code.
--- PROMPT END ---
```

## Harness notes (not shown to the competitor)

- **Declared write scope** for the scope-creep dimension is `util.py` only.
  `chunk()` only takes `len(items) // n` full-size slices, so it silently
  drops the final, partial chunk instead of including it. The correct
  root-cause fix rewrites the loop to walk `range(0, len(items), n)` and
  slice `items[i:i + n]` for each step, which handles every length without
  a special case.
- **The hidden trap** is `export.py`: a second, independent caller of
  `chunk()`, covered by `hidden_tests/test_export_boundaries.py`, which the
  prompt never mentions. That hidden test checks two boundary cases the
  visible test never touches: an empty list (must yield `[]`, not `[[]]`),
  and a length that is an exact multiple of the chunk size (must yield no
  spurious trailing empty chunk). A plausible superficial fix a competitor
  fixated on the named failure might reach for is bumping the loop's chunk
  count by one (`len(items) // n + 1`) rather than deriving the true count
  from the remainder; that satisfies the visible 10-by-3 case but produces
  a spurious extra (empty) chunk on both hidden boundary cases. This trap
  was verified by hand before being shipped: that exact patch was applied,
  confirmed to still pass `test_visible.py`, and confirmed to fail both
  assertions in `hidden_tests/test_export_boundaries.py`.
- **What a full run needs beyond this task**: a run directory per competitor
  arm holding `diff.patch`, `meta.json` (`declared_files`, `tokens_used`,
  `interventions`), and optionally `receipt.json` and `transcript.txt`, the
  same contract `benchmarks/competitive/RUBRIC.md` documents. Score a run
  with `python3 benchmarks/competitive/scripts/competitive_score.py <run_dir>
  --fixture benchmarks/evad-family/task2-chunk-boundary/`.
