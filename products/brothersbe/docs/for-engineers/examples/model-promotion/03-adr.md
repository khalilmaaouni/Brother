# 03. Architecture decision record

## Context
The retention team needs to know a promoted renewal-risk model is genuinely
better than the one it replaces, not better because of a leaked evaluation. The
feature store is the system of record for what the model saw; the model registry
is the system of record for what is live. Something has to sit between training
and serving that a challenger cannot skip.

## Criteria
Deploying teams = 2 (data platform and the retention engineering team). Failure
isolation = high, a bad promotion must never leave the retention team scoring
customers with an untested model. Ops maturity = medium, the evaluation job
shares an on-call rotation with the feature store. Reproducibility = the primary
requirement: a promotion decision from three months ago must be re-derivable
from its pinned snapshot.

## Options considered

### Rejected: Promote based on offline AUC alone without holdout reconciliation
Removes the gate entirely and trusts whatever number the training job reports.
The training job's own number is not independent of anything: a leaked feature
or an overlapping holdout inflates it the same way a copy-pasted second
derivation inflates a warehouse figure, and nothing here would catch it.

### Rejected: Continuous shadow deployment with no fixed promotion gate
Runs the challenger alongside the baseline indefinitely and watches the live
metrics drift apart. That never produces an accountable promotion decision, and
a shadow score that quietly degrades has no threshold at which anyone is told to
look, so a false promotion and a real one are equally invisible.

## Decision
Promotion is gated behind a holdout evaluation with three checks, each producing
its own receipt: the challenger's holdout AUC must beat the baseline's by at
least 0.05, no customer may appear in both the training set and the holdout, and
the training run must record its random seed. All three read from one pinned
feature snapshot.

## Consequences
Every retrain costs one evaluation cycle before it can be promoted, which is
slower than trusting the training job's own report. In exchange, every
registered model version carries evidence a reviewer can re-run six months
later without access to the live feature store.

## What would flip this
If retrains need to happen many times a day, the per-run gate becomes the
bottleneck; revisit toward a lighter per-run check with a periodic full holdout
audit instead of gating every single run.
