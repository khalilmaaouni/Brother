# Backend engineer

One artifact walked end to end: adding idempotency keys to `POST /jobs` on an
internal Jobs API. The full dossier is in `examples/backend-idempotency/`.

Every block of output below was produced by running the command above it from the
clone root, with the dossier copied to `design/jobs-idempotency`.

## Shortest path from a design doc to a verdict

Four commands. Everything else is optional.

```
mkdir -p design/jobs-idempotency
python3 tools/sbe_intake.py design/jobs-idempotency     # five questions, computes the tier
# write the artifacts the tier asks for
python3 tools/sbe_design.py design/jobs-idempotency     # advisory, exits 0
python3 tools/sbe_gate.py design/jobs-idempotency       # the four receipt gates
```

## Step 1: the tier is computed, not chosen

The intake asks five questions and picks the tier from the first matching rule.
You do not decide how much design work this change owes.

```
$ printf 'y\ny\ny\nn\nsome\n' | python3 tools/sbe_intake.py design/jobs-idempotency
Does this change a data model, an API contract, or a file interface others depend on? (y/n) Does it cross a service, system, or team boundary? (y/n) Is it reversible in under an hour? (y/n) Does it touch money, partner data, personal data, or production state? (y/n) How many downstream consumers break if it is wrong? (none/some/many) tier T2 (artifacts required: 01, 02, 03, 05, 06, 07) written to design/jobs-idempotency/00-intake.json
To override this tier, edit that file and set all three fields: "tier" (the tier you are moving to), "override" (the same tier, declaring the move), and "override_reason" (at least 3 words and 12 characters). A move with any of the three missing or disagreeing FAILs the design check as an edit rather than an override.
```

Contract change plus some consumers gives T2: six artifacts, no technology map.
A one-line internal fix answers no to everything and gets T0, which owes nothing
at all. Piping the answers is how you run this from a script; interactively it
re-asks until the answer is in the accepted vocabulary rather than guessing.

## Step 2: the failing run (this is the useful half)

All six artifacts were written. The ADR listed one rejected alternative (Redis
SETNX) and no flip condition. That is what a real hurried ADR looks like.

```
$ python3 tools/sbe_design.py design/jobs-idempotency
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/jobs-idempotency (.); 0 of 0 director(y/ies) directly under design/jobs-idempotency contributed no dossier
  dossier: . (under design/jobs-idempotency)
  artifacts  PASS     tier T2: every required artifact present, carrying content, and naming subject matter the rest of this dossier also names; examined . under design/jobs-idempotency [severity: gate]
  adr        FAIL     only 1 distinct rejected alternative(s) found under a rejected-alternatives heading; an ADR needs at least 2, each with at least one line saying why it lost (an empty heading is not an alternative, and the chosen option is the decision, not an alternative). The heading (a #-heading, a bold line, or a colon-terminated lead) is accepted as anything containing any of: rejected, alternatives, alternative, options, roads not taken, not taken, not chosen, ruled out, discarded, dropped, declined, did not pick, did not choose, didn't pick, didn't choose, why not, trade-offs, tradeoffs, trade offs, trade-off, tradeoff. Accepted forms under it: a bullet list (- or *); a numbered list (1. or 2)); one sub-heading per alternative, each with a body; one paragraph per alternative; a comparison table (one row per alternative; a row whose verdict-like column says chosen is the decision, not an alternative); no 'What would flip this' section; an ADR without it is a tombstone (this heading is accepted as any of: What would flip this, What would change our mind, What would reverse this, When we would revisit this, Flip condition, Flip conditions, Revisit, Reconsider, Reversal); examined . under design/jobs-idempotency [severity: gate]
  datamodel  PASS     3 entities, each with a system of record; 2 relationship line(s) read, each carrying cardinality; examined . under design/jobs-idempotency [severity: gate]
  diagrams   PASS     5 diagram node(s) in erDiagram, flowchart, all traceable: 3 to entities in 05-data-model.md, 2 to declared components, 0 to declared lifecycle states, 0 to a system of record an entity names; 2 of the component trace(s) resolve to bullets declared in this artifact itself, so for those the declaration and the diagram are one file; a row in 04-technology-map.md is the cross-artifact form; tokens read as diagram syntax rather than as nodes: erDiagram (the diagram declaration: type), flowchart LR (the diagram declaration: type and direction); examined . under design/jobs-idempotency [severity: gate]
  placeholder PASS     6 artifact(s) present, none still carrying an unfilled-template marker; examined . under design/jobs-idempotency [severity: gate]
```

Three things worth noticing.

The message names every heading spelling it would have accepted and every list
form it would have read. It is not asking you to match a template. It read your
markdown, found one alternative, and told you what it counted. That kills the
usual argument with a linter, which is "it did not understand my format".

It also caught the missing flip condition in the same line. An ADR with no
observable condition for revisiting is a tombstone.

The `scope` line and the `; examined . under design/jobs-idempotency` clause on
every verdict are the third. The run tells you what it opened before it tells you
what it found, so a PASS can never be a verdict about a directory you did not
mean to check.

## Step 3: the corrected version

A second rejected alternative was added (each team keeps its own dedupe table,
with the reason it loses) plus a `## What would flip this` section.

