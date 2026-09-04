# 04. Teams and evolution

How a small team (two to eight people) runs BrotherSBE, and how the skill gets
better over time without any colleague's tool quietly changing how yours behaves.

BrotherSBE is the specialist sibling of BrotherModeUp
(github.com/khalilmaaouni/BrotherModeUp). BrotherModeUp assumes one operator.
BrotherSBE keeps every gate and hook that one gave you and adds one thing the solo
design left out: a way for a lesson one engineer learned to become a law the whole
team runs, through a mechanism a reviewer can see and a teammate can veto.

This doc is worked examples with real commands and real JSON, because the operator
asked for depth on execution, not theory. Every command, file, and JSON field
below exists in the shipped code. Paths are written as `~/.claude/skills/brothersbe`
(the install location) and repo-relative `tools/...`; substitute your own clone
path if you cloned elsewhere.

---

## 1. What a team install actually is

Two things live in two places, and keeping them straight is the whole model.

**The shared repository** (cloned to `~/.claude/skills/brothersbe` on every
machine) carries the law and the tools:

- `SKILL.md` and the `references/*.md` files its routing table names, the outermost law
  when `/brothersbe` is invoked.
- `tools/sbe_gate.py`, `tools/sbe_score.py`, `tools/sbe_telemetry.py`, the hooks.
- `RUBRIC.md`, the metric definitions the weekly review scores against.
- `memory-template/LEARNED.md`, the team law file. This is the only
  file in the repo whose content is meant to grow from what the team learns.

**The local vault** (one per machine, default `~/BrotherSBEVault`, relocatable
with `BROTHERSBE_VAULT`) carries everything private:

- `99-System/telemetry/outcomes.jsonl`, one line per session: token counts, tool
  calls, duration, and the basename of the working directory. No file contents,
  no prompts.
- `99-System/telemetry/corrections.jsonl`, short excerpts of your own messages
  that look like corrections, secret-redacted and owner-only (0600).
- `99-System/telemetry/ratings.jsonl`, `reviews.jsonl`, and the per-project
  `Sessions/*.md` logs.

The line between them is the security guarantee: **the shared repo is reviewed and
public to the team; the vault never leaves the machine.** `DIGEST.md` states it
("Local telemetry never leaves the machine"),
and `SECURITY.md` lets you verify the scoped network claim yourself:

```bash
grep -rnE "urllib|requests|socket|http|curl|wget|subprocess" tools/ src/ hooks/ scripts/ bin/
```

(Expected result: exactly one real network call, `src/brothersbe/prverify.py`,
which is the documented `sbe pr verify` exception and reads the GitHub API only
when you run it. Every other hit is subprocess-for-git, a word inside a message
string, or a redaction-test fixture. The count moves with the code, so no number
is stated; `SECURITY.md`
states the same property beside the command, and a test in
`tools/test_sbe.py` re-checks the property itself on every run.)

A lesson crosses from the private side to the shared side exactly once, and only
one way: a reviewed pull request into `LEARNED.md`. Everything in this doc hangs
off that sentence.

---

## 2. A day in the loop: the gates in one engineer's session

Before the learning story, the thing that makes learning worth spreading: the four
hard gates in `tools/sbe_gate.py`. They are what "trust in proportion to
mechanical checkability" means at the keyboard. An engineer ships a warehouse
figure, a migration, a money-path change, or a reconciliation, and the gate reads
a receipt off disk that proves the check ran.

### 2a. A number that will reach a decision

Say the change reports a GMV figure a manager will quote. The engineer writes a
`numbers-manifest.json` next to the change:

```json
{
  "figures": [
    {
      "label": "gmv_q3",
      "snapshot_id": "snap_2026_07_24",
      "query": "SELECT SUM(amount) FROM orders WHERE quarter = 'Q3'",
      "second_derivation": "SELECT SUM(qty * unit_price) FROM order_lines WHERE quarter = 'Q3'",
      "rerun": { "ran": true, "primary": 4820113, "secondary": 4820113 }
    }
  ]
}
```

The gate reads exactly these fields (`sbe_gate.py`, `gate_numbers`): a
`snapshot_id` (a live warehouse drifts, so the read is pinned), a
`second_derivation` that is textually different from `query` (a copy is not an
independent check), and a `rerun` whose `primary` and `secondary` match (zero
drift). Run it:

