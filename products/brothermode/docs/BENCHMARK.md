Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# The public benchmark

Thirteen scenarios, published with their inputs and their expected outputs, not
with a score. The score is a by-product. What matters is that you can run any
single scenario yourself, see the exact commands it ran, and check the answer
against what this page says it should be.

Run it:

```bash
python3 scripts/benchmark.py            # all thirteen, with full transcripts
python3 scripts/benchmark.py --quiet    # verdict lines only
python3 scripts/benchmark.py 3 11       # only scenarios 3 and 11
```

Exit code is 0 only when every scenario passed. A scenario that cannot run on
your machine prints SKIP with a reason and is never counted as a pass.

Measured on this machine, 2026-07-29, macOS, Python 3.9.6:

```
BENCHMARK: 13 passed, 0 failed, 0 skipped, of 13 run
```

## How it runs

Each scenario builds its own throwaway git repository under a temporary
directory, sets `BROTHERMODE_ROOT` to it, and drives the real command line
tools as subprocesses, the way a person would. Nothing is imported and mocked,
and nothing touches your own store. The temporary directory is deleted when
the scenario ends.

`scripts/benchmark.py` lives under `scripts/` rather than `tools/` on purpose:
the shipping tools may not use subprocess and a mechanical test enforces that,
while this harness has to spawn them to be a real end to end demonstration.

## The thirteen scenarios

The numbering is the ratified list in
`docs/BrotherMode_V2_Post_Audit_Execution_Loops.md`, unchanged.

### 1. Relevant correction retrieved

- Input: approve a gate rule (when: pushing commits or publishing a branch to
  GitHub, do: use the GitHub Desktop app), then ask
  `relevant --query "I want to push this branch to github"`.
- Expected: the rule comes back, `mode=lexical` is stated in the output, and
  the matched words are named (`branch`, `github`, `push`). A match that cannot
  say which words matched explains nothing, so the scenario fails on an empty
  term list even if the rule is returned.

### 2. Irrelevant rule not retrieved

- Input: approve a NON gate rule about writing release notes, then ask
  `relevant --query "what colour should the breathing orb be"`.
- Expected: zero rules returned, while the rule remains eligible by scope. This
  is the relevance floor: scope alone does not get a rule injected.

### 3. Applicable gate returned despite limit zero

- Input: one gate rule and one soft rule, both about pushing to GitHub, then
  `relevant --query "push this branch to github" --limit 0`.
- Expected: the gate is returned; the soft rule is not. A limit binds ordinary
  rules and may not silence a safety rule.
- This scenario FAILED when it was first written, on 2026-07-29, against
  commit 4332a90. The reproduction was one command:
  `bm_learn.py relevant --query "push this branch to github" --limit 0`
  printed `no founder rules apply here` with a live gate in the store. The fix
  is in `bm_store.py retrieve_learning_rules`: gates survive truncation.
  Calibrated by reinjecting the old truncation, which turns both this scenario
  and `test_calibrated_gate_survives_a_limit_that_truncates_everything` red.
- That first fix carried EVERY gate past the limit, which is the opposite
  failure, and this scenario could not see it because it only ever has one gate
  in the store. Reproduced on commit 68eb4d8 with twelve gates: `--limit 1`
  returned twelve rules, every one at relevance 0.0. The limit now binds
  ordinary rules, at most three gates are carried past it, and any gate the cap
  cuts is reported as a count instead of dropped in silence. This scenario does
  not prove that bound. `test_calibrated_gates_do_not_make_the_limit_unbounded`
  in `tools/test_bm_store.py` does.

### 4. Conflicting rules surfaced

- Input: approve a rule, then try to approve its reversal.
- Expected: the plain approve is refused with exit code 2 and a reason; after
  an explicit `--override-conflict` with a written reason, BOTH rules are live,
  the pair is listed by `conflicts`, and retrieval returns both together rather
  than picking a winner. The tool does not decide which of your instructions is
  the real one.

### 5. Rule edit does not rewrite past application history

- Input: approve a rule, record an application of it against a work record,
  then approve a replacement and `supersede` the original.
