---
description: Check the current work against the definition of done and report what passes and what does not
---

> This command works and is supported. Its current name is `/brothermode:review`, and both names do exactly the same thing.

Outcome to produce: an honest review verdict the user can trust, leading with what is solid and what is not, in plain language.

Enter the review flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" review <task_id>` to record the evidence and move the task through its real state; never answer from memory of this conversation about whether it passed. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_project.py review <task_id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records. Apply every point of the definition-of-done checklist at references/definition-of-done.md to the current work. Each point gets a pass, a not-yet, or a NO-DATA, with the evidence that proves it (a command that ran, a file that exists, a check that passed). NO-DATA applies when the evidence could not be read or the check did not run, never when the check ran and failed. NO-DATA is never a pass and never a block on its own: it names what could not be read and the next action that would produce the evidence. Never soften a failing point; bad news comes first.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-review` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:review` at `skills/review/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-review` remains.
