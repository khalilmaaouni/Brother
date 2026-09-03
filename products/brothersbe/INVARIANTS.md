# INVARIANTS: the numbered promises

The short register of what BrotherSBE's checkers promise, one number each, with
the test that asserts it. The laws in [SKILL.md](SKILL.md) say what the tools
check; this file says what the CHECKERS themselves may never do, so a refutation
has a number to aim at instead of a diff. Each promise names its asserting test,
because a promise nothing asserts is a wish with a serial number.

- **I1. NO-DATA is never PASS.** Evidence that was never examined is an absence,
  not a clean bill. Asserted by `evals/test_no_data_class.py`, which hollows
  every check's own declared worked example and requires that none of it
  produces a PASS, and by the constructor in `tools/sbe_checks.py`, which
  refuses to register a check that declares PASS as its empty state.
- **I2. An advisory run exits 0 whatever it finds.** The local hooks and bare
  invocations never block a session, including when they crash. Asserted by
  `tools/test_sbe.py` (TestStrictMode) and by the eval
  `a-change-with-no-numbers-and-no-migration-blocks-nothing`.
- **I3. A strict run blocks on FAIL only, never on NO-DATA, and which FAILs
  block is the severity each check declared at write time.** Gate severity
  blocks under `--strict`; soft severity blocks only under the opt-in
  `--strict-soft`. Asserted by `tools/test_sbe.py` (TestStrictMode, both
  directions) and the eval above for the NO-DATA half.
- **I4. Every verdict names what it examined and what it skipped.** Files
  opened, tokens a parser skipped, directories pruned, refused, or left
  uninspected when a budget ran out. Asserted by the evidence-class evals
  (among them `the-diagram-evidence-names-what-it-skipped`,
  `a-symlinked-source-directory-is-disclosed-not-silent`).
- **I5. A check cannot be registered without a hollowable worked example, a
  declared empty state that is not PASS, and a declared severity.** Asserted by
  the constructor refusals in `tools/sbe_checks.py`, the eval cases
  `a-check-without-a-declared-severity-is-refused` and
  `a-severity-outside-gate-or-soft-is-refused`, and by
  `evals/test_no_data_class.py`, which fails loudly on a registry it cannot
  discover or invoke.
- **I6. A crashing check reports FAIL carrying the exception, never a missing
  line.** Asserted by the eval `a-crashing-check-fails-instead-of-disappearing`.
- **I7. A waiver is reported, never silently applied.** A `.sbe-exempt` prints
  as a WAIVED line naming the directory and every check it covers, and the
  shipped CI surfaces it as an annotation and in the job summary. Asserted by
  the waiver evals, including
  `the-ci-waiver-annotation-does-not-fire-on-a-run-with-no-waiver`.

## Measured power of the tests: the defect-reinjection record

The strongest evidence for the promises above is that each shipped gate and
design check was fed the defect its eval exists to catch, and caught it. The
reinjection is executed by the eval bed itself on every run, not once at
release: every fixture plants the defect and the suite fails if the check stops
catching it. Measured 2026-07-31 by `python3 evals/run_evals.py`, ending
"547 evals: 547 passed, 0 regressions." One representative reinjection per
check, from that run:

| Check | Defect reinjected | Eval case | Result |
|---|---|---|---|
| numbers (L7) | an overstated total | `overstated-total-caught` | caught |
| migration (L8) | an untested reverse leg | `untested-reverse-caught` | caught |
| approval (L9) | a typed name instead of a verified identity | `typed-name-approval-caught` | caught |
| ran (L10) | a check recorded green over a nonzero exit | `green-on-red-caught` | caught |
| lints (L11) | a swallowed exception with no waiver | `lints-catch-a-swallowed-error` | caught |
| artifacts (L2) | a required artifact missing at the tier | `missing-required-artifact-caught` | caught |
| adr (L3) | a decision record with no rejected alternatives | `adr-without-rejected-alternatives-caught` | caught |
| datamodel (L4) | a relationship with no cardinality | `unspecified-cardinality-caught` | caught |
| diagrams (L5) | a node tracing to nothing the dossier declares | `orphan-diagram-node-caught` | caught |
| placeholder | the shipped template, copied and unedited | `unedited-copied-template-caught` | caught |
| citation-inventory | an external URL cited with no scoped inventory entry | `citation-url-without-inventory-entry-caught` | caught |
| scope naming (gates) | a change directory carrying no receipt, pooled into a sibling's PASS | `a-change-directory-with-no-receipt-is-named-in-the-verdict-that-pools-it` | caught |
| scope naming (approval) | a refusal quoting one of several APPROVAL files and naming none | `the-approval-verdict-names-which-approval-file-it-read` | caught |
| scope naming (design) | an empty directory printing another root's five PASS lines | `an-empty-directory-cannot-print-the-report-of-a-dossier-somewhere-else` | caught |

This calibration re-runs whenever a check or the eval bed changes, because the
shipped CI workflow runs the suite on every pull request; a check that stops
catching its defect stops the merge, not just the claim.

## What this register does NOT claim

- The hollowing sweep and the reinjection record are evidence about the cases
  that ran, not proof over all inputs. The meta-test prints its own coverage,
  skipped cases and exemptions on every run so that boundary is checkable.
- Nothing here detects that a change NEEDED an approval, and nothing resolves a
  Reviewed-in id; those paths report NO-DATA on purpose (L9's stated scope).
- The promises hold for the shipped checkers, not for the model running beside
  them: a vendor model or harness update changes behavior with no pull request.
- Every threshold behind these tests was measured on one estate. Re-measure on
  yours before trusting a number.
