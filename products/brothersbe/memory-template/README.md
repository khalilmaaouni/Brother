# BrotherSBE memory template

This folder is the starting shape of a BrotherSBE vault. Copy it once to the path
you point `BROTHERSBE_VAULT` at (default `~/BrotherSBEVault`), then let the tools
and the session laws fill it in over time. It is a skeleton, not a filled vault:
every dated line here is a placeholder that will not be counted until you replace
it with a real date.

```bash
cp -R memory-template ~/BrotherSBEVault
export BROTHERSBE_VAULT="$HOME/BrotherSBEVault"    # put this in your shell profile
```

The vault is where the skill keeps what it must not forget between sessions:
the shape of each project, what is still open, what has bitten before, what was
decided and why, a one-line ledger of every substantial run, and the team laws
that spread from one install to the next. Some of it you write by hand at
milestones and session end; some of it the hooks write for you.

## Why the layout is exactly this

The paths are not decorative. `tools/sbe_telemetry.py` and `tools/sbe_score.py`
read specific files at specific globs, so the folder names below are a contract,
not a suggestion. If you rename `10-Projects` or move `OUTCOMES.md`, the speed
feed and the weekly checks go quiet (NO-DATA, never a false pass, but quiet).

```
~/BrotherSBEVault/                     <- BROTHERSBE_VAULT points here
  LEARNED.md                           team laws; the one file that spreads between installs
  .gitignore                           keeps local telemetry out of any shared repo
  10-Projects/
    _TEMPLATE/                         copy this per project
      Overview.md                      project shape, stack, invariants
      Open-Items.md                    what is still open, owner and next step
      Failures-Index.md                what bit before; read BEFORE working an area
      Decisions.md                     dated decisions, newest first
      OUTCOMES.md                      one line per substantial run (read by the speed feed)
      Sessions/
        YYYY-MM-DD-<slug>.md           one human session log per work session
  50-Reference/
    operator-model.md                   optional prediction ledger (read by prediction-audit)
  99-System/
    telemetry/                         LOCAL ONLY, gitignored, created on demand by the tools
      outcomes.jsonl                   one line per real session (SessionEnd hook)
      ratings.jsonl                    felt-outcome scores you record with `rate`
      reviews.jsonl                    weekly-review markers
      corrections.jsonl               correction candidates, secret-redacted, 0600
```

Two rules about that tree:

1. `99-System/telemetry/` is machine-local and gitignored. It holds per-session
   counts and short excerpts of your own messages. It never enters a shared repo.
   The shipped `.gitignore` (below) enforces this. `SECURITY.md` in the skill root
   explains exactly what those files contain and how to purge them
   (`python3 tools/sbe_telemetry.py purge-corrections`).
2. `LEARNED.md` is the opposite: it is meant to travel. A lesson becomes a law by
   a reviewed pull request that adds a line to `LEARNED.md` in the team repo, and
   every install reads it on session start. Raw telemetry stays home; the distilled
   rule is what a teammate reviews and merges.

## What the tools actually read (so you know why the format matters)

You do not have to memorize this, but when a field looks fussy, this is why:

- `OUTCOMES.md`: `sbe_telemetry.py speed` counts lines matching a leading
  `YYYY-MM-DD | ` (a real four-digit-year date, then a space, a pipe, a space).
  Placeholder rows that start with the literal text `YYYY-MM-DD` are ignored, so a
  fresh template reports NO-DATA instead of inventing a run.
- `Sessions/*.md`: the `stop-warn` hook and the `vault-log-per-active-day` check
  look for a session log dated today (by filename date `^YYYY-MM-DD` or by file
  mtime). A day with recorded telemetry and no session log gets a nag.
- `50-Reference/operator-model.md`: `sbe_telemetry.py prediction-audit` counts rows
  under a `## Prediction ledger` heading that start with `20` and carry at least
  five pipe-separated fields, sealed when the third field is not `n/a` or empty,
  scored when the fifth starts with `yes`/`hit`/`no`/`miss`. This is optional; skip
  the file and prediction-audit simply reports zero.

None of these tools block work. Every path exits 0. A missing file is NO-DATA, a
present-but-empty file is NO-DATA, and NO-DATA is never a pass.

## The read-at-start / write-at-end loop

Section 11 of the skill fixes the rhythm. Concretely:

Session start (some of this the SessionStart hook injects for you):
- Read the project `Overview.md`, `Open-Items.md`, `Failures-Index.md`, and the
  root `LEARNED.md`. If any is missing, say so in one line and continue; a memory
  gap never blocks the work.

