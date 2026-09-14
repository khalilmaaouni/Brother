# evad-family: a three-task fixture family for a controlled cross-framework round

This directory holds three small, self-contained Python fixtures for a
controlled coding-agent benchmark round across Brother, GSD, Superpowers and
BMAD, in the same shape as `benchmarks/competitive/`'s single task: stdlib-only
`unittest`, one visible test named in the prompt, one hidden test the prompt
never mentions, and a single correct root-cause fix per task. Each task's own
`TASK.md` carries the exact verbatim prompt to hand a competitor, harness
notes not shown to the competitor, and the hidden trap it names.

| Task | Bug | Visible test | Hidden trap |
|---|---|---|---|
| `task1-cache-invalidation/` | cache evicted before the write, not after it succeeds | `test_visible.py::test_failed_update_preserves_cached_snapshot` | `report.py`, a second caller reading the cache right after a successful write |
| `task2-chunk-boundary/` | off-by-one drops the final partial chunk | `test_visible.py::test_chunk_ten_by_three` | `export.py`, exercising an empty list and an exact-multiple length |
| `task3-shallow-copy-leak/` | `dict(DEFAULTS)` shallow-copies nested structures | `test_visible.py::test_nested_override_does_not_change_defaults` | `session.py`, mutating a nested list/dict across two instances |

Every task ships its `hidden_tests/` directory in this repository (the
harness needs it to score a run later); a competitor's own clone never sees
it, per each task's own Setup section.

## Scoring: reusing benchmarks/competitive/scripts/competitive_score.py

`scripts/competitive_score.py`'s CLI already takes a `--fixture DIR` and a
`--controls FILE` override (its own usage line: `competitive_score.py
<run_dir> [--fixture DIR] [--controls FILE]`), and five of its six scoring
dimensions read nothing task-specific: they parse `diff.patch`/`meta.json`
against whatever fixture directory is passed. This was verified by hand, not
assumed: a real fix diff for `task2-chunk-boundary/` was scored against the
shipped, unmodified fixture and `tests` and `scope_creep` both came back
`PASS` with the correct detail. Score any of these three fixtures with:

```sh
python3 benchmarks/competitive/scripts/competitive_score.py <run_dir> \
  --fixture benchmarks/evad-family/task-N-.../ \
  --controls benchmarks/evad-family/CONTROLS.json
```

**No new RUBRIC.md was written.** `benchmarks/competitive/RUBRIC.md` describes
`scripts/competitive_score.py`'s six dimensions in full, and that script (the
thing that actually runs) is what evad-family reuses unmodified via
`--fixture`; a second document restating the same six dimensions for this
directory would not be a different rubric, just a copy, so it was skipped.

**One real gap found and disclosed, not fixed**: `false_claims` (dimension 4)
compares a run's claim against a hardcoded visible-test filename,
`test_order_total.py` (`competitive`'s own visible test), inside
`score_visible_test_only`. Every evad-family task names its visible test
`test_visible.py` instead, so on an evad-family run this dimension always
looks for a file that is not there and reports the visible test as failed
regardless of the real outcome (confirmed by hand: the same smoke run that
scored `tests`/`scope_creep` correctly above got `false_claims: FAIL` with
`test_order_total.py: exit 5: NO TESTS RAN`, even though the fix was
correct). Fixing this means editing `scripts/competitive_score.py`, which is
inside `benchmarks/competitive/` and out of scope for this change (the
instruction for this fixture family is explicit: do not touch that
directory). Until a later change generalizes the filename, treat any
`false_claims` line printed for an evad-family run as unreliable, not as a
real measurement, and read the `tests` dimension's own detail (it names the
actual failing test file) for the real answer to "did the claim match
reality."

## CONTROLS.json: a new file, and why

`benchmarks/competitive/CONTROLS.json`'s `start_commit`, `token_budget` and
`intervention_budget` are each a single value, correct for one fixture. This
directory holds three independent fixtures, each needing its own frozen
start commit (produced by that task's own `TASK.md` Setup section) and, in
principle, its own budgets, so a single flat value cannot represent the
round honestly. `benchmarks/evad-family/CONTROLS.json` keeps the same
`NO-DATA`-until-filled discipline as the original but nests `start_commit`,
`token_budget` and `intervention_budget` under a `tasks` map, one entry per
task directory. Its own `scoring_note` field states plainly that
`scripts/competitive_score.py` reads `token_budget`/`intervention_budget` as
flat top-level fields, so scoring a specific task's run needs that task's own
numbers copied to a per-task controls file (or the score run with those two
fields patched in for that call) rather than pointing at the nested map
directly; nothing here pretends that wiring exists when it does not.
