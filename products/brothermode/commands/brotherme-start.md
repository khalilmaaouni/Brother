---
description: Start a project with a short guided conversation that ends in one clear project brief
argument-hint: <what you want to build or achieve>
---

> This command works and is supported. Its current name is `/brothermode:start`, and both names do exactly the same thing.

The user wants to start a project. Their goal, in their own words: $ARGUMENTS

Outcome to produce: one clear project brief (the Project Canvas) and one recommended first decision, in plain language, with a realistic time and cost range.

Follow skills/start/SKILL.md in this plugin exactly; it is the single source for this command.

Answer in the language the user wrote in, per references/honesty.md. If the user is standing in a repository that already holds code but no project yet, the first step is not the guided kickoff: it is adopt (one command, at most one question), which reads what the repository already says instead of asking for what it can read itself. Otherwise, enter the guided kickoff flow of the brotherme skill: size up the goal, ask only the questions whose answers change the scope, one decision at a time with a recommended option first.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-start` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:start` at `skills/start/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-start` remains.
