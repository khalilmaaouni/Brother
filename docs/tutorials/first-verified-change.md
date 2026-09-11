# Tutorial: your first verified change

Learn the smallest Brother workflow worth using: a change whose check is sensitive to the behavior and whose evidence can be reviewed from a receipt.

## Prerequisites

Use a clean throwaway Python repository with pytest. Start with:

```python
def add(a, b):
    return a + b
```

and one passing test for ordinary numeric addition.

## 1. State the outcome

```text
Make add() reject non-numeric input with a clear TypeError and cover the behavior with a test.
```

## 2. Inspect the proposed proof before editing

The new-behavior check must fail against the starting repository. If it already passes, it cannot prove the change caused the desired behavior.

## 3. Keep work bounded

Implementation/test changes should stay in declared files. An unrelated config rewrite is a scope event, not “helpful cleanup.”

## 4. Read the receipt

For each changed file/unit, read the exact check and whether it actually discriminated the change. A strong receipt can honestly show one unit PASS and another NO-DATA when the second unit's check did not establish the dependency it claims.

## 5. Make the human decision

Decide whether the named evidence is sufficient for this small change. Add stronger/independent evidence only if the risk warrants it.

## What is proven

Only the behavior discriminated by the recorded checks. This tutorial does not prove every possible input type, runtime version, or downstream caller is safe.
