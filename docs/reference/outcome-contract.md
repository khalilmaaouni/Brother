# Outcome contract reference

The outcome contract captures what was asked before planning turns it into implementation work.

Machine-readable schema: `docs/schema/outcome-contract-v1.json`.

## Required top-level keys in v1

- `schema_version`
- `project`
- `language`
- `question`
- `success_checks`
- `must_answer`
- `affected_products`
- `ticket`
- `audit`
- `persona`
- `state`
- `receipts`
- `questions`
- `history`
- `decision`

Use the JSON schema for exact nested requirements/enums.

## States

`draft` → `contracted` → `planned` → `in-flight` → `delivered`, plus `superseded`.

A draft can hold unresolved questions. Under current validation, a contracted-or-later outcome needs meaningful success checking.

## Success checks

Success checks bind the requested proof to later execution. A plan should not quietly replace the promised proof with a different green command.

## Receipts

Evidence references must use the resolver families accepted by the schema/checker. Unresolvable evidence is unverified, not silently valid.

## Persona field versus professional lenses

The current v1 schema's `persona` enum is:

- `analyst`
- `lead`
- `developer`
- `NO-DATA`

It is **not** the eleven profession pages under `docs/personas/`. Those are composable practice/evidence lenses. Do not write profession names into v1 records unless the schema is deliberately versioned.

## Decision records

Intake tooling may create records under `docs/decisions/` in a working estate. A clean public checkout is not guaranteed to contain a particular record, so public docs must not rely on a specific one existing.

## Validation

Use the repository's current contract checker, not generic JSON Schema validation alone; cross-field rules are enforced in code.

## Authority

Schema: `docs/schema/outcome-contract-v1.json`.

Intake guidance: `bundle/skills/using-brother/references/intake.md`.
