# 05. Data model

## Conceptual: entities and meanings
- IdempotencyKey: the caller supplied token that identifies one logical create attempt; system of record: the Jobs API database.
- Job: one unit of scheduled work created through the Jobs API; system of record: the Jobs API database.
- Caller: the service that owns a key namespace; system of record: the service registry.

## Relationships
- Caller to IdempotencyKey: one-to-many, mandatory. Every key belongs to exactly one caller; a caller may hold many keys.
- IdempotencyKey to Job: one-to-one, optional. A claimed key may not yet have a job, and a job created without a key has none.

## Attribute roles
| Attribute | Entity | Role |
|---|---|---|
| key | IdempotencyKey | identifier |
| caller_id | IdempotencyKey | foreign key |
| request_fingerprint | IdempotencyKey | descriptor |
| claimed_at | IdempotencyKey | temporal |
| job_id | Job | identifier |
| state | Job | status |

## Historization
IdempotencyKey rows are kept for 72 hours after claim and then deleted, because a
retry older than the client timeout budget is a new intent rather than a retry.
Job rows are never deleted; the state column keeps its own transition log.

## Source systems and failover
| Entity | Source | Refresh contract | If the source is unavailable |
|---|---|---|---|
| IdempotencyKey | The Jobs API database | Written in the create transaction | Job creation fails closed, the caller retries |
| Job | The Jobs API database | Written in the create transaction | Job creation fails closed |
| Caller | The service registry | Daily sync | The last known caller list is used |

## Physical
The key column carries a unique constraint on (caller_id, key), which is the
mechanism that makes two concurrent claims produce one winner. The migration adds
one table and one index; the reverse drops both and touches no job row.
