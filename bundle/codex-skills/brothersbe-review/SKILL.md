---
name: brothersbe-review
description: "Use when reviewing a diff, a pull request, or a colleague's change against the design it claims to implement. Runs the deterministic reviewer route to decide who looks, dispatches only the read-only specialists it names, normalizes and deduplicates every finding into the landed schema, and returns a fixed summary (ready or not, mechanical counts, the lenses used, blockers, improvements, pre-existing issues, one next action) with detail underneath. Invoke as /brothersbe:review."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-review/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
