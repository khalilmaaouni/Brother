# ETL builder

One artifact walked end to end: a nightly load of partner settlement files into
ledger staging, moving from overwrite-in-place to append-only batches. The full
dossier is in `examples/etl-job/`. The artifact that carries the weight here is
`07-verification.md` plus the two receipts an ETL change actually owes:
`migration-receipt.json` and `ran-receipt.json`.

Every block of output below was produced by running the command above it from the
clone root, with the dossier copied to `design/settlement-load`.

## Shortest path from a design doc to a verdict

```
mkdir -p design/settlement-load
python3 tools/sbe_intake.py design/settlement-load
# write the artifacts, ending with 07-verification.md
python3 tools/sbe_design.py design/settlement-load
python3 tools/sbe_gate.py design/settlement-load     # migration and ran
```

## Step 1: partner data means T3

```
$ printf 'y\ny\nn\ny\nsome\n' | python3 tools/sbe_intake.py design/settlement-load
Does this change a data model, an API contract, or a file interface others depend on? (y/n) Does it cross a service, system, or team boundary? (y/n) Is it reversible in under an hour? (y/n) Does it touch money, partner data, personal data, or production state? (y/n) How many downstream consumers break if it is wrong? (none/some/many) tier T3 (artifacts required: 01, 02, 03, 04, 05, 06, 07) written to design/settlement-load/00-intake.json
To override this tier, edit that file and set all three fields: "tier" (the tier you are moving to), "override" (the same tier, declaring the move), and "override_reason" (at least 3 words and 12 characters). A move with any of the three missing or disagreeing FAILs the design check as an edit rather than an override.
```

## Step 2: the verification artifact is a table of claims, each with its check

`07-verification.md` is one table. Claim, the check that proves it, when it runs.
This one:

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| Loading the same file twice creates one batch | Idempotency test running the extract twice on one file and asserting one batch row | Every pull request |
| A partial batch never reaches staging | Fault injection test failing the load midway and asserting zero rows for that batch id | Every pull request |
| Batch total matches the file trailer | Reconciliation query comparing summed amount_cents to the trailer total | Every load, blocking acceptance |
| Payout reads only accepted batches | Query asserting no payout row references a batch whose state is not accepted | Daily |
| The migration reverses cleanly | Forward and reverse both executed against a restored copy, with row counts before and after | Before the migration ships |

Writing this table is what turns the last two gates from paperwork into a
checklist you already wrote. Each row names a receipt you are going to emit.

The design checks on the full dossier:

```
$ python3 tools/sbe_design.py --strict design/settlement-load
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/settlement-load (.); 0 of 0 director(y/ies) directly under design/settlement-load contributed no dossier
  dossier: . (under design/settlement-load)
  artifacts  PASS     tier T3: every required artifact present, carrying content, and naming subject matter the rest of this dossier also names; examined . under design/settlement-load [severity: gate]
  adr        PASS     2 distinct rejected alternatives (each explicitly rejected in its own text, or listed beside an identified chosen option), each carrying at least 2 words and 8 characters of its own text (that the text says why the option lost, rather than restating its name, is human review), and criteria, decision, consequences and flip condition each carry content; examined . under design/settlement-load [severity: gate]
  datamodel  PASS     4 entities, each with a system of record; 3 relationship line(s) read, each carrying cardinality; examined . under design/settlement-load [severity: gate]
  diagrams   PASS     13 diagram node(s) in erDiagram, flowchart, stateDiagram-v2, all traceable: 4 to entities in 05-data-model.md, 5 to declared components, 4 to declared lifecycle states, 0 to a system of record an entity names; tokens read as diagram syntax rather than as nodes: erDiagram (the diagram declaration: type), flowchart LR (the diagram declaration: type and direction), stateDiagram-v2 (the diagram declaration: type); examined . under design/settlement-load [severity: gate]
  placeholder PASS     7 artifact(s) present, none still carrying an unfilled-template marker; examined . under design/settlement-load [severity: gate]
```

