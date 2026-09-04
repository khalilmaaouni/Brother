# 02. Process map

## Actors
The on call engineer, the traffic director, the primary region, the passive
region, and the database replication link.

## Steps
| # | Step | Actor | Trigger | Exception path |
|---|---|---|---|---|
| 1 | Regional health degrades past the alert threshold | Traffic director | Health probes fail for 3 minutes | Probe flapping: the promotion is not automatic, a human decides |
| 2 | On call declares a failover | On call engineer | The alert plus a judgment call | Ambiguous signal: the runbook says wait, do not promote on a partial fault |
| 3 | Database replica is promoted to primary | On call engineer | The failover declaration | Replication lag over threshold: promotion is refused, data loss is stated in the incident |
| 4 | Traffic director moves the public endpoint | Traffic director | Database promotion confirmed | Old region recovers mid-failover: it is fenced before traffic moves |
| 5 | Failback is planned, never automatic | On call engineer | The failed region is healthy again | Never failback during the incident, only after a rehearsed window |

## Handoffs
| From | To | What is handed over | Contract |
|---|---|---|---|
| On call engineer | Traffic director | A failover declaration | Traffic moves only after database promotion is confirmed |
| Primary region | Passive region | The replication stream | Lag is measured continuously and is the gate on promotion |
| Traffic director | API consumers | The public endpoint | The endpoint address never changes; only what it resolves to changes |
