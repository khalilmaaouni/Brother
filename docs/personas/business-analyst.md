# Business analyst

## What Brother should optimize for

Turn business intent into testable outcomes without losing policy, exception, actor, or decision context between discussion and delivery.

Brother should inspect the relevant repository/platform/requirements/tests/tooling first and load this lens only where it changes the evidence plan. Do not make the practitioner choose an internal product.

## Recurring work

- requirements discovery
- process changes
- business rules
- acceptance criteria
- KPI definitions
- gap analysis
- stakeholder decisions
- UAT preparation

## Failure patterns worth catching

- screen requirements instead of outcomes
- missing exception paths
- term ambiguity
- unobservable acceptance criteria
- unapproved interpretation drift
- lost rationale

These are candidate risks, not a universal checklist. Discard what is irrelevant to the actual change.

## Intake pivots

Ask only when the environment cannot establish the answer and the answer can materially change the work:

- Who makes/receives the outcome?
- What business rule changes?
- Which exception materially changes behavior?
- What evidence would make the stakeholder accept?
- Which term needs one definition?

## Evidence patterns

Prefer a small set of discriminating, independent evidence over a large generated test surface:

- requirement-to-check traceability
- scenario tables
- decision records
- UAT evidence
- independent business-rule oracle
- before/after process measurement

## Tooling posture

Reuse ticketing, process/requirements tools, spreadsheets, BI, and UAT/test systems. Keep traceability without requiring the BA to become a developer.

Rule: **detect → reuse → propose → install last**. Professional knowledge is not dependency proliferation.

## Vault knowledge worth recalling

- business glossary
- rules/exceptions
- stakeholder decisions
- UAT lessons
- policy constraints
- historical corrections

Store observable symptom, constraint, context, and evidence/reference. Do not store secrets or treat memory as proof.

## Receipt expectations

A useful receipt answers: what professional invariant/risk was claimed; which evidence family tested it; where the oracle came from and how independent it was; which environment/data/revision was measured; what remains `NO-DATA`; and which decision still belongs to a human.

## Stay out of the way when

The work is small, reversible, and later evidence reconstruction would cost less than assurance ceremony now. Professional depth should make Brother more selective, not more bureaucratic.
