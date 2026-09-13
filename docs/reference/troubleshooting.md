# Troubleshooting reference

## The agent seems frozen

Likely cause: the worker is waiting on model access, a check, or an unfinished
run. Inspect the host output first. Then check the repository and run store:

```bash
git status --short
git worktree list
```

If the run was interrupted, resume the same outcome with
`python3 scripts/brother_run.py --continue --cwd "$TARGET_REPO"`. Do not delete
the run directory or start a competing run before checking for a receipt.

## It says done but I do not believe it

Likely cause: a summary or process exit was mistaken for evidence. Find the
actual `brother_run: receipt:` line and open the receipt it names. Confirm that
each changed file has an exact deciding check and exit code. Rerun the relevant
check after the change. A broad green suite can still be `NO-DATA` for the
claim if it would also have passed before the change.

## It picked the wrong file

Likely cause: the outcome or unit scope was ambiguous, or an unexpected write
was quarantined. Compare the intended paths with `git status --short` and the
receipt's changed-file entries. Inspect the declared `writes` for the unit. Keep
unrelated work intact, narrow the outcome, and retry only after the intended
scope is explicit.

## It is burning tokens

Likely cause: the task is too broad, the context contains unrelated material,
or the worker is repeating a failed path. Stop and restate one outcome with a
small file scope and one deciding check. Use [Token optimization](../how-to/token-optimization.md)
to reduce context and [recover from failure](../how-to/recover-from-failure.md)
to preserve evidence before retrying.

## I lost track of where the work is

Likely cause: run state is outside the target repository, or an interrupted
run was not resumed. Check the target status, worktrees, and the host's Brother
run store. Use the printed receipt path, not a path copied from another run.
If work is unfinished, resume with `python3 scripts/brother_run.py --continue
--cwd "$TARGET_REPO"`. If no unfinished work is found, ask for a new outcome;
do not invent a run id.

## Nothing to continue

Confirm repository and host run store. “Nothing unfinished” is valid; do not manufacture a run id.

## Dirty-tree refusal

Move plan/run artifacts outside the target repo and deliberately resolve unrelated changes.

## NO-DATA despite a green check

Run the check against the pre-change state. If already green, rewrite it. Also inspect whether it exercises the claimed dependency and whether relevant files changed.

## Quarantined unit

Compare actual changed paths with declared `writes`. Do not widen the fence merely to make status green.

## Codex plugin exists but safety hooks do nothing

Verify Codex hook install/trust separately from plugin presence.

## Hooks affect an unexpected repository

Read [Hook scope](hooks.md), inspect install path, marker, and `.brother/config`. Ignore old blanket claims.

## Vault returns no memory

Confirm a Vault root is configured. Unconfigured is not retrieval failure.

## Public doc references a missing file

Treat it as a documentation failure. Determine whether the path is runtime-generated/historical or genuinely missing. Fix the canonical public link; never create fake evidence to satisfy it.
