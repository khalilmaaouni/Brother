# Brother documentation

Brother helps you delegate work without treating full auto as a leap of faith: define boundaries, inspect evidence, retain useful lessons, and keep consequential decisions yours. This documentation is organized by the reader's question, not Brother's internal products.

## Your first useful delegation

1. **Install:** [Claude Code](how-to/install-claude-code.md), [Codex](how-to/install-codex.md), or [Cursor](how-to/install-cursor.md), then verify the host setup.
2. **Try it:** [First verified change](tutorials/first-verified-change.md), on a task whose expected answer you understand.
3. **Know the limits:** [Safe delegation](how-to/delegate-safely.md) and [enforcement boundaries](reference/safety-boundaries.md).
4. **Return to the work:** [Read the receipt](how-to/review-a-receipt.md), [resume or recover](how-to/recover-from-failure.md).
5. **Make the next run better informed:** [Use the Vault](how-to/use-the-vault.md), without mistaking memory for proof.

Evaluating rather than installing? Read [Where Brother fits](explanation/choosing-a-workflow.md). Evaluate bounded delegation with reviewable evidence and useful memory, not a promise to eliminate human judgment.

## Learn by doing

- [First verified change](tutorials/first-verified-change.md): bounded code change, discriminating check, receipt.
- [First risky change](tutorials/first-risky-change.md): stronger evidence and explicit review for risk-bearing work.
- [First analysis verification](tutorials/first-analysis-verification.md): a decision-grade number with semantics and independent reconciliation.
- [First founder release review](tutorials/first-founder-release.md): production readiness without pretending every unknown is solved.

## Solve a task

**Install/operate:** [Claude Code](how-to/install-claude-code.md) · [Codex](how-to/install-codex.md) · [Cursor](how-to/install-cursor.md) · [Run](how-to/run-brother.md) · [Resume](how-to/resume-work.md) · [Recover](how-to/recover-from-failure.md) · [Scope hooks](how-to/scope-hooks.md)

**Define/verify:** [Outcome contract](how-to/write-an-outcome-contract.md) · [Schedulable plan](how-to/write-a-schedulable-plan.md) · [Review receipt](how-to/review-a-receipt.md) · [Migration](how-to/verify-a-migration.md) · [Decision-grade number](how-to/verify-a-number.md) · [Vault](how-to/use-the-vault.md) · [Team adoption](how-to/adopt-on-a-team.md) · [Release decision](how-to/prepare-a-release.md)

**Hand off or join an existing project:** [Hand off work to a teammate](how-to/hand-off-to-a-teammate.md) · [Add Brother to an existing repository](how-to/add-brother-to-an-existing-repo.md)

## Look up the contract

- [Routing](reference/routing.md)
- [Commands and entry points](reference/commands-and-entrypoints.md)
- [Outcome contract](reference/outcome-contract.md)
- [Verdicts](reference/verdicts.md)
- [Evidence and independence](reference/evidence.md)
- [Receipt model](reference/receipt-model.md)
- [Work units](reference/work-units.md)
- [Safety boundaries](reference/safety-boundaries.md)
- [Hooks](reference/hooks.md)
- [Vault](reference/vault.md)
- [State and files](reference/state-and-files.md)
- [Configuration](reference/configuration.md)
- [Install matrix](reference/install-matrix.md)
- [Troubleshooting](reference/troubleshooting.md)
- [Documentation assurance](reference/documentation-assurance.md)

## Understand the design

- [Why Brother](explanation/why-brother.md)
- [Evidence before confidence](explanation/evidence-before-confidence.md)
- [Why NO-DATA exists](explanation/no-data.md)
- [Human authority](explanation/human-authority.md)
- [Workers parallel, truth serial](explanation/worktrees-and-serial-truth.md)
- [Memory is not proof](explanation/memory-is-not-proof.md)
- [Intake before plan](explanation/intake-before-plan.md)
- [Professional practice packs](explanation/professional-practice-packs.md)

## Professional lenses

[All lenses](personas/README.md): senior backend, senior data engineering, infrastructure/SRE, architecture, data analysis, data science, BA, technical BA, QA automation, manual QA/QC, and solo founder.

These are composable evidence/practice lenses, not user-selected product modes and not promises about the current outcome-contract persona enum.

## Japanese

Start at [Brother 日本語ドキュメント](ja/README.md). The first Japanese set covers the concepts and operating paths most likely to cause unsafe misunderstanding; it does not pretend to be a complete mirror yet.

## Internal/historical material

`docs/releases/**`, `docs/deliveries/**`, `docs/plan/**`, machine-readable `docs/schema/*.json`, runtime-created `docs/decisions/**`, and generated `SYSTEM.md` are evidence/contracts/history rather than the public navigation model. Documentation rewrites must not silently rewrite them.

## Documentation rule

Every live behavioral claim gets one current meaning. When code, a shipped skill, schema, and prose disagree, identify authority and fix the public claim. Do not average the stories. See [Documentation assurance](reference/documentation-assurance.md).
