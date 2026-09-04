# 01. Purpose brief

## Problem
Renewal risk is currently scored by a baseline heuristic that has not changed in
two years. The retention team already suspects it misses customers who churn
without ever crossing its rule thresholds, and a challenger model trained on the
same feature store scores meaningfully better offline. Nothing today gates
whether that offline number is trustworthy enough to promote.

## Users
The retention team, who queue outreach against whichever model is live. The head
of data science, who signs off on promotion and has to defend the number to
finance if a promoted model underperforms in production.

## Success criteria
The challenger is promoted only when its holdout AUC beats the baseline's by at
least 0.05, no customer appears in both the training set and the holdout, and
the training run that produced it recorded the random seed it used. All three
are checked from a pinned snapshot, not a live re-query.

## Non-goals
This does not change what the retention team does with a risk score, does not
touch pricing or the renewal offer itself, and does not replace the baseline for
any segment the challenger was not evaluated on.

## What breaks if this is wrong
A promoted model that only looks better because the holdout leaked training
customers costs the retention team's outreach budget on customers who were never
actually at risk, and lets the customers the baseline would have caught slip
through unscored. A promotion nobody can re-derive is a promotion nobody can
defend when the live metric moves.
