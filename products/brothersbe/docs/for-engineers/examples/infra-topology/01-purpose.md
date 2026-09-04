# 01. Purpose brief

## Problem
The public API tier runs in one region. A regional outage takes the whole product
offline, and the last one lasted 40 minutes with no action available to us except
waiting. There is no rehearsed recovery path, only a runbook nobody has executed
under load.

## Users
Every API consumer, internal and external. On call engineers, who currently have
no lever during a regional event. The support team, who cannot give a recovery
estimate because there is no recovery procedure to estimate.

## Success criteria
A regional failure is survivable by promoting the passive region, with a rehearsed
procedure whose measured time to serve traffic is under 15 minutes. Failover is
executed at least quarterly against production, not against a diagram.

## Non-goals
This does not make the two regions active-active, does not change the data model,
and does not move the warehouse or any batch workload.

## What breaks if this is wrong
A half-built failover is worse than none: a promotion that succeeds at the edge
while the database stays in the failed region serves reads from a stale replica
and accepts writes that will be lost. Split brain is the specific risk.
