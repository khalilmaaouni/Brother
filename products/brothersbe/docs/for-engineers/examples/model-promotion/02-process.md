# 02. Process map

## Actors
The feature store, the training job, the evaluation job, the model registry, and
the serving system that scores customers with whatever model is registered as
live.

## Steps
| # | Step | Actor | Trigger | Exception path |
|---|---|---|---|---|
| 1 | Feature store computes the daily feature snapshot | Feature store | 01:00 daily | Snapshot build fails: the previous snapshot stays the one training and serving both read |
| 2 | Training job trains the challenger on a pinned snapshot and records its seed | Training job | A retrain is requested | No seed recorded: the run is not eligible for promotion |
| 3 | Evaluation job scores the challenger and the baseline on the same holdout | Evaluation job | Training finishes | Holdout shares a customer with the training set: promotion is refused |
| 4 | Registry checks the promotion rule before registering the challenger as live | Model registry | Evaluation finishes | Challenger does not beat the baseline by the margin: the baseline stays live |
| 5 | Serving system loads whichever model version the registry marks live | Serving system | A promotion is registered | Registry unreachable: serving keeps the last model it successfully loaded |

## Handoffs
| From | To | What is handed over | Contract |
|---|---|---|---|
| Feature store | Training job | A snapshot id and the customer features as of that snapshot | The snapshot id is immutable once written |
| Training job | Evaluation job | A model version with its training run id and recorded seed | A run with no recorded seed is not evaluated for promotion |
| Evaluation job | Model registry | A holdout AUC for the challenger and the baseline, both pinned to the same snapshot | The two scores must come from the same holdout read |
| Model registry | Serving system | The model version marked live | Live means it cleared the promotion rule; nothing else is ever loaded |
