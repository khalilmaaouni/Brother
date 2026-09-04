# Adopt Brother with a security reviewer

The adoption risk is hidden authority: automatic edits, machine-wide hooks, untraceable receipts, or a tool that appears to approve its own result.

## Start with the boundaries

Before installing, agree on three points:

1. Which repositories may use Brother.
2. Which outcomes require a live intent screen before work begins.
3. Who can accept a delivery after reading the receipt.

The public install is in the [root README](../../README.md#install). Run one small task in a disposable repository before using a sensitive one.

## Inspect the controls

Use these commands from the repository root:

```bash
python3 scripts/test_worktree_lane.py
python3 scripts/test_integrate.py
python3 scripts/test_receipt_door.py
python3 scripts/test_fable_authority.py
```

Together they check that work stays isolated, integration is serial, hollow proof becomes NO-DATA, and acceptance remains a human action.

For the installed bytes, run each product verifier from its own directory:

```bash
bash scripts/verify-install.sh
```

The verifier compares the files you received against the `CHECKSUMS.sha256` that shipped with them, and it changes nothing. Do not run `scripts/checksums.sh CHECKSUMS.sha256` first: that command writes the manifest, so verifying afterwards compares the tree against a manifest generated from that same tree a moment earlier, which proves nothing about what was shipped. Regenerating the manifest is the maintainer's step when a product's own files change, never a reviewer's step before verifying.

## Review the operating cost

The receipt prints token fields when the worker returns them and prints NO-DATA with a reason when it does not. Check that behavior with:

```bash
python3 scripts/test_brother_run.py
```

## Limits to approve explicitly

- Installed hooks run in every supported session on that machine. There is no per-repository opt-out yet.
- Workers auto-accept edits only inside their isolated checkouts. Review the integration boundary before allowing sensitive work.
- The current public tag is unsigned.
- The public toy has no recorded human acceptance.
- Recalled Vault lessons are untrusted context. Their effect on repeat mistakes is not yet measured.
- Brother does not provide a security guarantee and does not replace specialist review.

Adopt only after the reviewer can reproduce the controls above and is comfortable with these limits.
