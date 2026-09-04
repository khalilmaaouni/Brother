# 01. Purpose brief

## Problem
Partner settlement files arrive nightly as fixed width text and are loaded by a
shell script that overwrites the staging table. When a partner re-sends a
corrected file, the second load silently replaces the first, and the ledger has
no record that a correction happened. Two disputes last quarter could not be
reconstructed.

## Users
Ledger engineers, who reconcile partner totals. The finance team, who answer
partner disputes and currently have no way to see what a file said before it was
corrected.

## Success criteria
Every settlement file load is addressable by its own batch id and never
overwrites an earlier one. A corrected file is a new batch, and the ledger can
show both. Loading a file twice produces one batch, not two.

## Non-goals
This does not change the partner file format, does not change how settlements are
paid, and does not backfill the batches that were already overwritten.

## What breaks if this is wrong
Partner settlement totals feed the payout run. A batch counted twice pays a
partner twice; a batch dropped underpays. Both are money leaving the company
incorrectly, and neither is visible until the partner complains.
