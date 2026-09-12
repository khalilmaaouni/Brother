# Enterprise Controls

This page explains the 13 controls checked by `scripts/enterprise_doctor.py`.

## What the verdicts mean

* PRESENT: the required file or pattern was found and meets the rule.
* ABSENT: the required file or pattern was not found or does not meet the rule.
* NO-DATA: the check could not be decided from files in the repository, either because the data is missing, unparseable, or no contract exists.

NO-DATA is never a pass.

## Controls

1. security-policy: PRESENT if `SECURITY.md` exists at the repository root and is non empty.
2. code-owners: PRESENT if `.github/CODEOWNERS` exists and has at least one non comment line.
3. evidence-obligation: PRESENT if `scripts/gate_obligations.json` exists, parses as JSON and has a `default` key; ABSENT if missing; NO-DATA if present but unparseable.
4. obligation-wired: PRESENT if `scripts/required_fast.sh` contains the text `evidence_obligation.py transition`; ABSENT otherwise; NO-DATA if the file is missing.
5. signed-receipts: PRESENT if `scripts/receipt_attest.py` exists and contains a `verify` subcommand string; ABSENT otherwise.
6. signing-identities: PRESENT if at least one `allowed_signers` file exists under `products/*/scripts/` and has a non comment line; ABSENT otherwise.
7. pinned-ci: PRESENT if every `.github/workflows/*.yml` `uses:` line references a 40 hex character commit SHA after the `@`; ABSENT if any does not; NO-DATA if there is no workflows folder.
8. ci-permissions: PRESENT if every workflow file contains a `permissions:` key; ABSENT if any does not; NO-DATA if there is no workflows folder.
9. os-sandbox: always NO-DATA; no sandbox contract in this repository; Brother relies on the host's sandbox (see docs/reference/safety-boundaries.md).
10. egress-policy: always NO-DATA; no sandbox contract in this repository; Brother relies on the host's sandbox (see docs/reference/safety-boundaries.md).
11. delegated-identity: always NO-DATA; no sandbox contract in this repository; Brother relies on the host's sandbox (see docs/reference/safety-boundaries.md).
12. central-audit-store: always NO-DATA; no sandbox contract in this repository; Brother relies on the host's sandbox (see docs/reference/safety-boundaries.md).
13. fail-closed-mode: always NO-DATA in this doctor. The fence has an opt-in enforced mode, but this static check does not measure its effective mode or live host coverage (see docs/reference/safety-boundaries.md).

## Scope of the doctor

The doctor reports posture only. It is read only, never writes files, never runs mutating git commands, and never uses the network. It does not claim enterprise readiness or compliance. It prints a count, not a score, and NO-DATA is not a pass.

## Required evidence

The obligation map decides whether evidence is sufficient for a merge or release transition. A required check reporting NO-DATA can prevent that transition while its verdict remains NO-DATA. The obligation step does not relabel missing evidence as FAIL or count it as PASS.