During the run, at milestones (plan approved, gate green, number confirmed):
- Append to `Failures-Index.md` the moment a failure class is understood, not at
  the end when you have forgotten the detail.
- Append a `Decisions.md` line when a choice is made that a later session would
  otherwise re-litigate.

Session end:
- Write one `Sessions/YYYY-MM-DD-<slug>.md` log.
- Update `Open-Items.md` (close what closed, add what opened).
- Add one `OUTCOMES.md` line if the run was substantial.
- If a lesson is worth making a team law, open a PR that adds it to `LEARNED.md`.

## Worked example: one substantial run, end to end

Say the run was a warehouse rollup change (WAREHOUSE profile) that shipped one
decision figure and one reconciliation. The hard gates that apply are `numbers`
and `ran`. You verify BEFORE you claim done:

```bash
# from the repo worktree that holds the receipts
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py numbers .
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py ran .
# CI runs the same thing enforcing:
python3 ~/.claude/skills/brothersbe/tools/sbe_gate.py --strict
```

The `numbers` gate reads a `numbers-manifest.json` next to the change. A figure
passes only with a pinned snapshot, a textually different second derivation, and a
re-run at zero drift:

```json
{
  "figures": [
    {
      "label": "gmv_2026_q2",
      "snapshot_id": "snap_2026_07_25",
      "query": "SELECT SUM(amount) FROM orders WHERE quarter = '2026Q2'",
      "second_derivation": "SELECT SUM(qty * unit_price) FROM order_lines WHERE quarter = '2026Q2'",
      "rerun": { "ran": true, "primary": 17570, "secondary": 17570 }
    }
  ]
}
```

If `primary` and `secondary` differ, the gate prints `DRIFT primary=... secondary=...`
and FAILs. If `second_derivation` is a copy of `query`, it FAILs for
non-independence. If `snapshot_id` is missing, it FAILs because a live warehouse
drifts under an unpinned read.

The `ran` gate reads a `ran-receipt.json`: a check is real only with a zero exit
code and a nonzero duration.

```json
{
  "checks": [
    { "name": "row_parity_orders_vs_lines", "exit_code": 0, "duration_ms": 812 }
  ]
}
```

A check with `duration_ms` of `0` or a missing `exit_code` FAILs: a check that
took no time did not run.

Now the memory write-back. The `OUTCOMES.md` line records the run so the speed feed
can see it (real date, leading pipe format):

```
2026-07-25 | GMV Q2 rollup + reconciliation | WAREHOUSE | numbers PASS, ran PASS | 2 | none | 2024 backfill still pending
```

Read left to right: date, task, profile, gates run with verdicts, loops to green,
overrides taken, honest remaining. That last column is load-bearing: an unstated
gap is a failure by section 13.

A migration or a money path would add its own gate and column. A migration ships a
`migration-receipt.json` the `migration` gate checks:

```json
{
  "forward": { "ran_against_restore": true },
  "reverse": { "ran_against_restore": true, "rehearsal_run_id": "job_8842" },
  "row_counts": { "before": 100, "after_reverse": 100 }
}
```

A money or partner change ships an `APPROVAL` file declaring it touches that path,
plus a named human approval the agent cannot forge: a signed commit trailer
`Approved-by:` on a signed commit. A `Reviewed-in:` platform review id is the
keyless alternative and reports NO-DATA, because nothing resolves it. A typed
name with no signature FAILs the `approval` gate. That override, if you ever take
one, is never silent: it is named, carries a reason, and is visible in the diff
and on the tool's printed verdict line, and it is never available on the
`--strict` CI path.

## Starter files (paste these, or just copy the folder)

The files below already exist in this template folder. They are shown here so you
can see the exact format each one expects. Keep them terse: memory is a query, not
a tour, and every line you add is a line a future session has to read.

### LEARNED.md (vault root; the file that spreads by PR)

```markdown
# LEARNED: team laws promoted by pull request

The one file that travels between installs. A lesson earns a place here only after
a reviewed PR merges it; every install reads it at session start. Keep each entry
three lines: the LESSON (what happened, one line), the rule (what to do now), and
the because clause (the cost that makes the rule worth its weight). Distilled law
only, never raw telemetry.

Format:

    LESSON: <what went wrong or what was learned, one line>
    RULE:   <the specific, checkable thing to do or not do>
    BECAUSE: <the concrete cost that justifies the rule>

Worked example (delete once you have your own):

    LESSON: a five-year total was filed that overstated the sum of its own yearly components.
    RULE:   every decision figure ships a numbers-manifest with a textually independent second derivation, re-run to zero drift against a pinned snapshot.
    BECAUSE: a wrong number looks exactly like a right one, and this one reached a filing before anyone re-derived it.

## Laws
<!-- add promoted lessons below, newest first, three lines each -->
```

