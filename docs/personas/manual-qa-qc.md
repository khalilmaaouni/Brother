# Manual QA/QC

## What Brother should optimize for

Preserve exploratory judgment, realistic scenarios, environment state, and reproducible evidence instead of reducing manual QA to AI-generated scripts.

Brother should inspect the relevant repository/platform/requirements/tests/tooling first and load this lens only where it changes the evidence plan. Do not make the practitioner choose an internal product.

## Recurring work

- exploratory testing
- UAT support
- regression charters
- cross-device/browser checks
- workflow validation
- defect reproduction
- release sign-off evidence

## Failure patterns worth catching

- scripts missing emergent workflow issues
- uncaptured environment state
- unreproducible bugs
- cases mirroring requirements but not users
- manual findings never becoming regression knowledge
- screenshots without state/steps

These are candidate risks, not a universal checklist. Discard what is irrelevant to the actual change.

## Intake pivots

Ask only when the environment cannot establish the answer and the answer can materially change the work:

- Which user journey is most costly to break?
- What changed that deserves exploration?
- Which build/environment/data state is under test?
- What evidence lets engineering reproduce the finding?
- Which escaped defect should be challenged again?

## Evidence patterns

Prefer a small set of discriminating, independent evidence over a large generated test surface:

- exploratory charter/notes
- reproducible defect steps
- build/environment identity
- media tied to state
- human/business oracle
- session coverage map
- focused post-fix regression

## Tooling posture

Reuse test management, browser/device tools, capture, issue tracker, logs, and analytics. Brother reduces admin/preserves evidence while keeping exploration human.

Rule: **detect → reuse → propose → install last**. Professional knowledge is not dependency proliferation.

## Vault knowledge worth recalling

- escaped-defect patterns
- high-risk journeys
- environment quirks
- manual oracles
- customer symptoms
- effective regression charters

Store observable symptom, constraint, context, and evidence/reference. Do not store secrets or treat memory as proof.

## Receipt expectations

A useful receipt answers: what professional invariant/risk was claimed; which evidence family tested it; where the oracle came from and how independent it was; which environment/data/revision was measured; what remains `NO-DATA`; and which decision still belongs to a human.

## Stay out of the way when

The work is small, reversible, and later evidence reconstruction would cost less than assurance ceremony now. Professional depth should make Brother more selective, not more bureaucratic.
