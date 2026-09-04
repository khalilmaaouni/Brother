# 07. Verification plan

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| The challenger beats the baseline on the holdout by at least 0.05 AUC | `promotion_threshold_check.py`, comparing both AUCs computed by the rank-sum formula against the fixed margin | Every promotion decision, blocking registration |
| No customer appears in both the training set and the holdout | `split_check.py --key customer_id --time-col snapshot_date --cutoff 2026-02-01`, run over train.csv and holdout.csv | Every evaluation, blocking promotion |
| The training run recorded the seed it trained with | `seed_recorded_check.py`, reading training-run.json for an integer seed field | Every training run, before it is offered to evaluation |
| The published holdout AUC is independently re-derivable | `compute_auc_rank.py` and `compute_auc_trapezoid.py`, two different formulas (rank-sum, trapezoidal ROC integration) checked for zero drift by the numbers gate | Every promotion decision |
| A promotion decision is reconstructable months later | PromotionDecision is written append only, keyed to the snapshot_id and run_id it evaluated | Every promotion, read back at audit time |
