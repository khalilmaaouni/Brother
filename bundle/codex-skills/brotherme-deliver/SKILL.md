---
name: brotherme-deliver
description: "Package the finished work into one delivery summary with the evidence that it works"
---

This is the Codex route for the existing Claude command `/brothermode:brotherme-deliver`.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothermode-deliver/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
