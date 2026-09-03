---
description: Recommend the single best next step for the project
---

> This command works and is supported. Its current name is `/brothermode:next`, and both names do exactly the same thing.

Outcome to produce: one recommended next action, stated first, with a short reason and a realistic time range. Alternatives only when they are materially useful.

Enter the next-step flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" status --project-id <id>` (the packaged console script is `bm-lead status`) and take the recommendation from its Next step field, which is computed from that project's own records and is always exactly one action with the reason it was chosen. Never answer from memory of this conversation, and never read CANVAS.md by hand, since CANVAS.md is a generated view, not the source of truth. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py status --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

Present that recommendation as exactly one next step, with its reason. Estimates follow the rules in references/forecasting.md: always ranges with a confidence level, never single numbers.

When a decision from the user is what blocks progress, the Next step field says so, and that decision comes first, as a decision card in the shape of references/kickoff.md, recommended option first with its Why. The last option on that card is always the user's own: they can take the decision and the work under it back, and the project records where the work stopped and what would have been chosen. Never drop that option to shorten the card.

To see the ranked task list behind the recommendation rather than the single recommended action, run `python3 tools/bm_project.py next` and read its output. That is the machinery view of the same question, shown only when the user asks for it.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-next` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:next` at `skills/next/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-next` remains.
