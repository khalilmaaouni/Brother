# Add Brother to an existing repository

Most work starts in a repository that already has tests, CI, history, local
conventions, and changes in flight. Brother should make that work more
inspectable without replacing the repository's existing proof or rewriting
its history.

## What changes

Brother adds an outcome, bounded work units, deciding checks, and a receipt to
the workflow. It does not make the existing test suite sufficient by itself,
turn off CI, or turn an unreviewed result into an accepted change.

Run state and receipts belong in a durable Brother run store outside the target
source tree. Repository-local configuration may use `.brother/config`. Existing
tests, CI commands, hooks, and review rules remain the repository's baseline.

## 1. Inspect before installing or running

Start from the repository root and preserve the current state:

```bash
git status --short
git log -5 --oneline
git branch --show-current
```

Read the contribution notes, identify the normal test command, and note any
uncommitted changes. Do not reset or clean a shared working tree. Decide where
the external Brother run store will live before the first run.

## 2. Establish a baseline

Run the repository's normal focused check, then its broader check when the
change needs it. Record the command and result. A check that was already green
before the change is useful baseline evidence, but it does not prove the new
behavior.

Identify the smallest task whose expected result you understand. State the
behavior, success checks, affected files, and exclusions. Include the existing
test command when it covers the claim, and add a discriminating check when it
does not.

## 3. Scope the first run

Ask Brother to show the outcome, work-unit boundaries, and deciding checks
before execution. Keep the first run away from deployment, broad permissions,
and unrelated cleanup. Reuse the repository's test and CI tooling. Do not add
a dependency only to make the Brother workflow look complete.

Check the effective hook scope and safety mode for the target repository. Plugin
presence alone does not establish trust, enforcement, or worker availability.

## 4. Review the result

Read the receipt that the run actually printed. Confirm each changed file maps
to declared work and each claim has a check that could fail when the behavior is
wrong. Rerun the repository's relevant tests after the change. Keep the
existing CI review path in place.

If the run stops or refuses, preserve its evidence and use [Recover from failure](recover-from-failure.md).
If the work must move to another person, use [Hand off work to a teammate](hand-off-to-a-teammate.md).

## What to do first

The first useful action is a small, reversible, reviewable change on the
current branch or an explicitly isolated worktree. The first result should
answer three questions: what changed, which check decided it, and what remains
unproven. Expand the scope only after a teammate can review that receipt and
accept the next step.

For team-wide practice, read [Adopt Brother on a team](adopt-on-a-team.md).
