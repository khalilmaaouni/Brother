# 08. Behaviour table

## What this is

01-purpose.md states what the two-region failover must guarantee, and
07-verification.md already lists the rehearsal step or sampling behind each
guarantee. The rows below transcribe those same statements into this
repository's fixed row contract. No row states anything the dossier does not
already say.

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | The API tier running in the active region, passive region idle | A regional failover rehearsal is executed | The passive region serves traffic within 15 minutes | Quarterly failover rehearsal against production, recording its run id and measured time to serve |
| B2 | Ongoing replication from the active region to the passive region | Continuous operation, steady state | Replication lag stays under 30 seconds | Continuous lag sampling with an alert at the threshold |
| B3 | Replication lag that has crossed the 30 second threshold | A promotion of the passive region is attempted while lag is over threshold | The promotion is refused | Rehearsal step that forces lag past the threshold and asserts the promotion is refused, quarterly |
| B4 | Consumers connected to the public API endpoint address | A failover promotes the passive region | The public endpoint address never changes, and consumers reconnect without a configuration change | Assertion in the rehearsal that consumers reconnect without a configuration change, quarterly |
| B5 | The old primary region, now failed | Traffic is moved to the newly promoted region mid failover | The failed region is fenced before traffic moves and takes no writes | Rehearsal step that brings the old primary back mid-failover and asserts it takes no writes, quarterly |

## What this does not do

This does not run the rehearsals: it states the rule and hands the proof
obligation to 07-verification.md, where each check already lives.