```bash
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py numbers
```

```
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   PASS     1 figure(s) each pinned to a snapshot, with a second derivation whose text differs beyond case, whitespace and comments, re-run to zero drift; read 1 numbers-manifest.json under . (numbers-manifest.json); 0 of 0 director(y/ies) directly under . contributed no numbers-manifest.json [severity: gate]
```

Change `secondary` to `4820109` and the same gate prints
`FAIL  gmv_q3: DRIFT primary=4820113 secondary=4820109 (zero drift required)`.
Delete the `snapshot_id` and it prints
`FAIL  gmv_q3: no snapshot_id recorded (None); a live warehouse drifts, so pin the read. A placeholder is not a pin`. Present no
manifest at all and it prints `NO-DATA`, never `PASS`: absent evidence is never a
pass, which is the honesty law inherited from the chassis.

### 2b. A reconciliation that must have actually run

```json
{ "checks": [ { "name": "row_parity", "exit_code": 0, "duration_ms": 812 } ] }
```

`sbe_gate.py ran` (`gate_ran`) fails a check with no `exit_code` ("was it actually
run?"), a nonzero `exit_code` (green claimed on red), or a zero/missing
`duration_ms` ("a check that took no time did not run"). A green build the agent
reported but did not run is the exact lie this gate exists to catch.

### 2c. A migration with a tested reverse

```json
{
  "forward": { "ran_against_restore": true },
  "reverse": { "ran_against_restore": true, "rehearsal_run_id": "job_8842" },
  "row_counts": { "before": 100, "after_reverse": 100 }
}
```

`sbe_gate.py migration` fails a reverse that never ran against a restored copy, a
reverse whose `rehearsal_run_id` is missing or is not a string, or a
`row_counts` where `after_reverse` does not equal `before` (the reverse dropped
rows). A migration without a tested reverse is a one-way door.

### 2d. A money or partner path with a real approval

The change carries an `APPROVAL` file declaring it touches a billing or partner
path, and the approval is bound to more than a typed name: a signed commit
trailer (`Approved-by:` on a commit whose signature verifies as G here; U means
the key matched no trusted principal and E means this host could not check it,
and both are NO-DATA rather than an approval) or a
recorded platform review id (`Reviewed-in:`), which nothing resolves and which
the gate's evidence line describes as a pointer rather than proof. `sbe_gate.py
approval` fails a bare typed name: "a name in a text field is not a control."

### 2e. Advisory in the session, enforcing in CI

Every subcommand runs advisory by default (prints the verdict, exits 0: a session
gets told) and enforcing under `--strict` (exits nonzero on any FAIL: a merge gets
stopped). The team wires the strict form into CI:

```bash
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py --strict
```

Overrides exist because reality does, but an override is named, carries a reason,
and is visible in the diff and on the tool's printed verdict line. It is never
available on the `--strict` CI path, and it is never silent.

### 2f. The silent-failure lints

`tools/sbe_score.py` also scans the worktree for patterns that hide an error so a
wrong result passes for a right one: bare `except:`, except-then-`pass`, a
conflict-skipping upsert (`ON CONFLICT ... DO NOTHING` with no logged skip count),
a discarded `subprocess` result without `check=True`, and Swift `try!`. These are
gate severity by ratified decision. A genuine, reviewed exemption carries a
visible marker on the line, which is the override philosophy applied to lints (a
swallow is legal only when a human named why, in the diff):

```python
except OSError:  # sbe: allow-silent optional filesystem read; absence handled by the caller
    pass
```

The scan is opt-in on a path, so it never false-alarms on an unrelated tree:

```bash
python3 ~/.claude/skills/brothersbe/tools/sbe_score.py ~/work/your-service
# or
SBE_LINT_ROOT=~/work/your-service python3 ~/.claude/skills/brothersbe/tools/sbe_score.py
```

### 2g. The gates are proven, not asserted

`evals/run_evals.py` is one case per real failure class, each a fixture with a
planted defect and an assertion that the matching gate catches it: the overstated
five-year total, the non-independent second derivation, the unpinned read, the
untested reverse, the lossy reverse, the typed-name approval, the green-on-red
check, and the sound counterparts that must PASS. A release is blocked if any
regresses:

```bash
python3 ~/.claude/skills/brothersbe/evals/run_evals.py
```

```
  overstated-total-caught                want=FAIL     got=FAIL     ok
  sound-number-passes                    want=PASS     got=PASS     ok
  ...
  547 evals: 547 passed, 0 regressions.
```

That is what "proven" means here: the gates are tested against the exact defects
the operating record produced, and a promotion into `LEARNED.md` never weakens a
gate without its eval moving with it.

---

## 3. The team-learning law: what stays local, what becomes a law

Voluntary logging collapses, so telemetry is written by hooks, never by promises.
`sbe_telemetry.py outcomes-append` runs at `SessionEnd`, and by default it captures
nothing at all. Each category is separately opt-in: `BROTHERSBE_TELEMETRY_METRICS`
for the idempotent line in `outcomes.jsonl`, `BROTHERSBE_TELEMETRY_TRANSCRIPT` for
anything read out of the session transcript, and `BROTHERSBE_TELEMETRY_CORRECTIONS`
for the correction candidates written, secret-redacted and owner-only, to
`corrections.jsonl`. `BROTHERSBE_TELEMETRY_DISABLE` forces every category off and
cannot be re-enabled locally. A category that stays off names the switch that kept
it off, so an empty file is never mistaken for a quiet session. None of it leaves
the machine either way, and the default is now off because best-effort redaction is
not a safe default for a repository that may hold customer or partner material.

A candidate is raw material, not a law. It is an excerpt of one engineer's own
message on one machine. It becomes a law only by being distilled into a rule with
its reasoning and merged into `LEARNED.md` through a pull request a human reviews.
Three properties hold at that boundary, and they are the point of the whole design:

1. **The promotion carries the rule and its reasoning, not the raw ledger.** The
   reviewer judges a distilled rule, never someone's private transcript. The
   `corrections.jsonl` line that started it stays on the origin machine.
2. **Every install reads `LEARNED.md` on session start.** When `/brothersbe` is
   invoked, the invocation sequence (`SKILL.md`, "Every run, mechanically", step 2)
   reads the project overview, open items, failures index, and the `LEARNED.md`
   team laws before it acts. Merge a rule today and every teammate's next session
   runs under it. (The `SessionStart` shell hook injects the digest and the
   telemetry nags; the agent itself reads `LEARNED.md` as part of invocation.)
3. **No colleague's tool changes your behavior silently.** The only thing that
   alters how BrotherSBE behaves across the team is a merged diff to a file in the
   shared repo. There is no push of learned state from one machine to another. If
   your behavior changed, a reviewed commit changed it, and you can read it.

---

## 4. One promotion, end to end

A concrete walk, from the gotcha to the merged law, with the commands each step
runs. Nothing here is invented; every tool call exists.

### Step 1: an engineer hits the same wall twice

An engineer on the team writes a backfill that upserts with
`ON CONFLICT (id) DO NOTHING` and no skip count. It runs clean. A week later a
reconciliation is short by a few thousand rows, and the cause is that same silent
skip: rows that collided were dropped and nothing counted them. The
silent-failure lint in `sbe_score.py` flags the pattern each time
("conflict-skipping upsert without a logged skip count"), but the team keeps
re-introducing the underlying design mistake because the lint fires per file and
nobody has written down the rule behind it.

The engineer's frustration shows up in their own session as a short correction
("no, a backfill upsert has to log a skip count"), and the `SessionEnd` hook
captures it as a candidate:

```bash
python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py scorecard
# ... N correction candidates pending
```

### Step 2: propose a `LEARNED.md` line in a PR

At the weekly review (or the moment the pattern is undeniable), the engineer
distills the candidate into a rule with its reasoning. `LEARNED.md` is human-read
markdown, and `memory-template/LEARNED.md` fixes the shape: three lines under the
`## Laws` heading, newest first, being the LESSON, the RULE, and the BECAUSE
clause carrying the underlying reason (the weekly review step 5 requires that
clause). They open a branch on the shared repo and add one entry:

```markdown
## Laws

    LESSON: a backfill silently dropped colliding rows and the shortfall surfaced weeks later.
    RULE:   backfill upserts log a skip count; never a bare ON CONFLICT DO NOTHING.
    BECAUSE: a backfill that drops rows looks identical to one that inserted them, so the loss is only visible in a reconciliation that runs much later.
```

They push the branch through the GitHub Desktop flow and open a pull request. The
raw `corrections.jsonl` excerpt never travels: the PR carries the distilled rule
and its reasoning only, so the reviewer judges the rule, not private data.

### Step 3: a teammate reviews the rule

A second engineer reviews the PR. They are not confirming that the author was
annoyed; they are judging whether the rule is a law worth binding the whole team
to. Concretely they check: is the `because` clause the real reason and not a
restatement of the rule; does the rule contradict anything already in `LEARNED.md`
or in the law (`SKILL.md` and its `references/` files); is it specific enough to follow and general enough to matter; does
it need a gate or a lint to have teeth (here it already has one, so the rule
documents the design intent behind an existing lint). If it needs changes, that is
a review comment, same as any code PR.

### Step 4: merge, and everyone gets it next session

The teammate approves, the PR merges to the shared repo, and every engineer pulls.
On each machine, the next `/brothersbe` invocation reads the updated `LEARNED.md`
as part of its startup sequence and now carries the rule. `sbe_telemetry.py
check-update` (which reads git ref files directly, no network, no subprocess) will
also note once that the law changed under you and print the diff command, because
a changed law must be read, not silently inherited:

```
BROTHERSBE: the skill changed since your last session (a1b2c3d -> e4f5g6h).
Read the diff before relying on it:
  git -C ~/.claude/skills/brothersbe log --oneline a1b2c3d..e4f5g6h
```

One gotcha, one distilled rule, one reviewed PR, one merge, and the whole team is
protected on their next session. That is the entire team-learning mechanism. There
is no hidden channel, and there was no moment where one engineer's machine reached
into another's.

---

## 5. The weekly review loop

`LEARNED.md` grows through PRs whenever a lesson is ready. The weekly review is the
governor that keeps that growth honest: it decides which candidates deserve
promotion, and it audits whether last week's changes actually helped.
`tools/WEEKLY-REVIEW.md` is the runbook; here is the shape that matters for
evolution.

### Code grades first, judgment on the residue

```bash
python3 ~/.claude/skills/brothersbe/tools/sbe_score.py
```

`sbe_score.py` runs fourteen mechanical checks and labels each PASS, FAIL, or NO-DATA:
ledger coverage, schema uniformity, cache economy (warm-read ratio floor 90
percent), a vault log per active day, fence hygiene, correction latency,
budget-vs-tier tagging, prediction seals, felt-outcome ratings, review cadence,
the silent-failure lints, and the citation inventory (every external URL cited in
the shipped docs carries its claim, population, date and limit in
docs/CITATIONS.md, checked offline, never against live page content). It prints a tally and exits 0 (advisory); CI runs it
`--strict` to block on a FAIL. The rule the whole loop turns on: **the LLM judge
scores only the residue the code cannot decide.** Anything a check can settle
mechanically is off the judge's desk, which is what keeps scoring cheap and
repeatable.

### Judgment on what is left, isolated and anchored

The residue (the nine RUBRIC.md metrics that need judgment) is scored by a
fresh-context session or subagent given only the evidence bundle: the scorecard
output, a ledger extract, the git log, and `registry-check`. It never sees the
sessions that did the work (judge-isolation, against self-preference bias), and it
scores by comparison against last week's bundle (better, same, or worse per
metric), not naked absolute numbers. Self-scores cap at 8; a 9 or 10 needs named
external evidence (a passing CI run, a reviewer approval, a reproduced number).

