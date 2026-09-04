# A duplicate invoice example

Customers receive two invoices for one purchase after a cancelled order is reopened. You need an answer you can inspect, not a technical assurance that the issue is probably fixed.

## State what must be true

Write the outcome in plain words:

1. Reopening a cancelled order keeps the original order number.
2. One purchase has exactly one invoice.
3. Support sees the original invoice after reopening the order.

The person running Brother can use:

```text
/brother stop duplicate invoices when a cancelled order is reopened, while keeping the original order and invoice
```

## Read the receipt, not the confidence

Each sentence should come back with PASS, FAIL, or NO-DATA and the command that produced the verdict.

Suppose the first sentence is PASS but the second is NO-DATA. That means the order number was checked, but the run did not establish that duplicate invoices are impossible. The work may be promising, but your main pain is still unproven.

The developer can verify that Brother keeps missing proof visible:

```bash
python3 scripts/test_receipt_door.py
```

## Ask the next useful question

Point to the NO-DATA line and ask: "What would have to run for this sentence to become PASS or FAIL?"

That keeps the conversation on your requirement. You do not need to diagnose the code or invent the check yourself.

When the evidence arrives, read the updated receipt and answer the acceptance screen. Brother does not answer it for you. The developer can prove that boundary with:

```bash
python3 scripts/test_fable_authority.py
```

## Keep the durable lesson

If the root cause produces a rule future work should remember, approve a narrow Vault note. For example: reopening an order must reuse its invoice identity. The developer can prove point-of-need recall with:

```bash
python3 products/brothermode/tools/test_vault_recall_hook.py
```

## Limits

This example does not prove the real invoice behavior. It shows how to read a receipt. The strength of the result still depends on the check a developer writes. Vault memory can be stale, and its measured effect on repeated mistakes remains NO-DATA.
