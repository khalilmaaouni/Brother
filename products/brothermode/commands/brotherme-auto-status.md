---
description: Show where the Full-Auto controller run stands right now, in plain language
---

> This command works and is supported. Its current name is `/brothermode:auto-status`, and both names do exactly the same thing.

Outcome to produce: one short status view of the CONTROLLER run (distinct from `/brotherme-status`, which reports the project's own beginner-facing status) that the user can read in under a minute.

NAMING NOTE for whoever maintains this file: `/brotherme-status` already exists and reports `python3 tools/bm_project.py status`, a different, established command this file must never collide with or silently redefine. This command is named `/brotherme-auto-status` on purpose, alongside `/brotherme-auto` and `/brotherme-stop`, so both status views can be reached without either one shadowing the other.

Enter the Full-Auto status flow. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_controller.py" status --project <id>` and read its output; never answer from memory of this conversation about where the run stands. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_controller.py status --project <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

Translate the report into plain language: the run's state (and what that state means for what happens next, per `docs/FULL-AUTO.md`'s state-machine section), how many units are in each stage, any unit whose result is still awaited (name it, since that is exactly what would block `/brotherme-auto` from making further progress without the user's help), the spend verdict, and any open, founder-only step. If there is no controller run for this project yet, say so plainly and point at `/brotherme-auto` to begin one.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-auto-status` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:auto-status` at `skills/auto-status/SKILL.md` (an internal, hidden skill: reachable by exact name, not part of the nine advertised in `/help`). Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-auto-status` remains.
