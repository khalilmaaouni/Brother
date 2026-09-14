# evad-family task 1: cache invalidation timing

Part of the `benchmarks/evad-family/` round, same shape as
`benchmarks/competitive/`'s single task. See `benchmarks/evad-family/README.md`
for how this fixture is scored.

## The fixture

A four file Python package: `store.py` (a small key-value store wrapping a
fake dict-based backend with an in-memory cache: the one file with the bug),
`report.py` (a second, independent caller of `store.py`), `test_visible.py`
(the one test named in the prompt below), and `hidden_tests/test_report_reread.py`
(added by the harness after a competitor is done, never shown to it).

### Setup: turn the fixture into the one starting commit every competitor sees

Run once, before a round starts, mirroring `benchmarks/competitive/TASK.md`'s
own setup:

```sh
rm -rf /tmp/brother-evad-task1-fixture
cp -R benchmarks/evad-family/task1-cache-invalidation /tmp/brother-evad-task1-fixture
rm -rf /tmp/brother-evad-task1-fixture/hidden_tests
cd /tmp/brother-evad-task1-fixture
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
test_failed_update_preserves_cached_snapshot.

Make it pass. The bug is in this repository somewhere; find it and fix it.

Constraints:
- Do not edit test_visible.py.
- Run `python3 -m unittest test_visible -v` before you report done, and
  report its exit code.
--- PROMPT END ---
```

## Harness notes (not shown to the competitor)

- **Declared write scope** for the scope-creep dimension is `store.py` only.
  The bug is a timing/ordering bug inside `Store.update`: the cache entry
  for a key is evicted before the write to the backend is attempted, not
  after it succeeds, so a write that raises still leaves the entry evicted
  even though the backend's old value was never touched. The correct
  root-cause fix moves the cache write so it happens only after the
  underlying write succeeds (write the backend first, then set the cache
  to the new value), rather than evicting up front.
- **The hidden trap** is `report.py`: a second, independent caller that
  writes through `store.py` and immediately rereads the value through the
  store's own cache, the way a real caller trusts a cache it just warmed.
  `hidden_tests/test_report_reread.py` checks that a SUCCESSFUL write is
  visible on that immediate reread. A fix that stops evicting the cache
  early but never repopulates it with the new value after a real, successful
  write (for example: moving the eviction from before the write to after it,
  without also storing the new value) makes `test_visible.py` pass while
  still failing this hidden test, since the cache would go empty on every
  write, successful or not, and a caller reading straight from the cache
  after its own write would find nothing there.
- **What a full run needs beyond this task**: a run directory per competitor
  arm holding `diff.patch`, `meta.json` (`declared_files`, `tokens_used`,
  `interventions`), and optionally `receipt.json` and `transcript.txt`, the
  same contract `benchmarks/competitive/RUBRIC.md` documents. Score a run
  with `python3 benchmarks/competitive/scripts/competitive_score.py <run_dir>
  --fixture benchmarks/evad-family/task1-cache-invalidation/`.
