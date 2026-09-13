# Hand off work to a teammate

Use this path when another person must continue, review, accept, or release
the work. A handoff is a transfer of context and authority, not a request for
the next person to trust a summary.

## 1. Name the decision

Write down the outcome, the current state, and the decision still needed. Name
who can accept delivery, merge, release, override a gate, and accept material
`NO-DATA`. Brother records evidence, but it does not invent authority.

## 2. Freeze the working boundary

Record the target repository, branch, intended files, dependencies, and the
next concrete action. Check for unrelated changes:

```bash
git status --short
git log -3 --oneline
git worktree list
```

Do not reset, clean, or rewrite somebody else's work to make the handoff look
tidy.

## 3. Carry the evidence

Give the teammate the actual receipt path and the receipt's per-file checks.
For each check, include the exact command, exit code, and what it proves. Call
out `NO-DATA`, unresolved findings, environmental assumptions, and checks that
were already green before the change. The teammate should be able to rerun the
relevant commands from the target repository.

If the run stopped, keep its run directory and use [Recover from failure](recover-from-failure.md).
Resume the original outcome rather than reconstructing it from chat:

```bash
python3 scripts/brother_run.py --continue --cwd "$TARGET_REPO"
```

## 4. State the next action

End with one action, one owner, and one stopping condition. Examples include:

- rerun a named check and decide whether its evidence is sufficient;
- resolve a named `NO-DATA` item and update the receipt;
- review the bounded diff, then accept or reject delivery;
- continue the existing outcome from its run store.

Do not ask the teammate to search every file or infer the boundary from a
branch name.

## 5. Close the loop

The receiving teammate confirms the outcome, target, boundary, receipt, next
action, and authority. The receiver then records their decision separately
from the receipt. Acceptance is not the same decision as release.

For a team adopting Brother, see [Add Brother to an existing repository](add-brother-to-an-existing-repo.md)
and [Adopt Brother on a team](adopt-on-a-team.md). For the detailed review
standard, read [Review a Brother receipt](review-a-receipt.md).