```
$ python3 tools/sbe_design.py --strict design/jobs-idempotency
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/jobs-idempotency (.); 0 of 0 director(y/ies) directly under design/jobs-idempotency contributed no dossier
  dossier: . (under design/jobs-idempotency)
  artifacts  PASS     tier T2: every required artifact present, carrying content, and naming subject matter the rest of this dossier also names; examined . under design/jobs-idempotency [severity: gate]
  adr        PASS     2 distinct rejected alternatives (each explicitly rejected in its own text, or listed beside an identified chosen option), each carrying at least 2 words and 8 characters of its own text (that the text says why the option lost, rather than restating its name, is human review), and criteria, decision, consequences and flip condition each carry content; examined . under design/jobs-idempotency [severity: gate]
  datamodel  PASS     3 entities, each with a system of record; 2 relationship line(s) read, each carrying cardinality; examined . under design/jobs-idempotency [severity: gate]
  diagrams   PASS     5 diagram node(s) in erDiagram, flowchart, all traceable: 3 to entities in 05-data-model.md, 2 to declared components, 0 to declared lifecycle states, 0 to a system of record an entity names; 2 of the component trace(s) resolve to bullets declared in this artifact itself, so for those the declaration and the diagram are one file; a row in 04-technology-map.md is the cross-artifact form; tokens read as diagram syntax rather than as nodes: erDiagram (the diagram declaration: type), flowchart LR (the diagram declaration: type and direction); examined . under design/jobs-idempotency [severity: gate]
  placeholder PASS     6 artifact(s) present, none still carrying an unfilled-template marker; examined . under design/jobs-idempotency [severity: gate]
```

Exit 0. Read the adr PASS line: it says two alternatives were found, each
carrying at least two words of its own text, and then it says the interesting
part out loud. Whether the text explains why the option lost rather than
restating its name is human judgement. The tool does not claim to have judged
that.

The diagrams PASS line does the same. Two of the five nodes traced to bullets
declared in the same file as the diagram, so for those the declaration and the
diagram are one file, and it says a row in `04-technology-map.md` is the stronger
cross-artifact form.

## Step 4: the receipt gate for a backend change

The gate that matters for a service change is `ran`. Wire your integration test
runner to emit `ran-receipt.json`.

A receipt that records the check but not its time:

```
$ python3 tools/sbe_gate.py ran design/jobs-idempotency
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  ran       FAIL     idempotency-concurrency: zero or negative duration (a check that took no time did not run) [severity: gate]
```

The receipt was `{"checks": [{"name": "idempotency-concurrency", "exit_code": 0,
"duration_ms": 0}]}`. Exit code zero, name present, and it still fails. A check
that took no time did not run.

With real durations recorded:

```
$ python3 tools/sbe_gate.py design/jobs-idempotency
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  numbers   NO-DATA  no numbers-manifest found; if this change presents no decision figure that is correct, else add one; no numbers-manifest.json read under design/jobs-idempotency; 0 of 0 director(y/ies) directly under design/jobs-idempotency contributed no numbers-manifest.json [severity: gate]
  migration NO-DATA  no migration in this change, or no migration-receipt.json; no migration-receipt.json read under design/jobs-idempotency; 0 of 0 director(y/ies) directly under design/jobs-idempotency contributed no migration-receipt.json [severity: gate]
  approval  NO-DATA  no APPROVAL file and no Approved-by trailer; if this change touches no money or partner path that is correct; no APPROVAL read under design/jobs-idempotency; 0 of 0 director(y/ies) directly under design/jobs-idempotency contributed no APPROVAL [severity: gate]
  ran       PASS     2 recorded check(s), each with a zero exit and a nonzero duration; read 1 ran-receipt.json under design/jobs-idempotency (ran-receipt.json); 0 of 0 director(y/ies) directly under design/jobs-idempotency contributed no ran-receipt.json [severity: gate]
```

Three `NO-DATA`, one PASS. The three are not failures. An API change presents no
decision figure and runs no migration, so there is nothing to prove and the tool
does not tax you for it.

Notice that the PASS names the file it read, `ran-receipt.json`, and that each
`NO-DATA` names the file it looked for and did not find. That naming is what
makes a summed verdict over several dossiers readable, which is the trap
`20-what-it-will-not-tell-you.md` walks through.

## What it catches that a human reviewer usually misses

- **The second rejected alternative.** Reviewers read the decision and the
  consequences. Nobody counts alternatives, and one alternative is how a decision
  that was never really made gets recorded as one.
- **The missing flip condition.** Almost no ADR has one, and its absence is
  invisible because nothing is there to look at.
- **The entity with no system of record.** In a service dossier this is where
  "who owns this data" quietly goes unanswered.
- **A diagram that drifted from the model.** A node naming a component nobody
  declared reads fine to a human and means the two artifacts disagree.
- **A test receipt that proves nothing.** Exit code 0 with zero duration is what a
  skipped test suite looks like.

## What it cannot judge, and hands back

- Whether the design is right. It counts alternatives; it cannot tell a real one
  from a straw man.
- Whether your idempotency semantics are correct. It reads that you wrote a
  unique constraint; it does not reason about your concurrency.
- Whether the change needed an approval at all.
- Whether your integration test actually tests what its name says.

Everything in that list is stated in the tool's own verdict lines. It is not
hiding behind a green check.
