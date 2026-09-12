---
name: brothersbe-prove-this-change
description: "Use when someone is changing an API response or request shape, an event or message contract, a partner or third party integration, a retry, timeout, or idempotency path, a queue consumer, a service boundary, a warehouse model, a SQL transformation, a dbt model, an ELT or ingestion step, a table schema, or a backfill, or when they ask whether a change will break something and what proof a reviewer will want. Fires on the work itself, with nothing started and no command learned. No BrotherSBE session, dossier, or setup required."
---

This is the Codex route for the `brothersbe` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothersbe-prove-this-change/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
