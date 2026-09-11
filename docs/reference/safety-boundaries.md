# Safety boundaries

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

## Authority

`bundle/skills/using-brother/references/router-details.md` and current runtime/hook tests.
