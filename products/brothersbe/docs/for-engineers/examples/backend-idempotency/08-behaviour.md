# 08. Behaviour table

## What this is

This dossier already states, in 01-purpose.md and 07-verification.md, what
the idempotency layer over the Jobs API must do under a retried, concurrent,
or reused request. The rows below transcribe those statements into the fixed
row contract this repository's design checker reads. Nothing here is a new
rule: every Required outcome is a sentence already in this dossier, and every
Proof names the check 07-verification.md already lists.

## Rules

| ID | Starting point | Trigger | Required outcome | Proof |
|---|---|---|---|---|
| B1 | A POST to the Jobs API carrying an idempotency key already used with a given request body | The client retries after a timeout, submitting that same key and body again | The API returns the same job id and creates no second job | Integration test posting one key twice and asserting one job row, every pull request |
| B2 | A POST to the Jobs API with a given idempotency key | 50 concurrent POSTs are fired carrying that same key (a retry storm) | Exactly one job is created | Concurrency test firing 50 parallel POSTs on one key, every pull request |
| B3 | An idempotency key already used with one request body | The same key is submitted again with a different request body | The request is rejected rather than silently ignored, and no second job row is created | Integration test asserting a 422 and no new job row, every pull request |
| B4 | An idempotency key stored after a prior request | 72 hours pass with no reuse of that key | The key is deleted so expired keys do not accumulate | Reconciliation query counting keys older than 72 hours, daily |

## What this does not do

This does not run the checks: it states the rule and points at the test or
query 07-verification.md already assigns to prove it.
