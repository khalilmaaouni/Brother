# Documentation assurance

Brother documentation must not ask readers to trust a claim the repository cannot keep consistent.

Important current-state claims live in `docs/assurance/DOC-CLAIMS.md` with authority, test/evidence, public repeaters, and review state.

## Validation catches

1. Broken relative links.
2. Repository paths missing from a clean checkout unless explicitly runtime-generated.
3. Incompatible live claims.
4. Host-specific command drift.
5. Hand-edited generated files.
6. Schema/prose disagreement.
7. Release-specific facts presented as timeless behavior.
8. Historical plans presented as current runtime truth.

## Authority order for conflicts

Start with current executable behavior/tests, then current installed routing skill/contract, current machine-readable schema, generated system inventory, public prose, then historical plans/steering notes. A higher item can still contain a bug; the order prevents convenience-based reconciliation.

## Publish rule

Fail the docs build/release when a registered safety/behavior claim is contradictory or its canonical path is missing.
