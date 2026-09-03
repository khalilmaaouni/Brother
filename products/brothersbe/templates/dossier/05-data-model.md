# 05. Data model

<!-- SBE-TEMPLATE-UNFILLED 05-data-model: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

## Conceptual: entities and meanings
- Customer: the person or account placing an order; system of record: the CRM.
- Order: a confirmed request to purchase, one per checkout; system of record: the order service.
- OrderLine: one product line within an order, with its quantity and price at time of sale; system of record: the order service.

Every entity names its system of record. An entity with no system of record is a defect.

## Relationships
- Customer to Order: one-to-many, mandatory. Every customer can place many orders; every order belongs to exactly one customer.
- Order to OrderLine: one-to-many, mandatory. Every order has at least one order line; every order line belongs to exactly one order.

Every relationship carries cardinality (one-to-one, one-to-many, many-to-one, many-to-many)
and optionality. An unspecified cardinality is a defect.

## Attribute roles
| Attribute | Entity | Role (identifier, descriptor, measure, foreign key, temporal, status) |
|---|---|---|
| customer_id | Customer | identifier |
| order_id | Order | identifier |
| customer_id | Order | foreign key |
| order_id | OrderLine | foreign key |
| quantity | OrderLine | measure |
| placed_at | Order | temporal |
| status | Order | status |

## Historization
How change over time is preserved, per entity, and why.
Example: Order keeps every status transition (placed, paid, shipped) as its
own row, timestamped, so an audit can reconstruct what the order looked
like at any point instead of only its current state.

## Source systems and failover
| Entity | Source | Refresh contract | If the source is unavailable |
|---|---|---|---|
| Customer | The CRM | Daily batch | The last known snapshot is used, marked stale after 24 hours |
| Order | The order service | Near real time stream | Writes queue and retry, no data loss; reads show a staleness notice |
| OrderLine | The order service | Near real time stream | Same as Order |

## The three lenses
1. Engineer: can this load reliably, idempotently, at volume, and recover after failure?
2. Analyst: can the real questions be answered without heroic joins, is every grain unambiguous?
3. Scientist: is history preserved, is leakage prevented, are features derivable?

## Physical (after the logical model is approved)
Types, indexes, partitioning, clustering, constraints, and the migration path with its reverse.
Example: Order partitioned by placed_at month, a foreign key from OrderLine
to Order, and a migration that can be rolled back by dropping the new
partition without touching the ones already in place.
