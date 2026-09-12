---
name: brothermode-brotherme
description: "v3 internal reference for BrotherMode's guided beginner flows (kickoff detail, the deep tour, the guided-loop delegation pattern). Not a direct entry point. The public surface is six names and two of them are not shipped: /brothermode:start, :status, :deliver and :doctor work today, while verify and toolkit are named for the surface and neither ships a stub. Every other skill in this folder, including next, review, view and help, keeps working exactly as it does today and is advanced internal surface rather than part of the public six. Verified on Claude Code; this plugin packaging is a release candidate."
---

This is the Codex route for the `brothermode` product skill.

Codex has no slash command surface. Invoke this skill by name in a bounded outcome. The shared `using-brother` route remains the entry point when the outcome is ambiguous.

The Brother engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

The canonical sibling route is `skills/brothermode-brotherme/SKILL.md`. Keep PASS, FAIL and NO-DATA distinct, and read the emitted receipt before claiming completion.
<!-- generated-codex-surface: v1 -->
