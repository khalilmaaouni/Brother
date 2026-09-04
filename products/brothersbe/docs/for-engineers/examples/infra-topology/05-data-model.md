# 05. Data model

## Conceptual: entities and meanings
- Region: one deployment location holding an API tier and a database node; system of record: the configuration store.
- FailoverEvent: one declared promotion, planned or incident driven; system of record: the incident record.
- ReplicationLagSample: one measurement of how far the passive database trails the primary; system of record: the metrics store.

## Relationships
- Region to FailoverEvent: one-to-many, optional. A region may have been promoted many times, or never.
- Region to ReplicationLagSample: one-to-many, mandatory. Every lag sample belongs to exactly one passive region.

## Attribute roles
| Attribute | Entity | Role |
|---|---|---|
| region_id | Region | identifier |
| role | Region | status |
| failover_id | FailoverEvent | identifier |
| region_id | FailoverEvent | foreign key |
| declared_at | FailoverEvent | temporal |
| time_to_serve_seconds | FailoverEvent | measure |
| lag_seconds | ReplicationLagSample | measure |
| sampled_at | ReplicationLagSample | temporal |

## Historization
FailoverEvent is append only and never updated, because a promotion that
happened cannot un-happen and the rehearsal record is the evidence the recovery
posture rests on. ReplicationLagSample is retained at full resolution for 30 days
and downsampled after that.

## Source systems and failover
| Entity | Source | Refresh contract | If the source is unavailable |
|---|---|---|---|
| Region | The configuration store | Read at boot and on change | The last known topology is cached; a stale active marker is refused |
| FailoverEvent | The incident record | Written when a promotion is declared | The promotion proceeds and the record is written after, never gating recovery |
| ReplicationLagSample | The metrics store | Continuous sampling | Missing samples are treated as lag over threshold, so promotion is refused |

## The three lenses
1. Engineer: promotion is idempotent, and a second promotion of an already primary region is a no-op rather than an error.
2. Analyst: time to serve per failover event is directly answerable, which is the number the recovery time objective is measured against.
3. Scientist: lag samples are retained long enough to characterise the tail, not only the median.

## Physical
FailoverEvent carries a foreign key to Region. ReplicationLagSample is partitioned
by sampled_at day. The change adds the passive region's tables in that region's
own database; the reverse decommissions the passive region and leaves the primary
untouched.
