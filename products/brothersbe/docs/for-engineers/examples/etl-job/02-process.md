# 02. Process map

## Actors
The partner, the file landing zone, the extract step, the transform step, the
load step, and the ledger reconciliation.

## Steps
| # | Step | Actor | Trigger | Exception path |
|---|---|---|---|---|
| 1 | Partner drops a settlement file | Partner | Their nightly close | File absent by 04:00: an alert fires, no empty batch is created |
| 2 | Extract reads the file and computes its content hash | Extract step | The file lands | Hash matches a loaded batch: the run stops, the batch is already loaded |
| 3 | Transform parses fixed width records into typed rows | Transform step | Extract succeeded | Any unparseable record: the whole batch is rejected, nothing partial loads |
| 4 | Load writes the batch under a new batch id | Load step | Transform succeeded | Write fails midway: the transaction rolls back, the batch id is unused |
| 5 | Reconciliation compares the batch total to the file trailer | Ledger reconciliation | Load committed | Totals differ: the batch is marked quarantined and no payout reads it |

## Handoffs
| From | To | What is handed over | Contract |
|---|---|---|---|
| Partner | Extract step | A fixed width settlement file with a trailer total | The trailer total is authoritative for the file |
| Extract step | Transform step | The raw file plus its content hash | One hash means one batch, forever |
| Transform step | Load step | Typed settlement rows | All rows or none; a partial batch is never handed over |
| Load step | Ledger reconciliation | A committed batch id | A batch is invisible to payout until reconciliation marks it accepted |
