# Data engineer

One artifact walked end to end: a daily revenue mart rebuilt from billing exports,
with reconciliation blocking publication. The full dossier is in
`examples/data-warehouse/`. The centrepiece is `05-data-model.md`, which is the
artifact the `datamodel` check reads, plus the `numbers` gate on the figure the
mart publishes.

Every block of output below was produced by running the command above it from the
clone root, with the dossier copied to `design/revenue-mart`.

## Shortest path from a design doc to a verdict

```
mkdir -p design/revenue-mart
python3 tools/sbe_intake.py design/revenue-mart
# write the artifacts, including 05-data-model.md
python3 tools/sbe_design.py design/revenue-mart      # five design checks
python3 tools/sbe_gate.py numbers design/revenue-mart # the figure gate
```

## Step 1: warehouse work lands at T3

```
$ printf 'y\ny\nn\ny\nmany\n' | python3 tools/sbe_intake.py design/revenue-mart
Does this change a data model, an API contract, or a file interface others depend on? (y/n) Does it cross a service, system, or team boundary? (y/n) Is it reversible in under an hour? (y/n) Does it touch money, partner data, personal data, or production state? (y/n) How many downstream consumers break if it is wrong? (none/some/many) tier T3 (artifacts required: 01, 02, 03, 04, 05, 06, 07) written to design/revenue-mart/00-intake.json
To override this tier, edit that file and set all three fields: "tier" (the tier you are moving to), "override" (the same tier, declaring the move), and "override_reason" (at least 3 words and 12 characters). A move with any of the three missing or disagreeing FAILs the design check as an edit rather than an override.
```

Money plus not reversible in an hour gives T3, which is all seven artifacts. Most
warehouse work will land here. That is the cost, stated up front, and it is why
T0 exists for everything else.

## Step 2: the data model artifact and the failing run

`05-data-model.md` carries conceptual entities, relationships with cardinality,
attribute roles, historization, source systems with failover, and the physical
layer. The check reads two things hard: every entity names its system of record,
and every relationship line carries a cardinality.

The first pass declared five entities. Four named their system of record.
`Refund` did not, because it is obviously billing and nobody writes down the
obvious thing.

```
$ python3 tools/sbe_design.py design/revenue-mart
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/revenue-mart (.); 0 of 0 director(y/ies) directly under design/revenue-mart contributed no dossier
  dossier: . (under design/revenue-mart)
  artifacts  PASS     tier T3: every required artifact present, carrying content, and naming subject matter the rest of this dossier also names; examined . under design/revenue-mart [severity: gate]
  adr        PASS     2 distinct rejected alternatives (each explicitly rejected in its own text, or listed beside an identified chosen option), each carrying at least 2 words and 8 characters of its own text (that the text says why the option lost, rather than restating its name, is human review), and criteria, decision, consequences and flip condition each carry content; examined . under design/revenue-mart [severity: gate]
  datamodel  FAIL     entity 'Refund' does not name the system that owns it (accepted as any of: system of record, system of truth, source of truth, book of record, authoritative source, mastered by, owned by, owner, sor, as `<phrase>: the OMS` on the bullet, or as a table column headed with one of them); examined . under design/revenue-mart [severity: gate]
  diagrams   PASS     10 diagram node(s) in erDiagram, flowchart, all traceable: 5 to entities in 05-data-model.md, 5 to declared components, 0 to declared lifecycle states, 0 to a system of record an entity names; tokens read as diagram syntax rather than as nodes: erDiagram (the diagram declaration: type), flowchart LR (the diagram declaration: type and direction); examined . under design/revenue-mart [severity: gate]
  placeholder PASS     7 artifact(s) present, none still carrying an unfilled-template marker; examined . under design/revenue-mart [severity: gate]
```

The message lists every phrasing it would have accepted, including a table column
heading. It is reading your prose, not demanding a schema.

The fix is one clause on one bullet:

```
- Refund: money returned against an invoice, in whole or in part; system of record: the billing system.
```

```
$ python3 tools/sbe_design.py datamodel design/revenue-mart
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/revenue-mart (.); 0 of 0 director(y/ies) directly under design/revenue-mart contributed no dossier
  dossier: . (under design/revenue-mart)
  datamodel  PASS     5 entities, each with a system of record; 4 relationship line(s) read, each carrying cardinality; examined . under design/revenue-mart [severity: gate]
```

Note what the PASS asserts: five entities, each with a system of record, and four
relationship lines each carrying cardinality. Nothing about whether the model is
right. Note also that naming one check still prints the `scope` line, so a
single-check run cannot hide which directory it opened.

## Step 3: the numbers gate, which is the one built for you

Every figure that could reach a decision needs a second derivation and a pinned
snapshot. `numbers-manifest.json`:

```json
{"figures": [{
  "label": "march_recognised_revenue_cents",
  "snapshot_id": "snap_2026_03_31",
  "query": "SELECT SUM(amount_cents) FROM revenue_event WHERE recognised_on >= '2026-03-01' AND recognised_on < '2026-04-01'",
  "second_derivation": "SELECT SUM(i.amount_cents) - COALESCE(SUM(r.refunded_cents), 0) FROM invoice i LEFT JOIN refund r ON r.invoice_id = i.invoice_id WHERE i.issued_on >= '2026-03-01' AND i.issued_on < '2026-04-01'",
  "rerun": {"ran": true, "primary": 84213977, "secondary": 84213977}
}]}
```

### Failing run one: the second derivation is the first one again

