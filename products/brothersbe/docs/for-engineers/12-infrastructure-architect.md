# Infrastructure architect

One artifact walked end to end: moving the public API tier from one region to two,
active-passive. The full dossier is in `examples/infra-topology/`. The two
artifacts that carry the weight here are `04-technology-map.md` and
`03-adr.md`, plus the `approval` gate.

Every block of output below was produced by running the command above it from the
clone root, with the dossier copied to `design/two-region`.

## Shortest path from a design doc to a verdict

```
mkdir -p design/two-region
python3 tools/sbe_intake.py design/two-region
# write the artifacts, including 04-technology-map.md and 03-adr.md
python3 tools/sbe_design.py design/two-region
python3 tools/sbe_gate.py approval design/two-region
```

## Step 1: topology work is T3

```
$ printf 'n\ny\nn\ny\nmany\n' | python3 tools/sbe_intake.py design/two-region
Does this change a data model, an API contract, or a file interface others depend on? (y/n) Does it cross a service, system, or team boundary? (y/n) Is it reversible in under an hour? (y/n) Does it touch money, partner data, personal data, or production state? (y/n) How many downstream consumers break if it is wrong? (none/some/many) tier T3 (artifacts required: 01, 02, 03, 04, 05, 06, 07) written to design/two-region/00-intake.json
To override this tier, edit that file and set all three fields: "tier" (the tier you are moving to), "override" (the same tier, declaring the move), and "override_reason" (at least 3 words and 12 characters). A move with any of the three missing or disagreeing FAILs the design check as an edit rather than an override.
```

Note the no answer to the contract question. It still lands at T3, because it
touches production state and is not reversible in an hour. Any one of those
conditions is enough.

## Step 2: the technology map is the artifact that carries the topology

`04-technology-map.md` is a table: component, technology, owner, failure mode,
recovery path. Then source systems, then recovery posture with a recovery time
objective, a recovery point objective, and the drill that proves them.

The interesting property is that this table is also the **declaration namespace
for your diagrams**. A node in a mermaid diagram must trace to something: an
entity in `05-data-model.md`, a declared lifecycle state, a system of record an
entity names, or a component declared here.

## Step 3: the failing run

The failover topology diagram named `GlobalAcceleratorEdge`. Real component, real
part of the path, and not in the technology map because the map was written first
and the diagram was drawn after.

```
$ python3 tools/sbe_design.py design/two-region
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/two-region (.); 0 of 0 director(y/ies) directly under design/two-region contributed no dossier
  dossier: . (under design/two-region)
  artifacts  PASS     tier T3: every required artifact present, carrying content, and naming subject matter the rest of this dossier also names; examined . under design/two-region [severity: gate]
  adr        PASS     2 distinct rejected alternatives (each explicitly rejected in its own text, or listed beside an identified chosen option), each carrying at least 2 words and 8 characters of its own text (that the text says why the option lost, rather than restating its name, is human review), and criteria, decision, consequences and flip condition each carry content; examined . under design/two-region [severity: gate]
  datamodel  PASS     3 entities, each with a system of record; 2 relationship line(s) read, each carrying cardinality; examined . under design/two-region [severity: gate]
  diagrams   FAIL     diagram element(s) trace to no declared entity, component, state or system of record this check read: GlobalAcceleratorEdge. Every node must be an entity in 05-data-model.md, a system of record an entity there names, or a declared component (a row in 04-technology-map.md, or a bullet under a Components heading in 04-technology-map.md or 06-diagrams.md), matched on the node id or on its label. A state diagram's states trace to states declared as bullets under a States, Status or Lifecycle heading, or to a `status: draft | placed | shipped` line in 05-data-model.md; tokens read as diagram syntax rather than as nodes: erDiagram (the diagram declaration: type), flowchart LR (the diagram declaration: type and direction); examined . under design/two-region [severity: gate]
  placeholder PASS     7 artifact(s) present, none still carrying an unfilled-template marker; examined . under design/two-region [severity: gate]
```

This is the check that earns its keep for an architect. A diagram node nobody
declared is drift between the picture and the design, and it is easy to miss
because a diagram with a plausible-looking box reads as correct.

The message names both escapes. The strong one is a row in the technology map.
The quick one is a bullet under a `Components` heading in the diagram file
itself, and the tool discloses when you take it: on the backend example the PASS
line said "2 of the component trace(s) resolve to bullets declared in this
artifact itself, so for those the declaration and the diagram are one file; a row
in 04-technology-map.md is the cross-artifact form."

## Step 4: the corrected version

One row was added to `04-technology-map.md`:

```
| GlobalAcceleratorEdge | Anycast edge terminating TLS in front of DNS | Platform | Edge health checks disagree with regional health | Edge follows the traffic director, it never decides the active region |
```

