# Recover from failure or refusal

Keep the evidence before retrying. A stopped run is not a reason to delete its directory, reset the target repository, or discard its worktrees.

Commands below run from the Brother checkout. Set `TARGET_REPO` to the absolute path of the repository Brother was changing. Use the same runtime and runs root as the original run.

## 1. Establish what survived

```bash
git -C "$TARGET_REPO" status --short
git -C "$TARGET_REPO" worktree list
```

Read the last failure message and saved run directory. If a receipt exists, read it before accepting any result. Without a receipt, do not infer success from a worker's closing text. Do not publish sensitive logs.

## 2. Match the symptom to the remedy

| Symptom | Inspect | Next action | Do not |
| --- | --- | --- | --- |
| Interrupted process | Run, journal, worktrees | Resume the same run below | Start over and lose provenance |
| Multiple unfinished outcomes | Numbered continuation list | Select the intended outcome | Assume the newest is correct |
| Dirty target baseline | Git status and your diff | Preserve unrelated work through your normal commit/worktree workflow | Reset somebody else's changes |
| Higher-autonomy refusal | Capability name and printed remedy | Repair and recheck the named capability | Claim lower mode is equally enforced |
| Scope violation/quarantine | Allowed versus changed files | Review unexpected writes; redesign scope explicitly if justified | Widen all writes to get green |
| Failing deciding check | Command, directory, output | Reproduce, fix behavior, rerun | Weaken the expected result |
| Already-green/irrelevant check | Before/after evidence | Supply a discriminating check | Rename NO-DATA to PASS |
| Contract refusal | Named schema/coverage error | Resolve the decision or cover the promised check | Delete requirements to bypass validation |
| Missing worker capability | Invocation error and host setup | Restore the supported worker path; in a coding session follow the skill's plan/contract route | Retry unsupported nested sessions |

## 3. Resume, do not reconstruct

Discover unfinished work for this target:

```bash
python3 scripts/brother_run.py --continue --cwd "$TARGET_REPO"
```

This is an execution command, not read-only listing: one match resumes directly; multiple matches are listed for selection. To choose the first displayed match:

```bash
python3 scripts/brother_run.py --continue 1 --cwd "$TARGET_REPO"
```

If you retained the exact run path, set `RUN_DIR` to it:

```bash
python3 scripts/brother_run.py --resume "$RUN_DIR" --cwd "$TARGET_REPO"
```

If the original run used `--runs-root`, supply that same absolute directory for discovery. Do not invent another store because discovery found nothing. In the host conversation, you can instead ask Brother to resume the unfinished outcome and identify the saved run it is using.

## 4. Verify recovery

The engine preserves already integrated units on resume. Read the new result and final receipt path. Confirm the original failure is resolved by evidence, not hidden by changed requirements. Rerun the relevant check, inspect the complete diff, and make acceptance a separate decision.

If it still fails, report the Brother revision, host/version, command, exit code, sanitized error, and whether it reproduces in a disposable repository. Keep the original run locally. Never attach credentials, customer records, or an entire private Vault to an issue.

Next: [review a receipt](review-a-receipt.md), [retain a lesson](use-the-vault.md).