Worth noting for pipeline work: the diagrams check reads `stateDiagram-v2` and
traces its states to bullets under a `States` heading in `05-data-model.md`. The
batch lifecycle (received, accepted, quarantined, superseded) is declared once and
drawn once, and the check binds them together. That is four of the thirteen nodes.

## Step 3: the migration receipt, and three real failing runs

The `migration` gate wants forward and reverse both run against a restored copy, a
rehearsal run id recorded as a string, and matching row counts.

### Failing run one: the reverse was never actually run

```json
{"forward": {"ran_against_restore": true},
 "reverse": {"ran_against_restore": false, "rehearsal_run_id": "af_run_20260726_0231"},
 "row_counts": {"before": 41288, "after_reverse": 41288}}
```

```
$ python3 tools/sbe_gate.py migration design/settlement-load
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  migration FAIL     reverse: reverse is not marked as run against a restored copy (recorded False, and only the value true is that claim) [severity: gate]
```

"Only the value true is that claim." The string `"false"` would also be refused,
because the string false is truthy in Python and the tool reads for meaning
rather than for truthiness.

### Failing run two: both legs ran, and nothing counted rows

```json
{"forward": {"ran_against_restore": true},
 "reverse": {"ran_against_restore": true, "rehearsal_run_id": "af_run_20260726_0231"}}
```

```
$ python3 tools/sbe_gate.py migration design/settlement-load
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  migration NO-DATA  1 receipt(s) with both legs run against a restore, but 1 recorded no row counts (migration-receipt.json: both legs recorded but no row_counts, so nothing compared the rows the reverse was supposed to restore); the reverse restoring the rows is the half this gate cannot assert, so it does not [severity: gate]
```

This is the behaviour to show a sceptic. The receipt is valid, both legs are
marked run, and it still refuses to say PASS, because the thing that matters is
whether the reverse restored the rows and nothing measured that. It states the
half it cannot assert instead of asserting it.

### The passing run

```json
{"forward": {"ran_against_restore": true},
 "reverse": {"ran_against_restore": true, "rehearsal_run_id": "af_run_20260726_0231"},
 "row_counts": {"before": 41288, "after_reverse": 41288}}
```

```
$ python3 tools/sbe_gate.py migration design/settlement-load
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  migration PASS     1 receipt(s): forward and reverse both ran against a restore, 1 row-count comparison(s) matched, and a rehearsal id string is recorded; read 1 migration-receipt.json under design/settlement-load (migration-receipt.json); 0 of 0 director(y/ies) directly under design/settlement-load contributed no migration-receipt.json [severity: gate]
```

"A rehearsal id string is recorded." Nothing resolves that id against Airflow or
any other job system. It is a pointer for a human to follow, and the PASS line
does not pretend otherwise.

## Step 4: the ran receipt

Your pipeline tests emit `ran-receipt.json`. One entry per check, with its exit
code and duration.

### Failing run: a check that ran and did not pass

```json
{"checks": [{"name": "batch-reconciliation", "exit_code": 1, "duration_ms": 2610}]}
```

```
$ python3 tools/sbe_gate.py ran design/settlement-load
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  ran       FAIL     batch-reconciliation: check exited nonzero (1) [severity: gate]
```

A zero duration is refused too: `zero or negative duration (a check that took no
time did not run)`. That is what a silently skipped suite looks like.

### The passing run, all four gates

```
$ python3 tools/sbe_gate.py --strict design/settlement-load
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   NO-DATA  no numbers-manifest found; if this change presents no decision figure that is correct, else add one; no numbers-manifest.json read under design/settlement-load; 0 of 0 director(y/ies) directly under design/settlement-load contributed no numbers-manifest.json [severity: gate]
  migration PASS     1 receipt(s): forward and reverse both ran against a restore, 1 row-count comparison(s) matched, and a rehearsal id string is recorded; read 1 migration-receipt.json under design/settlement-load (migration-receipt.json); 0 of 0 director(y/ies) directly under design/settlement-load contributed no migration-receipt.json [severity: gate]
  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if this change touches no money or partner path that is correct; no APPROVAL read under design/settlement-load; 0 of 0 director(y/ies) directly under design/settlement-load contributed no APPROVAL [severity: gate]
  ran       PASS     3 recorded check(s), each with a zero exit and a nonzero duration; read 1 ran-receipt.json under design/settlement-load (ran-receipt.json); 0 of 0 director(y/ies) directly under design/settlement-load contributed no ran-receipt.json [severity: gate]
```