```
$ python3 tools/sbe_design.py --strict design/two-region
BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; WAIVED is not a pass either)
  scope      -        read 1 dossier under design/two-region (.); 0 of 0 director(y/ies) directly under design/two-region contributed no dossier
  dossier: . (under design/two-region)
  artifacts  PASS     tier T3: every required artifact present, carrying content, and naming subject matter the rest of this dossier also names; examined . under design/two-region [severity: gate]
  adr        PASS     2 distinct rejected alternatives (each explicitly rejected in its own text, or listed beside an identified chosen option), each carrying at least 2 words and 8 characters of its own text (that the text says why the option lost, rather than restating its name, is human review), and criteria, decision, consequences and flip condition each carry content; examined . under design/two-region [severity: gate]
  datamodel  PASS     3 entities, each with a system of record; 2 relationship line(s) read, each carrying cardinality; examined . under design/two-region [severity: gate]
  diagrams   PASS     8 diagram node(s) in erDiagram, flowchart, all traceable: 3 to entities in 05-data-model.md, 5 to declared components, 0 to declared lifecycle states, 0 to a system of record an entity names; tokens read as diagram syntax rather than as nodes: erDiagram (the diagram declaration: type), flowchart LR (the diagram declaration: type and direction); examined . under design/two-region [severity: gate]
  placeholder PASS     7 artifact(s) present, none still carrying an unfilled-template marker; examined . under design/two-region [severity: gate]
```

Exit 0. Forcing the diagram and the technology map to name the same components is
the point. It is not a formatting rule.

## Step 5: the decision record, and the flip condition

`03-adr.md` needs context, criteria with values observed on your estate, at least
two rejected alternatives each with a reason, a decision, consequences, and a flip
condition. For a topology decision the flip condition is the part that ages: this
one says revisit toward active-active if replication lag stops fitting the
recovery point objective, and revisit downward to cold standby if the business
accepts a multi-hour outage.

The tool checks that section exists and carries content. It does not check that
the condition is observable. That is on you and your reviewer.

## Step 6: the approval gate, and why it is honest about itself

A topology change touching production state declares an approval by carrying an
`APPROVAL` file. Here is one:

```
$ python3 tools/sbe_gate.py approval design/two-region
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  approval  FAIL     APPROVAL (of 1 APPROVAL file(s) read) declares 'Promoting the passive region touches production state on the', but approval is a typed name with no signature or review id; a name in a text field is not a control (add a signed Approved-by trailer or a Reviewed-in review id) [severity: gate]
```

Declaring an approval and typing a name is not an approval. It names the file it
read, says how many it read in total, and quotes your own declaration back at you.

Three other real runs, from a git repository with committed history:

**A recorded review id.** The honest verdict is `NO-DATA`, not PASS.

```
$ python3 tools/sbe_gate.py approval <repo>
BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass)
  approval  NO-DATA  commit records Reviewed-in: PLATFORM-4471 on the infrastructure review board. This gate read a trailer out of a commit message and does not resolve the id against any review platform, so it points a human at a review rather than proving one happened. That is a pointer, not a control: resolve the id in CI (a job that queries your review platform) or sign the commit, and this becomes a verdict [severity: gate]
```

Read that carefully. Most tools would call this a pass. It tells you what it read,
what it did not do, and exactly how to make it a control.

**Self-approval.** The same person authored the commit and wrote their own name in
the trailer:

```
  approval  FAIL     the Approved-by identity is the identity that wrote the commit (dana@example.com); author and committer are Dana Author, dana@example.com. Self-approval is not approval: a signature proves a key holder signed, and it cannot prove a second party reviewed. A trailing period, a reordered name, an initial, a plus-address or a role suffix does not make a second person. A second person must review: have them amend and sign with their OWN key and record their email in the trailer (the signature's principal is then the approver's email, and this gate reads that committer as the approver who signed), or record a Reviewed-in id pointing at a review somebody else performed [severity: gate]
```

**A placeholder approver.** `Approved-by: TBD`:

```
  approval  FAIL     the Approved-by trailer records 'TBD', which names no identity; a placeholder where an approver belongs is a broken claim, not an approval [severity: gate]
```

The only path to PASS is a signed `Approved-by:` trailer that **this host
verified against a key it trusts**. A valid signature whose key matched no trusted
principal reports `NO-DATA`, which is what a self-generated SSH key produces. In
CI that means importing the approvers' public keys into the job, or accepting the
`NO-DATA` and enforcing review on your platform instead.

## What it catches that a human reviewer usually misses

- **Diagram drift.** A box in the topology diagram that no artifact declares.
  Almost every architecture discussion has one and nobody sees it.
- **The missing flip condition.** A topology decision with no revisit trigger
  becomes permanent by default.
- **A recovery posture with no drill.** The template's recovery section asks for
  the drill that proves the objectives, not just the objectives.
- **Self-approval on a production change.** A signature is not a second pair of
  eyes, and the gate refuses to conflate them.
- **An approval that is a name in a box.**

## What it cannot judge, and hands back

- Whether the topology is sound. It does not evaluate failover semantics, split
  brain risk, or your replication configuration.
- Whether your recovery time objective is achievable. It reads that you wrote 15
  minutes.
- Whether your infrastructure as code matches the technology map. It reads
  markdown, not Terraform state.
- Whether the change needed an approval. Only whether a declared one is bound to
  something.
- Whether a `Reviewed-in:` id exists. Nothing resolves it.
