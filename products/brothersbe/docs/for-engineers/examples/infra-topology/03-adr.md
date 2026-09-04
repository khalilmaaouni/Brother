# 03. Architecture decision record

## Context
One region hosts the entire public API tier and its database. A regional event is
a total outage with no lever. We need a survivable topology that the team can
actually operate with the on call rotation it has today.

## Criteria
Deploying teams = 3 sharing one on call rotation. Consistency = strong within the
primary; the passive region may lag, and the lag is measured. Ops maturity =
medium, one rotation, no dedicated infrastructure on call. Failure isolation =
high, a regional event must not require a code deploy to recover from.

## Options considered

### Rejected: Active-active across both regions
Removes failover time entirely and introduces cross-region write conflicts on
every table. With one shared on call rotation and no conflict resolution strategy
in the application, the failure mode moves from a rare outage to a permanent
correctness problem.

### Rejected: Backup and restore into a cold region
Cheapest, and the recovery time is measured in hours because the region has to be
built before it can serve. It also leaves the recovery path untested between
incidents, which is the condition we already have.

## Decision
Active-passive across two regions. The passive region runs a warm standby of the
API tier and a streaming database replica. Promotion is a human decision executed
through a rehearsed runbook, gated on measured replication lag.

## Consequences
The passive region costs roughly 60 percent of the primary while serving no
traffic. Failover is not instant: the target is under 15 minutes. Promotion being
manual means a 3am outage needs a human, which is a deliberate trade against
automatic promotion during a partial fault.

## What would flip this
If replication lag stops fitting inside the recovery point objective, or if the
application gains conflict resolution for cross-region writes, revisit toward
active-active. If the business accepts a multi-hour outage, revisit downward
toward cold standby and stop paying for the warm tier.
