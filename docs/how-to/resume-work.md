# Resume unfinished work

Use this when a Brother run was interrupted or intentionally stopped.

## User-facing route

Invoke Brother or ask to continue/resume in the same repository. The door should discover unfinished work rather than require internal run ids. If several outcomes exist, choose by plain-language outcome.

## Engine-level route

```bash
python3 <brother-runtime>/brother_run.py --continue --cwd <repo>
```

Use the same host run store as the original run.

## Dependency waves

A dependency graph can hand work over in waves. Finish current ready worktrees inside their write fences, then continue so the engine can audit/integrate and unlock later units.

## Verify the result

Resume must continue the existing outcome, not create a competing run. Confirm the final receipt belongs to the intended outcome and includes resumed units.
