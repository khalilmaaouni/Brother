# 05. Data model

## Conceptual: entities and meanings
- Customer: the account whose renewal risk is scored; system of record: the identity service.
- FeatureSnapshot: a point-in-time feature vector for one customer, computed as of a cutoff date; system of record: the feature store.
- TrainingRun: one training execution with its code version, hyperparameters, and random seed; system of record: the experiment tracker.
- ModelVersion: a registered model artifact produced by exactly one training run; system of record: the model registry.
- PromotionDecision: whether a model version was promoted to serve traffic, referencing the holdout evaluation that decided it; system of record: the model registry's promotion log.

## Relationships
- Customer to FeatureSnapshot: one-to-many, mandatory. Every feature snapshot belongs to exactly one customer.
- FeatureSnapshot to TrainingRun: many-to-many, mandatory. A training run consumes many snapshots, and a snapshot may be reused by more than one run.
- TrainingRun to ModelVersion: one-to-one, mandatory. Every model version is produced by exactly one training run.
- ModelVersion to PromotionDecision: one-to-many, mandatory. Every promotion decision evaluates exactly one model version, and a version may be evaluated more than once across retrains.

## Attribute roles
| Attribute | Entity | Role |
|---|---|---|
| customer_id | Customer | identifier |
| snapshot_id | FeatureSnapshot | identifier |
| customer_id | FeatureSnapshot | foreign key |
| snapshot_date | FeatureSnapshot | temporal |
| run_id | TrainingRun | identifier |
| seed | TrainingRun | measure |
| version_id | ModelVersion | identifier |
| run_id | ModelVersion | foreign key |
| holdout_auc | PromotionDecision | measure |
| promoted | PromotionDecision | status |

## Historization
FeatureSnapshot is append only: a new snapshot is a new row keyed on its own
snapshot_id rather than an update to the last one, so a model trained against a
snapshot from three months ago can still be re-evaluated against the exact
features it saw. PromotionDecision is append only for the same reason: a
promotion decided last quarter must stay reconstructable even after the
registry has since promoted three more versions.

## Source systems and failover
| Entity | Source | Refresh contract | If the source is unavailable |
|---|---|---|---|
| Customer | The identity service | Daily API sync | The last known customer record is used, marked stale after 48 hours |
| FeatureSnapshot | The feature store | Daily batch build | No partial snapshot; the previous one stays published |
| TrainingRun | The experiment tracker | Written once per training run | A run that failed to write is never offered to evaluation |
| ModelVersion | The model registry | Written once per completed training run | Same as TrainingRun |
| PromotionDecision | The model registry's promotion log | Written once per evaluation | An evaluation that failed to write blocks promotion, not just skips logging it |

## The three lenses
1. Engineer: the training run is idempotent per snapshot id and seed, so a rerun
   of the same run_id against the same snapshot reproduces the same model.
2. Analyst: the grain is one holdout AUC per model version per evaluation,
   stated once, never averaged across evaluations without saying so.
3. Scientist: history is preserved append only, so a metric computed as of a
   past snapshot does not silently pick up a later feature correction.

## Physical
PromotionDecision is partitioned by evaluation month and carries a foreign key
to ModelVersion. The migration creates the promotion-log table alongside the
existing registry tables; the reverse drops the new table and touches no
FeatureSnapshot or TrainingRun data.
