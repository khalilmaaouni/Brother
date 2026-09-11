# Vault reference

The Vault is durable local memory for knowledge worth recalling at the point of work.

Useful categories include decisions, observable failure symptoms, invariants, exceptions, operational lessons, data semantics, test oracles, architecture constraints, tooling notes, safe domain knowledge, and deprecations.

## Precedence

1. current explicit human decision;
2. current evidence/repository state;
3. current authoritative contract/specification;
4. recalled Vault context.

A remembered lesson can warn; it cannot prove.

## Root selection

Current routing guidance recognizes configured Vault roots including `BM_VAULT_ROOT`, `BROTHERMODE_VAULT`, and Brother-recorded configuration. With none configured, report unconfigured/NO-DATA rather than inventing a path.

## Do not store

Credentials, secrets, raw customer data, unnecessary full chat histories, or anything that should live in a secrets/data system.

## Retrieval quality

Write the observable symptom a future practitioner will recognize, not only the internal cause they would already have to know.

## Authority

Current Vault tools/runtime and router closing guidance. Measured claims about whether recall reduces repeated mistakes require a current experiment.
