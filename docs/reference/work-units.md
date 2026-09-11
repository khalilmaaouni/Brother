# Work-unit contract

A work unit is a bounded piece of execution that can be checked and integrated independently.

## Current in-session shape

```json
{
  "id": "U1",
  "objective": "what is true when this unit is done",
  "done_check": "one shell command that decides it",
  "writes": ["path/one.py"],
  "deps": []
}
```

The current router describes 2 to 9 units for this route.

## Rules

**Deciding check:** run it before work. If already green, it cannot establish that the change caused the result.

**Writes fence:** declare every path the unit may touch. Out-of-scope writes should be quarantined/refused, not normalized after the fact.

**Dependencies:** `deps` names valid unit ids. Dangling edges are refused before work.

**Concurrency:** independent units may run separately; units writing the same path do not run together. The current in-session router limits a batch to at most three worktrees.

**Plan location:** keep the plan outside the target repo. A plan inside dirties the baseline the engine expects to inspect.

## Handback

In the current in-session route, the engine can claim ready units and hand worktrees to the session. The session edits only declared files, runs the done check there, leaves work in the lane, then continues so the engine can audit, commit, verify, and integrate.

## Authority

`bundle/skills/using-brother/references/router-details.md` plus current engine validation/tests.
