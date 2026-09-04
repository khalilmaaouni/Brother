# 08. Behaviour table

## What this is

01-purpose.md states what promoting the challenger must guarantee, and
07-verification.md already lists the check behind each guarantee. The rows
below transcribe those same statements into this repository's fixed row
contract. No row states anything beyond what the dossier already says.

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A challenger model that finished holdout evaluation | The registry decides whether to promote it | The challenger's holdout AUC beats the baseline's by at least 0.05, or promotion is refused | `promotion-threshold`, every promotion decision, blocking registration |
| B2 | A training run and its holdout | The evaluation job scores the holdout | No customer appears in both the training set and the holdout | `split-check-holdout`, every evaluation, blocking promotion |
| B3 | A training run that produced a model version | The run finishes and is offered to evaluation | The run records the random seed it trained with | `seed-recorded`, every training run, before it reaches evaluation |
| B4 | The published holdout AUC for a promotion decision | The decision is recorded | The figure is independently re-derivable by a second formula, matching to zero drift | Rank-sum and trapezoidal-ROC recomputation checked for zero drift, every promotion decision (see numbers-manifest.json) |
| B5 | A promotion decision made months ago | Someone audits it later | The decision, its snapshot_id and its run_id are still readable exactly as recorded | PromotionDecision is written append only, never updated in place, read back at audit time |

## What this does not do

This does not run the checks: it states the rule and hands the proof
obligation to 07-verification.md, where each check already lives.
