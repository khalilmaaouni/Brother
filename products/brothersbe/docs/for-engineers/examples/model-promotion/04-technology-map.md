# 04. Technology map

| Component | Technology | Owner | Failure mode | Recovery path |
|---|---|---|---|---|
| FeatureStore | Daily batch feature build over the warehouse | Data platform | Snapshot build fails | Training and serving keep reading the previous snapshot; no partial snapshot is published |
| TrainingJob | Batch training run against one pinned snapshot | Data platform | Run crashes or records no seed | The run is discarded and not offered to the evaluation job |
| EvaluationJob | Holdout scoring for the challenger and the baseline | Data platform | Holdout shares a customer with the training set | Promotion is refused, the run is quarantined pending a re-split |
| ModelRegistry | Versioned model store with a promotion log | Data platform | Promotion rule not met | The baseline stays marked live; the challenger version is stored but not served |
| ServingSystem | Online scoring service the retention team's queue reads | Retention engineering | Registry unreachable at load time | The last successfully loaded model version keeps serving |

## Source systems
| System | What it masters | Interface | Availability expectation | Failover |
|---|---|---|---|---|
| The feature store | Customer feature snapshots as of a cutoff date | Daily batch build | Business hours plus the nightly window | Previous snapshot stays published; no partial snapshot |
| The billing system | Subscription state feeding renewal-risk features | Daily export | Business hours plus the nightly window | Previous snapshot's subscription state is used, marked stale |

## Recovery posture
Recovery time objective of four hours for the serving system to fail back to the
last known-good model version, recovery point objective of one feature snapshot,
proven by a quarterly drill that reloads the previous three registered versions
into a scratch serving instance and reconciles their scores against their own
promotion receipts.
