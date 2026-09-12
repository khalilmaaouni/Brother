---
name: brothersbe-verify
description: "Use when work is about to be called done, a figure that could reach a decision has been produced, a schema migration is part of the change, the change touches money or a partner path, or a verification plan is being written. Runs the hard gates and reports PASS, FAIL or NO-DATA with the evidence each verdict actually read. Invoke as /brothersbe:verify."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-verify/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
