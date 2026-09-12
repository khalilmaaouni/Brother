---
name: brothersbe-status
description: "Use when someone wants to know where their work stands. Wraps the status command and the fence registry, and reframes the raw output as a plain answer with one next action, with technical detail kept below the summary rather than leading it. Invoke as /brothersbe:status."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-status/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
