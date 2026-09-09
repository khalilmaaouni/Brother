# Intake, one record

Read this when the ask is an intake: a new outcome, a decision to put to a
person, or a plan to accept. Loaded on demand, never at session start
(tests/test_context_budget.py caps the door at 6,000 bytes).

An intake writes exactly ONE record: the outcome contract at
`docs/schema/outcome-contract-v1.json` (2026-09-08 debate judgment,
merging on branch u4-contract-schema). Check a record with:

    python3 scripts/contract_check.py <record>.json

Exit 0 PASS, 1 FAIL naming every violation, 2 NO-DATA if the record or
schema cannot be read as JSON. One write path, one read path, one schema.

## While the record is still being drafted

`scripts/intake_inflight.py` writes that same record, mid-draft, under
`docs/decisions/inflight/<slug>.json`:

    python3 scripts/intake_inflight.py open <slug> --from <draft>.json
    python3 scripts/intake_inflight.py rewrite <slug> --note "..."
    python3 scripts/intake_inflight.py close <slug>

`close` promotes it to `docs/decisions/<slug>.json`, one file at a time;
every write appends to the record's `history`, never overwriting it.

## Every source carries a receipt

    python3 scripts/receipt_check.py docs/decisions/<slug>.json

A source with no receipt, or one this checker cannot resolve, renders
UNVERIFIED wherever the record is shown.

## Corrections

A correction made once is a fact from then on: `scripts/annotations_store.py
add <record>.json` (also `list`, `remove <id>`) holds it at
`docs/decisions/annotations.json`, keyed on the option and criterion it
corrects, never on which record it was first typed into.
