# 02. Process map

## Actors
The billing system, the ingestion job, the warehouse staging layer, the mart
build, and the analyst reading the mart.

## Steps
| # | Step | Actor | Trigger | Exception path |
|---|---|---|---|---|
| 1 | Billing exports the day's invoices and refunds | Billing system | 02:00 daily | Export absent: the ingestion job stops and alerts, no partial day is loaded |
| 2 | Ingestion loads the export into staging | Ingestion job | The export lands | Row count differs from the export header: the load is rolled back |
| 3 | Mart build rebuilds the revenue tables | Mart build | Staging load succeeded | Reconciliation fails: the previous mart stays live and the build is quarantined |
| 4 | Reconciliation compares mart revenue to billing | Mart build | The rebuild finished | Difference over one cent: the build is not published |
| 5 | Analyst queries the published mart | Analyst | Any time | Mart is stale: the freshness column shows the last published date |

## Handoffs
| From | To | What is handed over | Contract |
|---|---|---|---|
| Billing system | Ingestion job | A daily invoice and refund export with a row count header | The header count is authoritative; a mismatch is a failed load |
| Ingestion job | Mart build | A staging snapshot with a snapshot id | A snapshot id is immutable once written |
| Mart build | Analyst | The published revenue mart | Published means reconciled to billing within one cent |
