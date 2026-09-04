# 05. Data model

## Conceptual: entities and meanings
- Partner: the counterparty that sends settlement files; system of record: the partner registry.
- SettlementFile: one physical file as it arrived, identified by its content hash; system of record: the landing zone.
- SettlementBatch: one load of one file into staging, with its own batch id and state; system of record: the ledger staging schema.
- SettlementRecord: one settlement line inside a batch; system of record: the ledger staging schema.

## Relationships
- Partner to SettlementFile: one-to-many, mandatory. Every file belongs to exactly one partner.
- SettlementFile to SettlementBatch: one-to-one, optional. A file that was skipped as a duplicate has no batch.
- SettlementBatch to SettlementRecord: one-to-many, mandatory. Every record belongs to exactly one batch.

## States
- received: the batch committed and has not been reconciled yet.
- accepted: reconciliation matched the file trailer total; payout may read it.
- quarantined: reconciliation did not match; payout must not read it.
- superseded: a later accepted batch for the same partner and period replaces it.

## Attribute roles
| Attribute | Entity | Role |
|---|---|---|
| partner_id | Partner | identifier |
| content_hash | SettlementFile | identifier |
| partner_id | SettlementFile | foreign key |
| received_at | SettlementFile | temporal |
| batch_id | SettlementBatch | identifier |
| content_hash | SettlementBatch | foreign key |
| state | SettlementBatch | status |
| total_cents | SettlementBatch | measure |
| record_id | SettlementRecord | identifier |
| batch_id | SettlementRecord | foreign key |
| amount_cents | SettlementRecord | measure |

## Historization
Append only. A correction is a new SettlementBatch, and the batch it replaces
moves to superseded rather than being deleted or updated in place. That is what
makes a dispute reconstructable: both batches remain readable with their own
totals and their own arrival times.

## Source systems and failover
| Entity | Source | Refresh contract | If the source is unavailable |
|---|---|---|---|
| Partner | The partner registry | Read at run start | Cached layout is used; an unknown layout version rejects the batch |
| SettlementFile | The landing zone | Nightly | No file means no batch, and an alert rather than an empty load |
| SettlementBatch | The load step | Per successful load | The transaction rolls back and the batch id is unused |
| SettlementRecord | The load step | Per successful load | Same as SettlementBatch, all rows or none |

## The three lenses
1. Engineer: the load is idempotent on content hash, so re-running the same file creates no second batch.
2. Analyst: the grain is one settlement line per batch, and batch state says plainly which batch is authoritative.
3. Scientist: superseded batches are retained, so a model trained on last quarter can be reproduced against what the ledger believed then.

## Physical
SettlementBatch carries a unique constraint on content_hash, which is the
mechanism that makes a duplicate re-send a no-op. SettlementRecord is partitioned
by batch_id. The migration adds the batch table and a batch_id column on the
existing record table; the reverse drops the batch table and the column and
leaves the record rows in place.
