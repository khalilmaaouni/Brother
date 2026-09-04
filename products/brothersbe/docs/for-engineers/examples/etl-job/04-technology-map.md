# 04. Technology map

| Component | Technology | Owner | Failure mode | Recovery path |
|---|---|---|---|---|
| LandingZone | Object storage bucket with per partner prefixes | Data platform | A partner writes to the wrong prefix | The extract refuses an unexpected prefix and alerts; nothing loads |
| ExtractStep | Airflow task reading the file and hashing it | Data platform | Partial read of a file still being written | Reads only files with a completion marker, and retries otherwise |
| TransformStep | Fixed width parser with a per partner layout | Ledger | Layout drift after a partner change | Whole batch rejected, alert names the first unparseable record |
| LoadStep | Warehouse transaction writing one batch | Data platform | Transaction fails midway | Rollback, the batch id is unused, the run retries |
| ReconciliationStep | Query comparing batch total to the file trailer | Ledger | Totals differ | Batch marked quarantined, payout does not read it |

## Source systems
| System | What it masters | Interface | Availability expectation | Failover |
|---|---|---|---|---|
| The partner settlement feed | Settlement records and their trailer totals | Nightly fixed width file | Nightly window | No file means no batch; the previous accepted batch stays current |
| The partner registry | Partner identity and file layout version | API read at run start | Business hours | Cached layout is used, and an unknown layout version rejects the batch |

## Recovery posture
Recovery time objective of one nightly cycle: a failed load is re-runnable from
the same file with no manual cleanup, because the batch id is unused on rollback.
Recovery point objective is zero, since the partner file itself is retained in the
landing zone for 90 days. Proven by a monthly replay drill that re-runs the last
seven nights into a scratch schema and compares batch totals.
