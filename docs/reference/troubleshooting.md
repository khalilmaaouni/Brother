# Troubleshooting reference

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
