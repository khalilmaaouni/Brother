# 07. Verification plan

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| The passive region can serve traffic within 15 minutes | Quarterly failover rehearsal against production, recording its run id and measured time to serve | Quarterly |
| Replication lag stays under 30 seconds | Continuous lag sampling with an alert at the threshold | Continuous |
| Promotion is refused when lag is over threshold | Rehearsal step that forces lag past the threshold and asserts the promotion is refused | Quarterly |
| The public endpoint address never changes | Assertion in the rehearsal that consumers reconnect without a configuration change | Quarterly |
| The failed region is fenced before traffic moves | Rehearsal step that brings the old primary back mid-failover and asserts it takes no writes | Quarterly |
