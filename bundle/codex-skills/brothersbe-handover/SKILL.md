---
name: brothersbe-handover
description: "Use when someone wants to hand this change's ownership to another named human, or when a receiver wants to inspect and accept or reject a handover already prepared for them. Runs the status and worktree checks first, then prepares (or reads) 12-handover.json through the sbe handover engine and renders the concise handover summary a receiver needs, never the project's whole history. Invoke as /brothersbe:handover."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-handover/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