- Expected: `applications --rule <old>` still shows the text as it was applied,
  at version 1. Editing a rule today may not change what the record says you
  were shown yesterday.

### 6. Retrieval miss classified from complete context

- Input: two eligible rules, an application run recorded with `--limit 1` so
  only one of them reaches the model, then `classify`.
- Expected: exactly one `retrieval_miss`, naming the rule that was approved
  before the task ran, ranked for it, and got no application row.

### 7. Ignored gate classified as compliance failure

- Input: a gate rule is retrieved and recorded, then marked
  `disposition ... ignored`.
- Expected: classification `compliance_failure`. Shown and skipped is not an
  excuse and is not graded as "the rule was wrong".

### 8. Followed bad rule classified from rework

- Input: a rule is retrieved, marked `followed`, and the work is marked
  `--outcome rework`.
- Expected: classification `bad_rule`, not `compliance_failure`. When the rule
  was obeyed and the work still had to be redone, the rule is what failed.

### 9. Repeated correction classified by cause

- Input: a rule is ignored once, followed and accepted once, promoted to
  `confirmed` then `settled` on that evidence, and then the same correction is
  captured a second time. Run `repeat-check --record <work>`.
- Expected: the repeat is attributed to the settled rule with cause
  `compliance_failure`, not counted as new evidence. A rule that is not yet
  settled treats a repeat as first evidence instead, which the tool says in
  words.

### 10. Forced compaction recovery restores files and context

- Input: commit a file, edit it without committing, claim a work record, run
  `bm_autosave.py precompact`, clobber the file, then `bm_autosave.py recover`.
- Expected: the recovered copy holds the in-flight text; the LIVE working tree
  is not touched (recovery lands in a separate worktree, printed by path); and
  the store still lists the work record, so the context survives too.

### 11. Conflicting file write blocked through supported edit tool

- Input: session A takes a fence over `src/app.py` under its own hook-derived
  label, then session B sends a real `PreToolUse` payload for an `Edit` of that
  file.
- Expected: `permissionDecision: deny`, and the reason names the owning record
  so it can be acted on. A deny that says only "no" is a failure here.

### 12. Default export withholds founder prose

- Input: capture a candidate carrying the founder's raw words, then read it
  back with `show-candidate --json`, with `--show-source --json`, and check
  `bm_store.py dump`.
- Expected: the default JSON carries `[withheld: pass --show-source]` and not
  the words; the opted-in call returns them verbatim; `dump` never contains
  them.

### 13. Clean install and uninstall leave expected traces only

- Input: run a normal working session (approve, retrieve, claim) with `HOME`
  pointed at an empty directory, then delete `.brothermode/` and the generated
  `STATE.md`.
- Expected: the only paths created in the project are `.brothermode/`,
  `STATE.md`, and timestamped `STATE.md.bak-*` backups; no BrotherMode data is
  written under `HOME`; after removal the tree is exactly as it was found.

## What this benchmark does NOT prove

Stated here rather than left for a reader to discover.

- **It is not the test suite.** `python3 tools/test_all.py` is the gate. This is
  the public demonstration, and it is deliberately small enough to read.
- **It is not evidence of real-world use.** Every scenario runs against a
  throwaway store built seconds earlier. `docs/NOT-FINALIZED.md` item 1 (this
  has never run through a real working day) is untouched by a green benchmark.
- **Scenario 13 records one real trace it does not fail on:** CPython writes
  compiled bytecode caches for any module it runs, and on macOS those land
  under `HOME`. The scenario counts and prints them, and asserts only that no
  BrotherMode DATA is written there. `python3 -B` suppresses them.
- **Retrieval is lexical.** Scenarios 1, 2, 3 and 6 test a word-overlap matcher
  that names itself `mode=lexical` in its own output. They are not evidence of
  semantic retrieval, which this project does not have.
- **One machine, one platform.** The measured line above is macOS on Python
  3.9.6. Windows behaviour comes from CI, never from a machine on this desk.
- **Thirteen scenarios are thirteen scenarios.** They were ratified before the
  code was written, which is the point, but they are a floor and not a survey
  of everything that could go wrong.
