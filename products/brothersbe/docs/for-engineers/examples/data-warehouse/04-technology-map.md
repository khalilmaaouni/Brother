# 04. Technology map

| Component | Technology | Owner | Failure mode | Recovery path |
|---|---|---|---|---|
| BillingExport | Nightly export job in the billing system | Finance engineering | Export does not land by 02:30 | Ingestion stops and alerts; no partial day is loaded |
| IngestionJob | Airflow DAG writing to warehouse staging | Data platform | Row count mismatch against the export header | Load is rolled back, the snapshot id is not published |
| StagingLayer | Warehouse schema, one snapshot per day | Data platform | Disk pressure from retained snapshots | Snapshots older than 30 days are dropped by a retention task |
| MartBuild | Warehouse transformation job | Data platform | Reconciliation exceeds one cent | The build is quarantined, the previous mart stays published |
| RevenueMart | Published warehouse tables | Data platform | Stale because a build was blocked | The freshness column shows the last published date |

## Source systems
| System | What it masters | Interface | Availability expectation | Failover |
|---|---|---|---|---|
| The billing system | Invoices, refunds, subscription state | Daily file export | Business hours plus the nightly window | The previous snapshot stays published; no partial load |
| The identity service | Customer identity and account ownership | Daily API sync | Business hours | The last known customer dimension is used and marked stale |

## Recovery posture
Recovery time objective of four hours for the published mart, recovery point
objective of one day of billing data, proven by a quarterly rebuild drill that
replays the last 30 snapshots into a scratch schema and reconciles each one.