### .gitignore (vault root; keeps local telemetry out of shared repos)

```gitignore
# Local telemetry: per-session counts and short excerpts of your own messages.
# Machine-local, owner-only, best-effort secret-redacted. It never leaves this
# machine and never enters a shared repo. See SECURITY.md in the skill root.
99-System/telemetry/

# Editor and OS noise
.DS_Store
*.swp
```

### 10-Projects/_TEMPLATE/Overview.md

```markdown
# Overview: <project name>

One paragraph: what this project is and who depends on it.

## Shape
- Kind: <backend service | warehouse + SQL | pipeline | data quality | infrastructure | performance>
- Repo(s): <paths or urls>
- Entry points: <the seams a change usually touches>

## Stack
- Languages / frameworks: <...>
- Data stores / warehouse: <engine, dataset, snapshot convention>
- Build: <exact command, copied verbatim from the repo>
- Tests: <exact command>
- CI gate: <what must be green to merge; note if sbe_gate.py --strict runs here>

## Invariants (the things that must stay true)
- <a rule a change must never break, and how it is checked>
- <the blast-radius line: what no agent may apply to production>
```

### 10-Projects/_TEMPLATE/Open-Items.md

```markdown
# Open items: <project name>

Newest first. One line each: what, owner, next concrete step. Close it here the
moment it closes; a stale open item is worse than none.

- [ ] <item> | owner <name> | next: <the smallest next action>
```

### 10-Projects/_TEMPLATE/Failures-Index.md

```markdown
# Failures index: <project name>

READ THIS BEFORE WORKING AN AREA. One line per failure class that has cost real
time here, so it is never rediscovered. Add the moment a failure is understood,
not at session end.

Format: <area> | <the failure> | <the guard that now prevents it>

- <area> | <what went wrong, one line> | <the check or habit that catches it now>
```

### 10-Projects/_TEMPLATE/Decisions.md

```markdown
# Decisions: <project name>

Dated, one line each, newest first. A decision supersedes its predecessors; the
superseded line is noise from that moment. Record the choice and the why, not the
whole debate.

- YYYY-MM-DD: <the decision> | because <the reason it beat the alternative>
```

### 10-Projects/_TEMPLATE/OUTCOMES.md

```markdown
# Outcomes ledger: <project name>

One line per substantial run. The speed feed counts lines that begin with a real
YYYY-MM-DD date, a space, a pipe, a space. The placeholder row below is ignored
until you replace it.

Columns: date | task | profile | gates run (with verdicts) | loops to green | overrides | honest remaining

YYYY-MM-DD | <task, one line> | <profile> | <e.g. numbers PASS, ran PASS> | <n> | <none or the named override> | <what is still not done>
```

### 10-Projects/_TEMPLATE/Sessions/ (one log per work session)

Name each log `YYYY-MM-DD-<slug>.md`. A minimal, honest log:

```markdown
# YYYY-MM-DD <slug>

Objective: <one line>.

Done:
- <what landed, with the verifying command and its result>

Gates:
- <gate>: <PASS | FAIL | NO-DATA> (<evidence>)

Remaining / unverified:
- <what is still open, only sampled, or assumed>

Next session starts with:
- <the first concrete step>
```

### 50-Reference/operator-model.md (optional prediction ledger)

```markdown
# Operator model

Notes on how the operator works, what they value, what they have corrected. Kept
so the skill stops relearning the same preference every week.

## Prediction ledger

Seal a prediction BEFORE you make a recommendation, so calibration is measurable.
prediction-audit counts rows that start with a real 20xx date and carry five
pipe-separated fields; sealed when the third field is a date (not n/a), scored
when the outcome starts with yes/hit/no/miss. The placeholder row is not counted.

Columns: date | claim | sealed-on | confidence | outcome

YYYY-MM-DD | <the falsifiable claim> | YYYY-MM-DD | <low/med/high> | <pending, then yes/no>
```

## Keeping it a template

Everything above ships empty on purpose. Placeholder dates (`YYYY-MM-DD`) do not
match the tools' real-date patterns, so a fresh copy reports NO-DATA everywhere
rather than pretending to have history. Replace the placeholders with real dates
as real runs happen, and the ledgers, nags, and the weekly review come to life on
their own.

Maintained by Khalil Maaouni, Founder. Corrections and additions to `LEARNED.md`
go through a pull request so the law a teammate relies on is the law a human
reviewed.
```