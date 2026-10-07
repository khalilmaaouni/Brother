# Run Brother

Use Brother when another person may later need evidence or when the work's risk makes ordinary implementation checks insufficient.

## Start in your host

First [install Brother for your host](../reference/install-matrix.md), then open the repository where you want to work.

In Claude Code or Cursor, type:

```text
/brother make add() reject non-numeric input and prove the behavior with a test
```

In Codex, start a fresh session and ask: "Use Brother to reject non-numeric input in add(), preserve valid addition, and show the deciding checks and receipt."

Replace the example with your own outcome.

## Describe the outcome

State behavior/result, not an internal product.

Good:

```text
Make retries idempotent for duplicate payment callbacks and prove a repeated callback cannot create a second ledger entry.
```

Weak:

```text
Use BrotherSBE with maximum verification.
```

## Leave trivial work trivial

A reversible one-line edit nobody would later ask to prove should not pay full receipt ceremony.

## Inspect intake and plan when present

For contracted work, confirm the question and success checks match the ask. For advanced/in-session plans, every unit needs a discriminating check, complete `writes`, valid deps, and the plan file outside the target repository.

## Read the receipt

The run is not done because a process exited zero. Find the receipt and inspect per-unit/per-file evidence and `NO-DATA` gaps.

## Verify the result

You can explain what changed, what evidence supports it, what evidence is not independent, what remains unproven, and which human decision comes next.
