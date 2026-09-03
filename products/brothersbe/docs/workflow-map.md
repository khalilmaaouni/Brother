# Workflow Map

BrotherSBE follows one engineering path from intent to evidence.

If you are unsure what to do next, use:

```text
/brothersbe:start
```

or:

```text
/brothersbe:next
```

## Workflow

```text
1. START
     |
2. INTAKE
     |
3. DESIGN
     |
4. IMPLEMENT
     |
5. REVIEW
     |
6. VERIFY
     |
7. STATUS
     |
8. HUMAN MERGE
```

## Phase 1: Start

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `/brothersbe:start` | Start or resume work | One recommended next action | The recommendation matches the actual project state |
| `/brothersbe:next` | Ask what to do next | One next action | You understand why it is next |
| `/brothersbe:help` | Explain BrotherSBE or available paths | Guidance | Use when the workflow itself is unclear |

## Phase 2: Intake

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `sbe intake design/<change>` | Score change risk using five questions | `00-intake.json` and tier | Human confirms answers and tier are reasonable |
| `sbe impact . --base origin/main` | Compare actual diff with declared risk | Impact and tier verdict | Actual changed paths do not imply higher risk than declared |

Risk controls ceremony. The person in a hurry should not decide how much engineering evidence a high-risk change owes.

## Phase 3: Design

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `/brothersbe:design` (fires on its own once `start` reaches it) | Create or review engineering design | Dossier artifacts | Humans review decisions |
| `sbe design design/<change>` | Check required design structure | PASS / FAIL / NO-DATA by design check | Resolve FAIL before implementation |

Typical design artifacts:

```text
01-purpose.md
02-process.md
03-adr.md
04-technology-map.md
05-data-model.md
06-diagrams.md
07-verification.md
```

Important human decisions include:

```text
system boundary
contract
system of record
grain
cardinality
reversibility
verification strategy
```

## Phase 4: Implement

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `/brothersbe:work` (fires on its own once a plan is ready) | Execute ready plan tasks | Scoped implementation work | Worker stays inside scope and runs named verification |
| `sbe fences` | Show live write fences | Ownership and fence view | No unexpected writer owns the same path |

Each implementation task carries:

```text
owner
scope
must-not-touch paths
dependencies
acceptance criteria
verification command
stop conditions
```

## Phase 5: Review

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `/brothersbe:review` | Review a change against the design | Specialist findings | Findings are answered |
| `sbe review-route --base origin/main` | Route review from actual diff | Reviewer roles | Appropriate specialist risk is represented |

Reviewers are read-only. Writers change the implementation.

## Phase 6: Verify

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `/brothersbe:verify` | Run or guide final verification | Verification state | Every important claim has an evidence path |
| `sbe evidence run --check <id> --out <receipt>` | Run the check registered under that id | Receipt | The registry entry, not the command line, defines what ran |
| `sbe evidence run --out <receipt> -- <command>` | Run a free-form command as advisory evidence | Receipt | Advisory only; satisfies no required policy check |
| `sbe gate <gate> <directory>` | Evaluate a hard gate | PASS / FAIL / NO-DATA | Read the verdict and evidence, not only exit colour |

Hard-gate areas:

```text
numbers
migration
approval
ran
```

Evidence states:

| State | Meaning |
| --- | --- |
| `PASS` | Evidence exists and the configured check passed |
| `FAIL` | The check failed or the evidence is broken |
| `NO-DATA` | Evidence is absent or insufficient |

`NO-DATA` is never silently converted to `PASS`.

## Phase 7: Status

| Command | Purpose | Produces | Verify |
| --- | --- | --- | --- |
| `/brothersbe:status` | Explain current project state | Human-readable status | Read blockers and missing evidence |
| `sbe status` | CLI status for engineering and CI | Verdict summary | Release commit has current evidence |
| `sbe converge design/<change> --base <approved-sha> --head HEAD` | Compare the code between two commits with the approved dossier | Convergence verdict | Implementation still matches design intent |

## Phase 8: Human merge

BrotherSBE does not merge, approve, or deploy.

Humans and your existing branch rules make the final decision.

## CI progression

Start:

```text
advisory
```

Then:

```text
measure findings
```

Then:

```text
fix noise and missing checks
```

Only then:

```text
strict selected controls
```

Do not make every BrotherSBE verdict blocking on day one. The exact step order for CI is documented in [CI-ORDER.md](CI-ORDER.md).

Next: **[Command Reference](commands.md)**
