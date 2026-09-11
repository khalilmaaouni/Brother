# Receipt model

A Brother receipt is the durable handoff from “the agent says done” to “a reviewer can see what evidence exists.”

## A reviewer should be able to answer

1. What changed?
2. Which unit claimed it?
3. What exact check was used?
4. What result decided the verdict?
5. Did the check discriminate the change or was it already green?
6. Where is the fuller evidence/output?
7. Who/what authored the check?
8. Which runtime/engine revision produced the record when available?
9. What evidence family/oracle was used when known?
10. What remains `NO-DATA`?

## Per-file accountability

A run-level “tests passed” summary is insufficient when multiple files changed. Preserve enough per-file/per-unit linkage for a stranger to map evidence to change.

## Reading order

Risky, unproven, and scope-surprising changes should be read before low-risk mechanical changes. Reading order assists review; it is not acceptance.

## Durability

Run state/receipts belong in a durable host-specific Brother run root outside the target source tree. Temporary-directory evidence is not a durable handoff.

## Missing receipt

No receipt means no Brother delivery proof to review. Process exit zero is not a substitute.

## Authority

Current receipt/runtime modules and their tests, enumerated by generated `SYSTEM.md`.
