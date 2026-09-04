# 05. Data model

## Conceptual: entities and meanings
- Customer: the account that holds subscriptions; system of record: the identity service.
- Subscription: a recurring agreement to pay for a plan; system of record: the billing system.
- Invoice: one billing document issued against a subscription; system of record: the billing system.
- Refund: money returned against an invoice, in whole or in part; system of record: the billing system.
- RevenueEvent: the grain of the mart, one recognised amount for one invoice line on one date; system of record: the revenue mart.

## Relationships
- Customer to Subscription: one-to-many, mandatory. Every subscription belongs to exactly one customer.
- Subscription to Invoice: one-to-many, mandatory. Every invoice belongs to exactly one subscription.
- Invoice to Refund: one-to-many, optional. An invoice may have no refund, or several partial ones.
- Invoice to RevenueEvent: one-to-many, mandatory. Every revenue event traces to exactly one invoice.

## Attribute roles
| Attribute | Entity | Role |
|---|---|---|
| customer_id | Customer | identifier |
| subscription_id | Subscription | identifier |
| customer_id | Subscription | foreign key |
| invoice_id | Invoice | identifier |
| amount_cents | Invoice | measure |
| refund_id | Refund | identifier |
| invoice_id | Refund | foreign key |
| refunded_cents | Refund | measure |
| recognised_on | RevenueEvent | temporal |
| status | Invoice | status |

## Historization
Invoice and Refund are append only in the mart: a correction arrives as a new row
with its own effective date rather than an update, so a month total computed last
week can be reproduced exactly. Customer is a slowly changing dimension keyed on
customer_id with valid_from and valid_to, because a customer that changes plan
tier must not retroactively change last quarter's segmentation.

## Source systems and failover
| Entity | Source | Refresh contract | If the source is unavailable |
|---|---|---|---|
| Customer | The identity service | Daily API sync | The last known dimension is used, marked stale after 48 hours |
| Subscription | The billing system | Daily export | No partial load; the previous snapshot stays published |
| Invoice | The billing system | Daily export | Same as Subscription |
| Refund | The billing system | Daily export | Same as Subscription |
| RevenueEvent | The mart build | Rebuilt per snapshot | The previous published mart stays live |

## The three lenses
1. Engineer: the build is idempotent per snapshot id, so a rerun of one day produces the same rows.
2. Analyst: the grain is one recognised amount per invoice line per date, stated once and not overloaded.
3. Scientist: history is preserved append only, so a feature computed as of a past date does not leak later corrections.

## Physical
RevenueEvent is partitioned by recognised_on month and clustered by
subscription_id. Invoice carries a foreign key to Subscription. The migration
creates the mart schema alongside the existing tables; the reverse drops the new
schema and touches no staging snapshot.
