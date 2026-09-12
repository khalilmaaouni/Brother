---
name: brothersbe-kickoff
description: "Use before designing or writing anything, when a backend, infrastructure, or data engineering change has not had its ground mapped yet and nobody has named what will verify it before it begins. Classifies the work profile, maps the ground (git state, disk, the repo's own build and test commands), scores the intake into a tier, and names the checks that will verify the work before the work begins. Fires on the work itself, the same way start already routes into it, so nothing needs to be typed to reach it."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-kickoff/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
