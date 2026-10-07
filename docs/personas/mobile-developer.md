# Mobile developer

Read [Mobile development and assurance with Brother](../explanation/mobile-development-and-assurance.md) for the mobile-specific capabilities and their evidence boundaries.

## What Brother should optimize for

Protect native behavior, journey intent, build identity, device evidence, and release-state facts while changing a mobile product quickly.

Brother should inspect the mobile project, its journey contract, its reference build, and its available simulator or device evidence first. Load this lens only where it changes the evidence plan. Do not make the practitioner choose an internal product.

## Recurring work

- native journey changes
- simulator builds and tests
- reference build binding
- screenshots and screen observations
- device presence and installation checks
- release-state tracking
- handoff packs for native evidence

## Failure patterns worth catching

- testing the wrong installed build
- a passing test with no exact test identity
- a reused result bundle presented as fresh evidence
- simulator evidence treated as physical-device behavior
- a visual target promoted without stable selector evidence
- installed state treated as proof of release

These are candidate risks, not a universal checklist. Discard what is irrelevant to the actual change.

## Intake pivots

Ask only when the environment cannot establish the answer and the answer can materially change the work:

- Which journey and exact test identity must remain valid?
- Which build, version, and source revision are being compared?
- Is simulator evidence enough, or is physical-device evidence required?
- Which screen, media, accessibility, or interaction observation matters?
- Which release states are expected to be observed, and which need separate access?

## Evidence patterns

Prefer a small set of discriminating, independent evidence over a large generated test surface:

- `scripts/mobile_workflow.py` doctor, reference, simulator run, and pack checks
- `scripts/native_evidence.py` records and validates fresh native result bundles, exact test leaves, logs, and artifact hashes
- `scripts/mobile_design.py` searches a curated reference board and records design or media evidence without making a UX verdict
- `scripts/mobile_journey_contract.py` and `scripts/mobile_canonical_action.py` validate journey and action records
- `scripts/mobile_native_ios_adapter.py` drives the supported simulator actions; unsupported actions remain explicit
- `scripts/device_matrix.py` records physical-device presence and can run its guarded local lifecycle, while external farm and provider adapters return `NO-DATA`
- `scripts/journey_passport.py` composes existing evidence into a view, not a second evidence store
- `scripts/release_state_tracker.py` keeps upload, processing, availability, installation, and acceptance or release separate; only installation is measured here

## Tooling posture

Use the existing mobile contracts, workflow, native evidence, simulator, device, and release-state tools before adding dependencies. The action router selects a real described driver, but does not execute it. The visual fallback adapter has no live model call and returns `NOT_ATTEMPTED`, so it does not guess a target.

Rule: **detect → reuse → propose → install last**. Do not treat a planner, contract, adapter description, or visual fallback as a real device run.

## Vault knowledge worth recalling

- journey invariants
- reference build identity
- exact test identities
- simulator and device limitations
- missing release-state access
- prior evidence failures

Store observable symptom, constraint, context, and evidence/reference. Do not store secrets or treat memory as proof.

## Receipt expectations

A useful receipt answers: which journey and native invariant were claimed; which evidence family tested it; which build, source revision, device class, and test identity were measured; what remains `NO-DATA`; and which quality, accessibility, or release decision still belongs to a human.

## Stay out of the way when

The work is small, reversible, and later evidence reconstruction would cost less than assurance ceremony now. Mobile depth should make Brother more selective, not more bureaucratic.
