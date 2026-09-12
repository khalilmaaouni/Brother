---
name: brothersbe-work
description: "Use when a plan already exists and its tasks are ready to be built, not just recommended or designed. Reads the engine's JSON state, briefs and starts one to three independent ready tasks, dispatches each to an implementation-worker subagent inside the worktree already opened, verifies the result and finishes, and hands a claimed task to a human on request. Fires on the work itself: start already routes into it once a plan is ready, so nothing needs to be typed to reach it."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-work/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
