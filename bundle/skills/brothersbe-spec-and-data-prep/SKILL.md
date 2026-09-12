---
name: brothersbe-spec-and-data-prep
description: "Use when a vague stakeholder ask needs to become a measurable specification with acceptance criteria, when requirements are being written or challenged before build, or when a dataset is being prepared and handed to a data scientist or analyst and its grain, keys, snapshot, and known limits must be declared. Fires on the work itself, with nothing started and no command learned. Never chooses, tunes, or interprets a model."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-spec-and-data-prep/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
