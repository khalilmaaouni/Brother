# Jev evaluation benchmark (JEV-04)

Ground truth measured in session 50a7c16b on 2026-09-18 (ORCH-1020),
comparing Jev (TypeSafe) against Muse and DeepSeek as judges on 80 labelled
items across four tasks. The dataset, runner and scorer are frozen here so
the cost-per-success and calibration claims made about Jev can be rerun,
not just re-read.

## What each task measures

- **Task A, mutation triage (24 items).** Given a rule a function enforces,
  the names of its tests, and a described mutation of the function, decide
  whether at least one listed test would fail under the mutation. This is
  the same "does this test suite actually catch this defect" judgment this
  estate's own mutation gates make.
- **Task B, unknown-reads-safe detection (20 items).** Given the text of a
  rule, decide whether a missing, unknown, undeclared or unreadable input
  ends up treated as the passing, permitted or safe outcome. This is the
  "unknown input raises, never falls through as safe" invariant, stated as
  a judgment problem.
- **Task C, routing (22 items).** Given the specification of a small module
  one model will draft and another will integrate, predict whether the
  landed result will pass with only light integration, or whether a later
  reviewer will find a real defect needing a fix.
- **Task D, gate-line triage (14 items).** Given one line of a release
  pipeline's gate output, classify it as blocking (the release must stop),
  warning (worth a human look, release may continue) or info (no action
  needed).

## How the labels were established

Every label is a fact recorded before any model was asked, not a
consensus of the models under test:

- Task A's `caught` label comes from the orchestrator's own mutation runs
  on this estate's code: a mutation was actually applied and the named
  tests were actually run against it.
- Task B's `unsafe` label is read directly off the stated rule text: does
  the rule, as written, let an unknown or missing case through as a pass.
- Task C's `clean` / `needs_fix` label comes from this session's own
  verification outcome once each unit actually landed and was reviewed.
- Task D's label is the release process's own documented policy for that
  line's prefix (BLOCK / WARN / PASS / SKIP / NO-DATA), not a model's
  opinion of severity.

