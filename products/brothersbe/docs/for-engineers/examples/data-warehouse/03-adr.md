# 03. Architecture decision record

## Context
Revenue reporting needs one authoritative daily mart. The billing system is the
system of record for money, and it will not serve analytical queries. Something
has to move billing data into the warehouse on a schedule with a reconciliation
that can block publication.

## Criteria
Deploying teams = 2 (data platform and finance engineering). Consistency = daily
is acceptable, but the published mart must never disagree with billing by more
than one cent. Ops maturity = medium, one on call rotation shared with ingestion.
Failure isolation = high, a failed build must leave yesterday's mart readable.

## Options considered

### Rejected: Query the billing replica directly from the dashboard
Removes the pipeline entirely, and removes the reconciliation with it. It also
puts analytical scans on a replica sized for transactional reads, and a runaway
dashboard query would degrade billing failover capacity.

### Rejected: Streaming change data capture from billing into the mart
Gives sub-minute freshness, which nobody asked for, at the cost of an ordering
and late-arrival problem on refunds. A refund that arrives out of order would
publish a wrong month total with no batch boundary at which to catch it.

## Decision
A daily batch: billing exports, the ingestion job loads staging under a snapshot
id, the mart rebuilds from that snapshot, and publication is blocked unless the
reconciliation matches billing within one cent.

## Consequences
Freshness is one day, and the mart states its own freshness. The snapshot id
makes every published figure re-derivable from a pinned read. A blocked build
means yesterday's mart stays live, which is stale but never wrong.

## What would flip this
If finance comes to need intra-day revenue, or if the export grows past the load
window, revisit toward incremental loads with a per-batch reconciliation instead
of a full daily rebuild.
