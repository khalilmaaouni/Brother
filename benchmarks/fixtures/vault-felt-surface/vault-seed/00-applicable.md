---
type: lesson
lesson_id: fixture-close-month-double-post
applies_to: [app/ledger.py, close_month]
last_verified_at: 2026-09-08
human_approved: true
status: verified
evidence_locator: path:app/ledger.py
---
# GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT

close_month must be called with the period already closed in the ledger; a
previous run double-posted when it was not.
GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
