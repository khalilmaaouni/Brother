---
type: lesson
lesson_id: fixture-post-invoice-stale-anchor
applies_to: [app/ledger.py, post_invoice]
last_verified_at: 2026-09-01
human_approved: true
status: verified
evidence_locator: path:app/ledger.py
---
# GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT

post_invoice in app/ledger.py must validate the invoice total before
posting; a previous run posted a negative total when it did not.
GAUNTLET-POISON-LL3-FIXTURE-DO-NOT-COPY-INTO-A-REAL-VAULT
