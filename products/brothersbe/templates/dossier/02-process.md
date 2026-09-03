# 02. Process map

<!-- SBE-TEMPLATE-UNFILLED 02-process: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

## Actors
Who and what participates.
Example: the customer, the checkout service, the order service, and the
warehouse system.

## Steps
| # | Step | Actor | Trigger | Exception path |
|---|---|---|---|---|
| 1 | Customer places an order | Customer | Checkout is submitted | Payment declined: no order is created |
| 2 | Order is recorded | Order service | Checkout confirms payment | Write fails: checkout retries, customer sees an error |
| 3 | Order reaches the warehouse | Warehouse system | Order service publishes the order | Warehouse is unreachable: the order queues and retries |

Every step names an actor, a trigger, and what happens when it fails.

## Handoffs
| From | To | What is handed over | Contract |
|---|---|---|---|
| Checkout | Order service | A confirmed order with its lines | Order service acknowledges receipt within one second |
| Order service | Warehouse system | A new order event | Warehouse consumes within five minutes or an alert fires |
