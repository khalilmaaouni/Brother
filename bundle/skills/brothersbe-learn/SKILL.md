---
name: brothersbe-learn
description: "Use when a lesson from an incident, a repeated correction, a review finding or a measured outcome should become a shared rule, or when a session wants to propose an amendment to the laws. Proposes; it never lands a change to shared behavior. Invoke as /brothersbe:learn."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-learn/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