### Amendments are reverted if their named signal did not move

This is the part that stops the skill from drifting. The review lands at most one
consolidation edit to the law (`SKILL.md` or one of its `references/` files) per week, and every amendment names the measured
signal it is supposed to move. Before landing this week's edit, the reviewer checks
last week's amendment against the signal it named, and reverts it if the signal did
not improve. A rejected candidate keeps its reason and is not re-proposed without
new evidence. The same discipline governs a `LEARNED.md` rule: a law unconfirmed by
a later run stays provisional, and one that never gets confirmed is demoted rather
than left standing as folklore.

Close the review so the cadence nag resets:

```bash
python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py review-mark "wk 2026-07-24: promoted backfill-skip-count rule; reverted last week's triage default (signal flat)"
```

If a week goes by without a review, `startup-nags` says so at the next session
start, so the loop cannot silently stop.

---

## 6. The solo IC: the degenerate case that still works

Take the team down to one person and nothing breaks; the mechanism collapses
cleanly. Learning stays entirely local because there is no second machine to spread
to. The gates, the lints, the evals, the autosave, the telemetry, and the weekly
review all run exactly as above. `handoff` and the correction-redaction discipline
still protect the day a solo operator hands a project to a first teammate.

Two roles that a team splits across two people, a solo IC plays with two contexts:

