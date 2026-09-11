# Verify a migration

Use this for database, warehouse, schema, storage, or infrastructure migrations where “the command succeeded” is insufficient.

## Define the migration contract

State source/target state, compatibility window, affected data/traffic, forward path, rollback or roll-forward strategy, downstream consumers, and acceptable loss/downtime.

## Inspect blast radius

Search schema references, lineage, API consumers, jobs, BI models, infrastructure dependencies, and runbooks before selecting evidence.

## Useful independent evidence

- row/count/key reconciliation;
- null/uniqueness/domain invariants;
- before/after query comparison;
- backward/forward compatibility tests;
- shadow reads/dual writes where appropriate;
- dry-run/plan output;
- rollback/recovery rehearsal;
- consumer tests;
- latency/cost comparison;
- observability signal verification.

## Make identity explicit

Record environment, revision, data snapshot/partition/time when material.

## Verify the result

Do not conclude “migration safe.” State exactly which invariants were established, against which environment/data, what recovery evidence exists, and what remains for human release authority.
