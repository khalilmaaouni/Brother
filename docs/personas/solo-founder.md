# Solo founder

## What Brother should optimize for

Ship fast without confusing AI-generated velocity with production readiness, while keeping operating burden small enough for one person.

Brother should inspect the relevant repository/platform/requirements/tests/tooling first and load this lens only where it changes the evidence plan. Do not make the practitioner choose an internal product.

## Recurring work

- MVP features
- auth/payments
- database changes
- deployments
- support fixes
- analytics
- billing
- backup/recovery
- security basics
- vendor integrations

## Failure patterns worth catching

- happy path works but tenant isolation fails
- duplicate money state on retries
- no restore path
- invisible production errors
- AI cleanup deletes needed state
- client/repo secrets
- custom infra replacing managed guarantees
- no rollback

These are candidate risks, not a universal checklist. Discard what is irrelevant to the actual change.

## Intake pivots

Ask only when the environment cannot establish the answer and the answer can materially change the work:

- Can this lose money, customer data, access control, or tomorrow's operability?
- Is a managed service already providing the hard guarantee?
- What is the smallest proof before real customers?
- What can be deferred and what flips the decision?
- How will I notice/recover at 3am?

## Evidence patterns

Prefer a small set of discriminating, independent evidence over a large generated test surface:

- tenant-isolation checks
- payment idempotency/webhook replay
- backup/restore evidence
- production smoke
- alert delivery
- secret/config checks
- rollback/roll-forward rehearsal
- core-journey exploration

## Tooling posture

Reuse managed auth/database/payment/deployment/monitoring and normal project tests. Prefer reuse over building a miniature platform team.

Rule: **detect → reuse → propose → install last**. Professional knowledge is not dependency proliferation.

## Vault knowledge worth recalling

- customer-impact incidents
- vendor quirks
- recovery steps
- billing edges
- launch deferrals
- security/ops lessons
- product promises

Store observable symptom, constraint, context, and evidence/reference. Do not store secrets or treat memory as proof.

## Receipt expectations

A useful receipt answers: what professional invariant/risk was claimed; which evidence family tested it; where the oracle came from and how independent it was; which environment/data/revision was measured; what remains `NO-DATA`; and which decision still belongs to a human.

## Stay out of the way when

The work is small, reversible, and later evidence reconstruction would cost less than assurance ceremony now. Professional depth should make Brother more selective, not more bureaucratic.
