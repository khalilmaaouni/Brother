# Technical QA

BrotherSBE treats QA as an evidence discipline, not only as a final test phase.

The goal is to connect what the business asked for to a check that actually ran.

## The QA chain

```text
Requirement
   |
Acceptance criteria
   |
Executable test
   |
Execution
   |
Evidence
   |
Verdict
```

## Example

Requirement:

```text
Every valid order received before cut-off
must reach the downstream ordering system exactly once.
```

Acceptance criteria might include:

```text
valid order delivered
no duplicate delivery
retry does not duplicate
partial failure is recoverable
late order follows defined path
invalid order is rejected correctly
status transition remains consistent
```

## 1. Write verification before done

The design dossier should include verification intent before implementation finishes.

Use:

```text
/brothersbe:design
```

The verification artifact should answer:

```text
What claim are we making?
Which check proves it?
When does it run?
What evidence should exist afterwards?
```

## 2. Review testability

Use:

```text
/brothersbe:review
```

The QA reviewer maps requirements and acceptance criteria to executable tests.

It looks for missing coverage in areas such as:

```text
negative paths
boundary conditions
retries
timeouts
duplicate events
partial failure
schema drift
unexpected nulls
recovery
performance
non-functional requirements
```

## 3. Run real checks

Do not accept:

```text
tests passed
```

as sufficient evidence from the implementation worker.

Register the check in `.sbe/checks.yml`, then run it by id:

```bash
sbe evidence run --check order-exactly-once --out .sbe/evidence/order-exactly-once.json
```

The registry entry, not the command line, defines what runs. That is the difference between a receipt and a claim.

## 4. Read the evidence state

```text
PASS
```

The configured evidence exists and passed.

```text
FAIL
```

The check failed or the evidence is broken.

```text
NO-DATA
```

The evidence required to make the claim is absent or insufficient.

A skipped test should not be mentally converted into a pass.

## 5. Separate reviewer from writer

BrotherSBE's specialist reviewers are read-only.

That separation matters.

Bad loop:

```text
AI writes code
AI reviews itself
AI edits its own findings
AI declares success
```

Preferred loop:

```text
writer implements
reviewer challenges
writer fixes
QA executes proof
CI records result
human decides
```

## 6. Verify traceability

Before merge, QA should be able to answer:

```text
Which requirement is this test proving?
Which command ran?
Against which commit?
What did it inspect?
Did it actually execute?
What evidence exists?
What remains unmeasured?
```

## 7. Final status

Use:

```text
/brothersbe:status
```

QA reads both:

```text
blockers
```

and:

```text
missing evidence
```

A green pipeline with `NO-DATA` still contains uncertainty.

The purpose of BrotherSBE is to make that uncertainty visible.
