# Your first day with Brother

You should not need to read code to know whether the result matches what you asked for.

## Write the picture of success

Use three plain sentences. For example:

1. A returning customer sees the same order number.
2. One purchase creates one invoice.
3. Support can reopen the order without creating a replacement.

Give those sentences to the person running Brother. They can start with:

```text
/brother stop duplicate invoices when a cancelled order is reopened, while keeping the original order and invoice
```

## Read the three answers

The receipt keeps each requirement beside one verdict:

| Verdict | Meaning |
| --- | --- |
| PASS | The named evidence supports the sentence. |
| FAIL | The named evidence contradicts the sentence. |
| NO-DATA | The evidence did not establish either answer. |

NO-DATA is useful because it stops an unchecked requirement from looking complete. The developer can prove this rule with:

```bash
python3 scripts/test_receipt_door.py
```

## Make the decision yourself

Ask what changed, which command checked each sentence, who wrote that check, and what remains NO-DATA. Then answer the acceptance screen yourself.

The developer can prove that Brother does not accept on your behalf with:

```bash
python3 scripts/test_fable_authority.py
```

## Keep one lesson for later

If the work reveals a durable rule, such as "reopening an order must preserve its original invoice," add it to the Vault after a person approves the wording. A later edit in that area can recall it.

The developer can prove recall with:

```bash
python3 products/brothermode/tools/test_vault_recall_hook.py
```

## Limits

You are not being asked to judge the code. You are judging whether the receipt answers your requirement. A passing check may still be the wrong check, so ask what it actually exercised. The Vault can be stale. The effect of recall on repeated mistakes is still unmeasured.

Next, follow [A-WORKED-EXAMPLE.md](A-WORKED-EXAMPLE.md).
