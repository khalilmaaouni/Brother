# 04. Technology map

<!-- SBE-TEMPLATE-UNFILLED 04-technology-map: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

| Component | Technology | Owner | Failure mode | Recovery path |
|---|---|---|---|---|
| Checkout | Checkout service | Checkout team | Cannot reach the order service | Retries, then queues locally |
| Order service | Order service | Order team | Database unavailable | Fails closed, checkout shows an error |
| Warehouse system | Warehouse system | Warehouse team | Cannot consume new orders | Backlog grows on the queue, no orders are lost |

## Source systems
| System | What it masters | Interface | Availability expectation | Failover |
|---|---|---|---|---|
| The CRM | Customer records | Batch export | Business hours | Last known snapshot is used until the next export |
| The order service | Orders and order lines | Event stream | Near continuous | The queue absorbs downstream outages |

## Recovery posture
Recovery time objective, recovery point objective, and the drill that proves them.
Example: recovery time objective of one hour, recovery point objective of
five minutes of order events, proven by a quarterly failover drill that
replays the queue into a standby order service.
