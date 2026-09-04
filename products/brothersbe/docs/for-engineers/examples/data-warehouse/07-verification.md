# 07. Verification plan

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| Mart revenue reconciles to billing within one cent | Reconciliation query comparing mart total to the billing export total for the snapshot | Every build, blocking publication |
| No partial day is ever loaded | Row count assertion against the export header before staging commits | Every ingestion run |
| A rerun of one day produces identical rows | Idempotency test rebuilding one snapshot twice and diffing the output | Every pull request |
| Refunds reduce recognised revenue | Unit test on a refunded invoice asserting the month total drops by the refunded amount | Every pull request |
| The mart states its own freshness | Assertion that the freshness column equals the published snapshot date | Every build |
