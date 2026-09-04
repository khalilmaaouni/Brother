# Brother from request to human acceptance

Long-running work fails in predictable places: intent gets blurred, parallel edits collide, a passing command proves the wrong thing, cost disappears, and acceptance becomes an assumption.

## 1. State the outcome

Use one door:

```text
/brother add rate limiting to the public API without changing current client behavior
```

Brother writes an intent screen that names the units and the check for each. A person corrects the outcome when the choice changes what should be built.

## 2. Keep each unit separate

Each unit works in its own checkout. It cannot land directly in yours. Integration happens one unit at a time so the next check runs against the result that would actually move forward.

Prove this boundary:

```bash
python3 scripts/test_worktree_lane.py
python3 scripts/test_integrate.py
```

## 3. Refuse hollow proof

A check must depend on the work. Brother records NO-DATA when a check already passed before the work, the unit changed no file, or the test still passes after the related code is reverted.

Prove the rule:

```bash
python3 scripts/test_receipt_door.py
```

PASS means the named evidence supports the claim. FAIL means it contradicts the claim. NO-DATA means it established neither answer.

## 4. Read the receipt

The delivery report names changed files, the check and exit code for every unit, who wrote each check, the engine revision, and the cost fields returned by the workers.

Prove the report shape:

```bash
python3 scripts/test_brother_run.py
```

When a token figure is absent, the field says NO-DATA and gives the reason. The report does not invent a number.

## 5. Make the human decision

Brother writes an acceptance screen. It does not answer accept or hold for you. Prove the authority boundary:

```bash
python3 scripts/test_fable_authority.py
```

A security reviewer can now inspect the changed files, rerun the checks, challenge their authorship, and decide whether the evidence covers the real risk.

## 6. Keep the useful lesson

If the work exposes a failure worth remembering, the Vault can bring that lesson back before a later edit to the same area. Prove recall and the third-attempt refusal:

```bash
python3 products/brothermode/tools/test_vault_recall_hook.py
python3 scripts/test_attempt_ledger.py
```

The note remains untrusted context. Current code, current checks, and a current human decision win.

## Limits

- The worker may also write the check. The receipt names that fact; it does not create independent review.
- Hooks run in every supported session on the machine, with no per-repository opt-out yet.
- The current public tag is unsigned.
- Recall exists, but its measured effect on repeated mistakes remains NO-DATA.
- Brother does not merge, release, deploy, spend, or accept for you.

For the exact audited example, read [A-REAL-RUN.md](A-REAL-RUN.md).
