# Write an outcome contract

Use this when the work needs an explicit record of what was asked, what must be proven, or what remains unresolved before planning.

## 1. Inspect before asking

Read repository/contracts/tests/project metadata first. Do not ask for facts the environment can establish.

## 2. Preserve the ask

Record the original `question` and `language`. Do not “improve” it into a different outcome.

## 3. Define discriminating success checks

A success check that already passes is not useful proof for a new change.

## 4. Record only material unknowns

Use `must_answer`/`questions` for blockers that can change scope, risk, evidence, or execution.

## 5. Handle audit/ticket fields deliberately

Do not fill required governance fields with placeholders merely to satisfy validation.

## 6. Validate with Brother's checker

Generic JSON Schema validation is insufficient because current code enforces cross-field rules.

## Verify the result

Before planning, the record is no longer a draft, success checks are meaningful, and the eventual plan will actually run the promised checks.

See [Outcome contract reference](../reference/outcome-contract.md).
