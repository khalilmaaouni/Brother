# 04. Technology map

| Component | Technology | Owner | Failure mode | Recovery path |
|---|---|---|---|---|
| GlobalAcceleratorEdge | Anycast edge terminating TLS in front of DNS | Platform | Edge health checks disagree with regional health | Edge follows the traffic director, it never decides the active region |
| TrafficDirector | Managed DNS with health probes | Platform | Probes flap and traffic oscillates | Promotion is manual, so a flap moves nothing without a human |
| PrimaryRegion | API tier plus database primary | Platform | Regional outage | Passive region is promoted through the rehearsed runbook |
| PassiveRegion | Warm API tier plus streaming replica | Platform | Replica falls behind the lag threshold | Promotion is refused, and the lag alert fires before an incident needs it |
| ReplicationLink | Streaming replication between regions | Platform | Link saturates and lag grows | Write throttling on the primary, lag alert at 30 seconds |
| FailoverRunbook | Executable runbook with a rehearsal record | Platform | Runbook drifts from the estate | Quarterly rehearsal, and a rehearsal that fails blocks the quarter's change freeze exit |

## Source systems
| System | What it masters | Interface | Availability expectation | Failover |
|---|---|---|---|---|
| The configuration store | Region topology and the active region marker | API read at boot and on change | Continuous | Cached last known topology, and a stale marker is refused rather than trusted |
| The secrets manager | Region-scoped credentials | API read at boot | Continuous | Both regions hold their own copy, replicated out of band |

## Recovery posture
Recovery time objective of 15 minutes to serve traffic from the passive region.
Recovery point objective of 30 seconds of writes, which is the replication lag
alert threshold and not a guess. Proven by a quarterly failover rehearsal against
production that records its own run id and measured time to serve.
