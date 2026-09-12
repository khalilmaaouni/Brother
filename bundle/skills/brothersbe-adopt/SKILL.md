---
name: brothersbe-adopt
description: "Use when someone is adding risk and evidence checks to a repository that has never had them, or is checking whether an existing installation is actually wired up. Inspects the repository for readiness, proposes a configuration, and reports what is present, what is missing and what it could not check. Dry run by default. Invoke as /brothersbe:adopt."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-adopt/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
