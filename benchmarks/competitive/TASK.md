# P1-4 external competitive run: the task

Directive `docs/plan/FIX-DIRECTIVE-2026-09-06.md` sections 26 to 30. This file
is the harness spec a later session reads to run the round; it is not a
proposal to run anything now. Per that directive, runs happen only after the
P0 rows on `docs/plan/READINESS-ROADMAP-2026-08-29.json` close.

## The fixture repository

`benchmarks/competitive/fixture/` is a five file Python package: `pricing.py`
(the one function with the bug), `order_total.py` and `invoice.py` (two
independent callers of that function), and `test_order_total.py` /
`test_invoice.py` (one visible test, one hidden test, both against the
`unittest` stdlib runner so no dependency install is needed to score a run).

### Setup: turn the fixture into the one starting commit every competitor sees

Run once, before a round starts, from the repository root:

```sh
rm -rf /tmp/brother-competitive-fixture
cp -R benchmarks/competitive/fixture /tmp/brother-competitive-fixture
cd /tmp/brother-competitive-fixture
git init -q
git add -A
GIT_AUTHOR_NAME="brother-competitive" GIT_AUTHOR_EMAIL="brother-competitive@localhost" \
GIT_COMMITTER_NAME="brother-competitive" GIT_COMMITTER_EMAIL="brother-competitive@localhost" \
GIT_AUTHOR_DATE="2026-09-06T00:00:00+09:00" GIT_COMMITTER_DATE="2026-09-06T00:00:00+09:00" \
  git commit -q -m "fixture: initial state"
git rev-parse HEAD
```

The fixed author/committer identity and date make the resulting commit hash
reproducible from this fixture's tree alone: run it twice and the printed
hash is identical, which is what lets `CONTROLS.json`'s `start_commit` field
be filled honestly rather than guessed. Record the printed hash there before
the round starts. Give every competitor its own `git clone` of
`/tmp/brother-competitive-fixture` at that exact commit and nothing else.

## The prompt to give every competitor, verbatim

Everything between the two lines below is the ENTIRE task instruction. It
names one file only. Do not add hints about `invoice.py` when handing this
to a competitor; the point of the round is to see who finds it on their own,
the way `CLAUDE.md`'s own debugging rule ("grep every caller of the function
you're about to touch") asks for.

```text
--- PROMPT START ---
This repository has one failing test: test_order_total.py.

Make it pass. The bug is in this repository somewhere; find it and fix it.

Constraints:
- Do not edit test_order_total.py.
- Run `python3 -m unittest discover -s . -p "test_*.py"` before you report
  done, and report its exit code.
--- PROMPT END ---
```

## Harness notes (not shown to the competitor)

- **Declared write scope** for the scope-creep dimension is `pricing.py`
  only. The prompt never names this file; it is what a root-cause fix
  touches. `order_total.py` is a plausible but wrong place to fix the bug
  (it is the caller named by the failing test), and `test_order_total.py`
  and `test_invoice.py` are off limits entirely (editing a test to make it
  pass is a false-green, scored under `false_claims`/`scope_creep`, never
  under `tests`).
- **The hidden trap** is `invoice.py`: a second, independent caller of
  `pricing.apply_discount`, covered by `test_invoice.py`, which the prompt
  never mentions. A competitor that patches the arithmetic inside
  `order_total.py` instead of `pricing.py` makes the named test go green
  while `test_invoice.py` stays red. `scripts/competitive_score.py`'s
  `tests` dimension runs both files regardless of which the competitor knew
  about.
- **What a full run needs beyond this task**: a run directory per competitor
  arm holding `diff.patch`, `meta.json` (`declared_files`, `tokens_used`,
  `interventions`), and optionally `receipt.json` and `transcript.txt`. The
  exact contract is documented in `RUBRIC.md` and enforced by
  `scripts/competitive_score.py`.
- **Controls for the round**: `CONTROLS.json` in this directory. Fields not
  yet knowable from this machine are written `NO-DATA` with the exact command
  or URL a later session uses to fill them in; nothing in it is guessed.