- **The reviewer.** With no teammate to approve the `LEARNED.md` PR, the solo dev
  is both author and merger. The review still happens: the weekly review's
  evaluator-optimizer step has a fresh-context critic session gate the edit before
  it commits, which is the reviewer role played against yourself with a clean
  context so the drafter does not grade its own homework.
- **The team repo.** A solo `LEARNED.md` can be a plain committed file rather than
  a PR target. The promotion is still a visible, dated, reasoned diff in git
  history; it simply does not wait on a second approval.

The one thing a solo install loses is the second pair of eyes on a promotion. It
keeps every other property: nothing changes behavior except a diff you can read,
and the diff carries the rule and its reasoning.

---

## 7. Governance: promotion is a visible pull request

For a Head of Data (or any lead who owns how the team's assistants behave), the
value is that **behavior change is a reviewed, dated, attributable diff in one
file.** There is no opaque "the tool learned something" that a manager has to take
on faith. The full history of what the team taught BrotherSBE is:

```bash
git -C ~/.claude/skills/brothersbe log --oneline -- memory-template/LEARNED.md
```

Every line answers who proposed a rule, who approved it, when it merged, and (from
the `because` clause) why. If a promoted law turns out wrong, reverting it is a
normal PR revert, and the weekly review's amendment-revert discipline already
watches for laws whose named signal did not move. A lead can require that
`sbe_gate.py --strict` and `sbe_score.py --strict` run in CI, so the hard gates and
the lints are enforced on the merge path and not merely advised in a session.
Governance here is not a dashboard; it is `git log` on a small set of reviewed
files, plus a CI gate anyone can read.

One naming warning the sibling's design already flagged: an unowned review loop
silently stops. Name one owner, or a rotation, for the weekly review and the
`LEARNED.md` PRs. The `startup-nags` overdue warning helps, but a named human is
the actual control.

---

## 8. Honest scope

The guarantee is over BrotherSBE's own laws, not the model or harness underneath.
A vendor model update or a Claude Code change can alter behavior with no PR into
`LEARNED.md` and no line in your git history. BrotherSBE cannot gate that; it
governs the session's conduct, not what the underlying model does or what Claude
Code transmits (`SECURITY.md`, scope note). What BrotherSBE does guarantee is
narrower and real: no rule the team adopted, and no change to how the skill's own
gates and laws behave, arrived without a diff a human reviewed and can still read.
When the model shifts under you, the weekly review is where you would notice, and
the anchored, isolated scoring is built so a quiet regression shows up as a metric
that got worse against last week's evidence rather than passing unremarked.

---

### Command reference used in this doc

```bash
# Hard gates (advisory in session; --strict blocks in CI)
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py                 # all four
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py numbers         # one class
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py --strict        # CI form

# Weekly-review checks and lints (advisory; --strict blocks in CI)
python3 ~/.claude/skills/brothersbe/tools/sbe_score.py ~/work/your-service
python3 ~/.claude/skills/brothersbe/tools/sbe_score.py --strict

# Proof the gates catch the real defects
python3 ~/.claude/skills/brothersbe/evals/run_evals.py

# Telemetry and the review loop
python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py scorecard
python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py review-mark "<summary>"
python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py startup-nags
python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py purge-corrections

# Verify the zero-network claim yourself
grep -rnE "urllib|requests|socket|http|curl|wget|subprocess" tools/
```

The receipt files the gates read live next to your change, under the directory you hand the gate:
`numbers-manifest.json`, `migration-receipt.json`, `ran-receipt.json`, and the
`APPROVAL` file plus a signed `Approved-by:` commit trailer or a `Reviewed-in:`
review id. Their exact fields are in section 2 and in `tools/sbe_gate.py`.
