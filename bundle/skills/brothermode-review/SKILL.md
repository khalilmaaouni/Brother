---
name: brothermode-review
description: "Check the current work against the definition of done and report what passes and what does not. Use only when the user explicitly asks for a review, an acceptance check, or whether finished work is done; never invoke speculatively just because a change looks complete."
---

This is the Codex route for the `brothermode` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothermode-review/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
