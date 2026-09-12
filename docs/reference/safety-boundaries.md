# Safety boundaries

**Brother is not an operating-system sandbox or a guarantee of safe unattended execution.** Instructions, launch checks, tool hooks, and post-work verification protect different boundaries. Inspect the active host and run, not just the installed plugin name.

## What acts where

| Control | When it acts | What it establishes | What it does not establish |
| --- | --- | --- | --- |
| Outcome and allowed writes | Before work | An inspectable agreement about the task | Instructions alone do not prevent a write |
| Managed capability probe | Before launch, including resume | Whether requested higher autonomy has the measured capabilities it requires | Every ordinary run is not automatically enforced |
| Per-lane scope claim and fence hook | Before launch and on supported tool events | A write boundary subject to active enforcement | Complete shell, network, or credential isolation |
| Worktree isolation | During work, where available | Separation of repository changes | Isolation from the rest of the computer |
| Checks and integration validation | After worker output | Evidence and scope inspection for the result | Prevention of side effects that already occurred |
| Receipt validation | At delivery | Structured evidence or refusal if the receipt cannot be written | Independent audit or complete test coverage |
| Human acceptance and release | After review | Explicit decision authority | Permission to ship because a check returned zero |

## Read the mode, not the marketing

`scripts/brother_run.py` probes capabilities before fresh and resumed runs. An explicit A0 request is refused when the capability floor cannot support A0. Other modes can continue with the message `not enforced` when capabilities are missing. Do not treat those runs as equivalent.

The probe and per-lane claim materialization live in [managed_safety.py](../../scripts/managed_safety.py). The worker launch path is in [loop_bridge.py](../../scripts/loop_bridge.py). These are application controls, not a hardened security boundary against an adversarial process with the same user permissions.

Depending on route/host, Brother may inspect repositories, create bounded units/worktrees, run declared checks, write run state/receipts, integrate verified local units, recall Vault context, quarantine scope violations, and ask for explicit human decisions.

That does **not** mean Brother automatically:

- approves the change for the human;
- merges a pull request to the default branch;
- publishes a release;
- deploys production;
- makes financial/contractual commitments;
- converts missing evidence into PASS;
- makes Vault memory authoritative;
- guarantees security because one security check passed.

## Shell checks

A `done_check` is executable code. The current engine applies additional restrictions to model-adopted replacement checks. Use installed runtime code/tests for the exact allowlist/syntax policy.

## Handback

Workers/lanes hand work back; review/merge are separate authority steps.

## Failure policy

Distinguish a policy allow/deny from inability to inspect. Some hooks may fail open on their own parser/internal failure to avoid bricking unrelated work. That is not evidence that the requested action was safe.

A concrete residual limit documented in `managed_safety.py`: a PreToolUse payload with a non-string `tool_name` can bypass the fence's mode check. This guide does not claim that gap is closed. Check the current implementation and tests before relying on hook enforcement for sensitive work.

Keep host permissions and credentials restrictive. Use an appropriate sandbox where risk requires one. Never test an escape scenario against production data. Missing capabilities should narrow delegation, not encourage broader permissions.

## Authority

`bundle/skills/using-brother/references/router-details.md` and current runtime/hook tests.
