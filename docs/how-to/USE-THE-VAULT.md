# Use the Vault without turning memory into truth

Save too much and the useful lesson disappears in noise. Trust memory too much and an old mistake becomes a standing rule.

## 1. Choose a local folder

On first setup, choose a folder you control for private memory. Brother should explain what it will write before writing it. If no Vault is configured, the honest answer is NO-DATA, not a guessed location.

The benefit is ownership: the notes are normal Markdown files you can inspect without Brother.

## 2. Keep only durable knowledge

Write a note when it should change a future task. Good candidates are:

- a failure and its cause;
- a decision and its reason;
- a security or compatibility constraint;
- an assumption that must be checked again;
- an approved lesson from a human correction.

Do not save credentials, raw sensitive data, full conversations, temporary debugging output, or every test log.

## 3. Make the note narrow

A useful note can be this simple:

```markdown
# Public interface compatibility

Decision: Existing response fields remain stable.
Why: Current clients read those fields directly.
Applies to: Public interface edits and migrations.
Recheck when: A versioned interface is available.
Evidence: Link to the receipt or human decision.
```

State the scope, reason, and recheck condition. Do not write a universal rule when the evidence supports only one area.

## 4. Approve a lesson separately

Capture is not approval. The person who reviews a correction decides whether its lesson should become durable. The author cannot approve the same candidate as a standing rule.

Prove that boundary from the repository root:

```bash
python3 products/brothermode/tools/test_bm_vault_lifecycle.py
python3 products/brothermode/tools/test_bm_vault_promotions.py
```

## 5. Let recall happen at the edit

When later work reaches a known file, Brother can print the relevant lesson before the edit. Prove the recall path:

```bash
python3 products/brothermode/tools/test_vault_recall_hook.py
```

Read the note as context. Check its age, source, scope, and current evidence before following it.

## 6. Refuse the third failed attempt

If the same failed technique is tried again, Brother records it. A third attempt is refused and the run is directed to gather new information.

Prove that behavior:

```bash
python3 scripts/test_attempt_ledger.py
python3 scripts/test_attempt_hook.py
python3 scripts/test_find_out.py
```

## Limits

The Vault does not prove that a remembered lesson is correct. It is not a backup, a secrets store, or an authority over current human intent. Its effect on the real rate of repeated mistakes has not yet been measured, so that benefit remains NO-DATA. Installed hooks also run across every supported session on the machine, with no per-repository opt-out yet.

Current evidence wins.