Every prompt is role-worded: no repository source, no file paths, no
product or company names appear in what a model is shown (see
`dataset.json`'s own `about` field).

## The fallback-is-NO-DATA rule

A row whose model answered as a model other than the one asked (the
bridge's own fallback chain substituting a different model) carries
`pred: null` and an `error` string, and is never credited to the model
that was actually asked. `scripts/jev_eval.py score` computes accuracy,
balanced accuracy and cost-per-success only over rows where `pred` is not
null; every row with `pred: null` is counted separately as NO-DATA and
never folds into either the numerator or the denominator of a correctness
figure. A results file with zero rows is refused as NO-DATA, never printed
as a zero score, because nothing measured is not the same fact as
everything failed.

A bridge call that timed out, could not start (`OSError`), or answered
with text that was not valid JSON is caught at the call site in
`scripts/jev_eval.py` and turned into exactly one NO-DATA row naming the
cause. This matters because the session's own `run_eval.py` let one
`subprocess.TimeoutExpired` escape a thread-pool worker and crash the
whole run, which is why the frozen file below has 394 rows rather than the
398 the dataset's item count implies: 4 DeepSeek attempts on task C never
got the chance to write a row at all before that run ended. Going forward,
`scripts/jev_eval.py run` cannot lose a row this way; see
`scripts/test_jev_eval.py`'s
`test_a_timed_out_bridge_call_is_recorded_as_nodata_and_the_run_completes`.

## How to rerun

```
python3 scripts/jev_eval.py run --dataset benchmarks/jev_eval/dataset.json --out benchmarks/jev_eval/results.jsonl
python3 scripts/jev_eval.py score benchmarks/jev_eval/results.jsonl
```

`run` needs a working bridge: by default it looks for
`~/.claude/bin/or_ask.py` and reports NO-DATA (refuses, records every row
it needed the bridge for as NO-DATA) if that file is not present. Set
`BROTHER_DECISION_BRIDGE` and/or `BROTHER_CHAT_BRIDGE` in the environment
to point at a different bridge command (Jev's typed-decisions calls use
the decision bridge, Muse's and DeepSeek's chat calls use the chat
bridge). `run` moves an existing file at `--out` aside
(`<out>.prev-<timestamp>`) rather than overwriting it, so a rerun never
silently destroys a prior result.

`score` takes a results path and an optional confidence gate (default
0.90, the threshold used for the `cover` and `acc@gate` columns below).

## The frozen evidence: `results-2026-09-18.jsonl`, 394 rows

This is the exact row set produced the night this benchmark was designed
and run, copied unmodified from the session's own evidence directory,
frozen so the numbers below can be checked without a live bridge. The
routing specs Task C's items reference are copied to `specs/` beside
`dataset.json`, and `dataset.json`'s own `C_routing.spec_dir` field points
at that relative directory, never at an absolute path under `/Users` or
`/tmp`.

Scorer output, pasted verbatim from
`python3 scripts/jev_eval.py score benchmarks/jev_eval/results-2026-09-18.jsonl`
run against this exact file (no number below is typed by hand):

```
gate for autonomous use: confidence >= 0.90

TASK A  items=24  base rate (always the majority answer)= 75%
  system        n    acc bal_acc  cover  acc@gate     $/success  $/gated-succ  p50 s
  jev          24    88%     75%    54%       92%     $0.000022     $0.000038    0.4
  jev2         23    87%     75%    48%       91%     $0.000022     $0.000044    0.4  NO-DATA rows=1
  jev_para     24    92%     83%    50%       92%     $0.000019     $0.000038    0.4
  muse         24    96%     92%    96%       96%     $0.000253     $0.000265    7.6
  deepseek     24    92%     89%    96%       96%     $0.000710     $0.000710    3.8
  calibration jev      0.5-0.7:  50% of 2 | 0.7-0.9:  89% of 9 | 0.9-1.0:  92% of 13
  calibration muse     0.5-0.7:   - of 0 | 0.7-0.9: 100% of 1 | 0.9-1.0:  96% of 23
  calibration deepseek 0.5-0.7:   - of 0 | 0.7-0.9:   0% of 1 | 0.9-1.0:  96% of 23
  jev agreement with its repeat:    23 of 23
  jev agreement with its paraphrase: 23 of 24

TASK B  items=20  base rate (always the majority answer)= 60%
  system        n    acc bal_acc  cover  acc@gate     $/success  $/gated-succ  p50 s
  jev          20    90%     88%    35%      100%     $0.000018     $0.000047    0.6
  jev2         20    95%     94%    35%      100%     $0.000017     $0.000047    0.5
  jev_para     20   100%    100%    15%      100%     $0.000016     $0.000107    0.5
  jev_batch    20    90%     88%    20%      100%     $0.000008     $0.000034    0.0
  muse         20    85%     88%    90%       89%     $0.000437     $0.000465   15.8
  deepseek     20    95%     96%    85%       94%     $0.001397     $0.001659    6.0
  calibration jev      0.5-0.7:  60% of 5 | 0.7-0.9: 100% of 8 | 0.9-1.0: 100% of 7
  calibration muse     0.5-0.7:   - of 0 | 0.7-0.9:  50% of 2 | 0.9-1.0:  89% of 18
  calibration deepseek 0.5-0.7:   - of 0 | 0.7-0.9: 100% of 3 | 0.9-1.0:  94% of 17
  jev agreement with its repeat:    19 of 20
  jev agreement with its paraphrase: 18 of 20
  jev agreement with its batched:   18 of 20

TASK C  items=22  base rate (always the majority answer)= 68%
  system        n    acc bal_acc  cover  acc@gate     $/success  $/gated-succ  p50 s
  jev          22    55%     51%     5%        0%     $0.000133       NO-DATA    0.8
  jev2         22    64%     66%     5%        0%     $0.000114       NO-DATA    0.7
  muse         22    64%     70%     0%   NO-DATA     $0.002395       NO-DATA   63.5
  deepseek     13    38%     50%    15%       50%     $0.026773     $0.133866  290.6  NO-DATA rows=5
  calibration jev      0.5-0.7: 100% of 1 | 0.7-0.9:   0% of 1 | 0.9-1.0:   0% of 1
  calibration muse     0.5-0.7:   - of 0 | 0.7-0.9:  64% of 22 | 0.9-1.0:   - of 0
  calibration deepseek 0.5-0.7:   0% of 1 | 0.7-0.9:  40% of 10 | 0.9-1.0:  50% of 2
  jev agreement with its repeat:    20 of 22

TASK D  items=14  base rate (always the majority answer)= 50%
  system        n    acc bal_acc  cover  acc@gate     $/success  $/gated-succ  p50 s
  jev          14    79%     73%    64%      100%     $0.000022     $0.000027    0.6
  jev2         14    79%     73%    71%      100%     $0.000022     $0.000024    0.5
  jev_batch    14   100%    100%    79%      100%     $0.000008     $0.000010    0.0
  muse         14    93%     89%   100%       93%     $0.000177     $0.000177    7.1
  deepseek     14    86%     84%    93%       92%     $0.000839     $0.000839    3.2
  calibration jev      0.5-0.7:   0% of 1 | 0.7-0.9: 100% of 2 | 0.9-1.0: 100% of 9
  calibration muse     0.5-0.7:   - of 0 | 0.7-0.9:   - of 0 | 0.9-1.0:  93% of 14
  calibration deepseek 0.5-0.7:   0% of 1 | 0.7-0.9:   - of 0 | 0.9-1.0:  92% of 13
  jev agreement with its repeat:    14 of 14
  jev agreement with its batched:   11 of 14

spend this eval by system: deepseek $0.1861, jev $0.0026, jev2 $0.0026, jev_batch $0.0002, jev_para $0.0007, muse $0.0491
```

Reading this table: `n` is the count of rows with a real prediction (never
including NO-DATA rows), `cover` is the fraction of those at or above the
confidence gate, `acc@gate` is accuracy restricted to that gated set,
`$/success` and `$/gated-succ` divide this row's total spend by the number
of correct answers overall and within the gated set respectively (`NO-DATA`
in either cost column means zero successes to divide by, not zero cost).
`jev2` is an independent repeat call with the identical question; `jev_para`
paraphrases the instructions (tasks A and B only); `jev_batch` carries every
item of one task in a single call (tasks B and D only). The "jev agreement"
lines measure Jev against its own repeat, paraphrase and batched answers on
the same items, not against ground truth.

## Reading this responsibly

An adversarial review of this harness (Muse, meta/muse-spark-1.3-contributor,
prompt=10394 completion=4252) found several ways these numbers invite a
reader past what they actually support. None of these are code defects,
they are properties of a small, mixed-methodology evaluation, so read the
table with them in mind rather than at face value:

- **Small samples in the gate and calibration cells.** Tasks have 14 to 24
  items each, and `acc@gate` and the calibration bands slice that further:
  several cells in the table above are computed from 1 to 3 rows (Task C's
  `jev` `acc@gate` of 0% is one item). A single item flipping changes that
  cell by tens of points. Treat any cell with `n` under 5 as a data point,
  not a rate.
- **Confidence is not the same quantity across systems.** Jev's `conf` is
  its typed decision's own probability (`max(p, 1-p)` from the `noul`
  answer); Muse's and DeepSeek's `conf` is a probability the model wrote
  into its own free-text JSON answer, unverified against anything. `cover`
  and the calibration bands compare these as if they were one scale; they
  are two different things that happen to share a column.
- **`jev_batch`'s per-row cost and seconds are amortized, not per-call.**
  One batched call answers every item of the task at once; its total cost
  and elapsed time are divided by the item count to produce the `cost` and
  `secs` shown per row, so a `p50 s` of `0.0` means the whole call was
  fast and the task had several items, not that each answer arrived in
  zero seconds.
- **Cost accounting differs by system.** Jev's cost comes from the bridge's
  own reported `usage.cost` (or is absent). Muse's and DeepSeek's cost
  uses `usage.cost` when the chat response carries one, and otherwise
  falls back to the catalog per-token price recorded at the top of
  `scripts/jev_eval.py`. The two are not measured the same way, so
  `$/success` compares two different accounting methods, not two prices
  for the same thing.
- **Tasks B and D read close to the rule or the line they classify.**
  Task B's items restate the rule text itself; Task D's items are gate
  output lines whose prefix (`BLOCK` / `WARN` / a pass-shaped line) often
  gives the label away. Both tasks are closer to a text-matching exercise
  than Tasks A and C are, which is part of why B and D read as easier
  across every system, not evidence that judgment on those two is more
  reliable in general.
- **One frozen run, one session, one set of prices and timings.** Every
  number above comes from a single run on 2026-09-18; nothing here is
  averaged over repeated runs, dated confidence intervals, or a fixed
  random seed. A rerun through a live bridge will differ, sometimes by
  more than the gap between two systems in this table.

## Files in this directory

- `dataset.json`: the 80-item ground truth set, four tasks, role-worded.
- `specs/`: the 22 routing specifications Task C's items reference.
- `results-2026-09-18.jsonl`: the frozen 394-row evidence file this README
  was scored against.
