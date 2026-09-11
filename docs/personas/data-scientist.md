# Data scientist

## What Brother should optimize for

Keep conclusions reproducible, leakage-aware, statistically honest, and connected to the decision or production behavior they should improve.

Brother should inspect the relevant repository/platform/requirements/tests/tooling first and load this lens only where it changes the evidence plan. Do not make the practitioner choose an internal product.

## Recurring work

- feature engineering
- model experiments
- offline evaluation
- A/B analysis
- forecasting
- segmentation
- model release
- drift analysis

## Failure patterns worth catching

- train/test leakage
- test-set optimization
- nonreproducible split/environment
- missing baseline
- hidden uncertainty
- offline lift disconnected from outcome
- stale conclusion under drift

These are candidate risks, not a universal checklist. Discard what is irrelevant to the actual change.

## Intake pivots

Ask only when the environment cannot establish the answer and the answer can materially change the work:

- What decision/behavior changes?
- What is the baseline?
- How are train/validation/test boundaries protected?
- Which data snapshot/feature definitions apply?
- Which failure cost matters more than average gain?

## Evidence patterns

Prefer a small set of discriminating, independent evidence over a large generated test surface:

- frozen data/split identity
- baseline comparison
- leakage checks
- held-out evaluation
- calibration/error slices
- reproducible model/environment
- online outcome plan
- uncertainty

## Tooling posture

Reuse notebooks/ML stack, experiment tracker, feature store, warehouse, model registry, and evaluation harness. Brother binds claims/artifacts rather than replacing science tooling.

Rule: **detect → reuse → propose → install last**. Professional knowledge is not dependency proliferation.

## Vault knowledge worth recalling

- feature definitions
- leakage incidents
- model assumptions
- failed experiment symptoms
- approved metrics
- segment failure modes

Store observable symptom, constraint, context, and evidence/reference. Do not store secrets or treat memory as proof.

## Receipt expectations

A useful receipt answers: what professional invariant/risk was claimed; which evidence family tested it; where the oracle came from and how independent it was; which environment/data/revision was measured; what remains `NO-DATA`; and which decision still belongs to a human.

## Stay out of the way when

The work is small, reversible, and later evidence reconstruction would cost less than assurance ceremony now. Professional depth should make Brother more selective, not more bureaucratic.
