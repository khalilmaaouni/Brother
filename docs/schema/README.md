# Schema documentation

`outcome-contract-v1.json` is the current public machine-readable outcome contract. Read [Outcome contract reference](../reference/outcome-contract.md) for human-oriented guidance.

Generic JSON Schema validation is not the whole Brother contract because current runtime code enforces additional cross-field rules. The checked-in schema remains field/enum authority; the runtime checker is authority for additional enforced constraints. Public docs must not invent fields or enum values that neither accepts.

## State and the Daybook columns

The outcome contract `state` has six lifecycle values: draft, contracted, planned, in-flight, delivered, superseded. Files that are not outcome-contract-v1 records are listed as "not a contract".

| Daybook column | derived from |
|---|---|
| open | Any state except delivered or superseded, with `decision.close_call` false |
| close-call | Any state except delivered or superseded, with `decision.close_call` true |
| decided | `state` delivered |
| superseded | `state` superseded, whatever `decision.close_call` says |

These columns are rendered by `scripts/daybook.py`, never stored on the record. Fixtures are in `scripts/fixtures/outcome-contract/`, and the suite is `scripts/test_contract_check.py`.
