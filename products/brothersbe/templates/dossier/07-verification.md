# 07. Verification plan

<!-- SBE-TEMPLATE-UNFILLED 07-verification: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| The warehouse sees a new order within minutes | Time from order confirmation to warehouse receipt, measured in production | Continuous, alerted on breach |
| No order is lost between checkout and the warehouse | Reconciliation of order counts in the order service against orders received by the warehouse | Daily |
| The queue survives a warehouse outage | Failover drill: stop the warehouse consumer, confirm the queue backs up without dropping messages | Quarterly |

Every claim names its check. A claim with no check is a hope.
