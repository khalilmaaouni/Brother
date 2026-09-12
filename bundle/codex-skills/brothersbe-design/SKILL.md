---
name: brothersbe-design
description: "Use when someone is deciding or reviewing the shape of a system before it gets built: what it is for, how the process runs, what the architecture and the data model look like, and how it will be verified. Runs the six design phases in order, each gating the next, and checks the dossier for the artifacts the tier requires. Fires on the work itself: start already routes into it once kickoff has scored the intake, so nothing needs to be typed to reach it."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-design/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