The tempting shortcut is to paste the query back with a comment. That is exactly
what was tried here: same SQL, lowercased, with `-- rerun 2026-04-02` on the end.

```
$ python3 tools/sbe_gate.py numbers design/revenue-mart
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   FAIL     march_recognised_revenue_cents: the second derivation is the first one again (it differs only in case, whitespace, comments or trailing punctuation, if at all), so nothing independent re-derived this figure [severity: gate]
```

It strips comments, case, whitespace and trailing punctuation before comparing.
A cosmetic edit does not buy the strongest sentence the tool prints.

### Failing run two: the snapshot is a placeholder

With a genuinely different second derivation but `"snapshot_id": "TODO"`:

```
$ python3 tools/sbe_gate.py numbers design/revenue-mart
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   FAIL     march_recognised_revenue_cents: no snapshot_id recorded ('TODO'); a live warehouse drifts, so pin the read. A placeholder is not a pin [severity: gate]
```

This is the behaviour worth remembering. `TODO` is not blank. It parses, the key
is present, the field is non-empty, and the tool still refuses it and quotes the
value back. The same test applies to `TBD`, `pending`, `n/a`, `???`, `t.b.d.`,
`TODO(dana)` and a wrapped `[TBD]`.

### The passing run

```
$ python3 tools/sbe_gate.py design/revenue-mart
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   PASS     1 figure(s) each pinned to a snapshot, with a second derivation whose text differs beyond case, whitespace and comments, re-run to zero drift; read 1 numbers-manifest.json under design/revenue-mart (numbers-manifest.json); 0 of 0 director(y/ies) directly under design/revenue-mart contributed no numbers-manifest.json [severity: gate]
  migration NO-DATA  no migration in this change, or no migration-receipt.json; no migration-receipt.json read under design/revenue-mart; 0 of 0 director(y/ies) directly under design/revenue-mart contributed no migration-receipt.json [severity: gate]
  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if this change touches no money or partner path that is correct; no APPROVAL read under design/revenue-mart; 0 of 0 director(y/ies) directly under design/revenue-mart contributed no APPROVAL [severity: gate]
  ran       NO-DATA  no ran-receipt.json; a SQL or pipeline change is not done until its check executed and left a receipt; no ran-receipt.json read under design/revenue-mart; 0 of 0 director(y/ies) directly under design/revenue-mart contributed no ran-receipt.json [severity: gate]
```

Read the PASS sentence exactly. "A second derivation whose text differs beyond
case, whitespace and comments." That is all it verified. It does not know your two
queries read different tables. Renaming an alias would pass. Text difference is
the floor, and the tool says so in the sentence rather than in a footnote.

The rest of the PASS line is the scope: one manifest read, named, from a
directory with no subdirectories that could have contributed another.

## Deciding the shape, when you are still deciding

`sbe_decide.py` scores architecture shape against named criteria. It reads
criteria on stdin, so pipe them from a script or a CI job:

```
$ printf '3\nstrong\nmedium\nhigh\n' | python3 tools/sbe_decide.py tables/architecture.json shape
deploying_teams (Independently deploying teams. Services below four teams usually cost more than they return.): consistency (Strong consistency across a service boundary is expensive and often accidental.): ops_maturity (On-call, tracing, and CI maturity. Without them a distributed estate is undebuggable.): failure_isolation (Does one component failing have to leave the others running?): 
Recommendation: modular monolith
Alternatives: services, event-driven
Decided by:
  - deploying_teams=3 favours modular monolith, event-driven
  - consistency=strong favours monolith, modular monolith
  - ops_maturity=medium favours modular monolith, services
  - failure_isolation=high favours services, event-driven
What would flip this: Cross four independently deploying teams, or need one module to fail without the others while ops maturity is high, and revisit this decision.
```

Answer nothing and it refuses to recommend:

```
$ printf '\n\n\n\n' | python3 tools/sbe_decide.py tables/architecture.json shape
deploying_teams (Independently deploying teams. Services below four teams usually cost more than they return.): consistency (Strong consistency across a service boundary is expensive and often accidental.): ops_maturity (On-call, tracing, and CI maturity. Without them a distributed estate is undebuggable.): failure_isolation (Does one component failing have to leave the others running?): 
NO-DATA: no criterion was answered, so no recommendation can be made.
```

The thresholds in `tables/architecture.json` were measured on one estate. They are
defaults, not measurements of yours. Edit the table.

## What it catches that a human reviewer usually misses

- **An entity with no system of record.** The one question that decides every
  future argument about a number, and the one nobody writes down.
- **A relationship with no cardinality.** Reads fine in prose, and it is where a
  fan-out bug is born.
- **A copy-pasted second derivation.** The most common way a reconciliation gets
  faked, and the hardest to spot because both queries look correct.
- **An unpinned read.** A figure computed against a live warehouse cannot be
  reproduced, and nothing in a pull request shows that.
- **A placeholder that parses.** `TODO` in a receipt field passes every JSON
  schema validator on earth.

## What it cannot judge, and hands back

- Whether your grain is right, whether your join fans out, whether your
  historization actually prevents leakage. It reads that you declared these; it
  does not compute over your data.
- Whether the two derivations are genuinely independent. Text difference only.
- Whether the figure is correct. It checks that primary equals secondary, which
  is a consistency check between two queries you wrote, not a truth check.
- Anything about your dbt models, your DAG structure, or your warehouse
  performance. It never connects to a database.