Exit 0. Under `--strict` a FAIL would exit nonzero; a `NO-DATA` never does.

## Step 5: the silent-failure linter, which is aimed at pipeline code

ETL code is where errors get swallowed. The linter reads `.py .sql .swift .rb .js
.ts .go`. The run below used a two-file directory called `sample-etl/`, holding
exactly this:

```python
# sample-etl/load_settlements.py
import subprocess


def load(batch):
    try:
        stage(batch)
    except DuplicateBatch:
        pass


def archive(path):
    subprocess.run(["mv", path, "/archive"])
```

```sql
-- sample-etl/upsert.sql
INSERT INTO ledger_staging (batch_id, amount_cents)
SELECT batch_id, amount_cents FROM settlement_raw
ON CONFLICT (batch_id) DO NOTHING;
```

A loader with an except-then-pass and an unchecked `subprocess.run`, plus a `.sql`
file with a conflict-skipping upsert. Three defects, three hits:

```
$ python3 tools/sbe_score.py sample-etl
silent-failure-lints      FAIL     3 hit(s) in 2 file(s) scanned: load_settlements.py:7 except-then-pass (swallows the error); load_settlements.py:12 discarded subprocess result without check=True (exit code is swallowed); upsert.sql:3 conflict-skipping upsert without a logged skip count [severity: gate]
```

(`sbe_score.py` prints twelve checks, grouped by whether the check opened a file
in the directory you named. Only the lint line is quoted here; the other eleven
are the vault-fed soft checks shown in `01-install-and-first-run.md`.)

The upsert rule fires on a plain `.sql` file, which is the point: it reads the SQL
wherever it is written, not only inside a Python `.execute(` call.

### The escape, and the escape that does not work

A reviewed exemption is an inline comment. The reason is read, not just matched:

```python
    except DuplicateBatch:
        pass  # sbe: allow-silent TBD
```

```
silent-failure-lints      FAIL     1 hit(s) in 1 file(s) scanned: load_settlements.py:7 except-then-pass (swallows the error) (an `sbe: allow-silent` marker with no reason after it waives nothing; write what makes this swallow legal) [severity: gate]
```

A real reason works:

```python
    except DuplicateBatch:
        pass  # sbe: allow-silent a duplicate content hash is the idempotency contract, not an error; the batch is already loaded
```

```
silent-failure-lints      NO-DATA  1 file(s) scanned under sample-etl and every match in every one of them was suppressed by an inline `sbe: allow-silent` comment (load_settlements.py:7); a scan whose every finding was waived examined nothing it was allowed to report, so it is not clean [severity: gate]
```

Note the verdict: `NO-DATA`, not clean. A scan whose every finding was waived did
not clear anything. You cannot silence your way to green.

## What it catches that a human reviewer usually misses

- **A migration whose reverse was never rehearsed.** Everyone writes the down
  migration. Almost nobody runs it against a restore, and the receipt makes the
  difference visible.
- **A rollback with no row counts.** The reverse "worked" and nothing counted what
  came back.
- **A test suite that reported success in zero milliseconds.**
- **`ON CONFLICT DO NOTHING` with no skip count.** The single most common way an
  ETL job drops rows without anybody knowing.
- **`except: pass` in a loader.** Easy to skim past; the linter names the line.

## What it cannot judge, and hands back

- Whether your transform is correct. It never runs your pipeline.
- Whether the rehearsal id points at a real job run. Nothing resolves it.
- Whether the row counts you recorded came from the run you say they came from.
  The receipt is your claim; the gate checks it is internally consistent.
- Whether your DAG dependencies are right, whether your partitioning will scale,
  whether your retry policy is sane.
- Whether the exemption reason you wrote is a good reason. It checks that you
  wrote one and that it is not a placeholder. The reason lands in the diff for a
  human to read, which is the actual control.
