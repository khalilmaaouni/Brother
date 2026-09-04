# 03. Architecture decision record

## Context
The Jobs API creates a duplicate job whenever a caller retries a timed out POST.
We need a way to make the create path idempotent without asking every caller to
build its own dedupe table.

## Criteria
Deploying teams = 4 callers. Consistency = the claim must be strongly consistent,
because two concurrent claims deciding they both won is the whole defect.
Ops maturity = medium. Failure isolation = the store must not become a second
availability dependency for job creation.

## Options considered

### Rejected: Redis SETNX on the key
Fast, but the store would be a second availability dependency with weaker
durability than the jobs table itself. A Redis failover that loses the last
second of writes loses claims, and a lost claim is a duplicate job, which is the
defect this design exists to remove.

### Rejected: Each caller keeps its own dedupe table
This is what three teams already do, and it is the reason the behaviour differs
per caller. It also cannot dedupe a retry that arrives from a different instance
of the same caller, because the table is local to the process that wrote it.

## Decision
Claim the idempotency key in the same Postgres transaction that inserts the job,
using a unique constraint on (caller_id, key).

## Consequences
Job creation gains one index write. Key expiry becomes a scheduled delete job.
Callers that reuse a key with a different body get a 422 instead of a surprise.
The jobs database now carries write load it did not carry before, and the key
table grows with request volume rather than with job volume.

## What would flip this
If key claim volume grows past what one Postgres primary absorbs, or if job
creation moves off Postgres, revisit toward a dedicated claim store with
durability guarantees written into its own contract.
