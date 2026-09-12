---
name: brothersbe-start
description: "Use as the single entry point when someone wants to begin or resume work and does not know, or does not care, which command comes next. Detects existing state, resumes it when found, and otherwise asks for the outcome in plain language and routes into the next step. Invoke as /brothersbe:start."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-start/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
