---
description: Show the decisions waiting on you, highest stakes first, each with a recommended option
---

> This command works and is supported. Its current name is `/brothermode:decisions`, and both names do exactly the same thing.

Outcome to produce: the open decisions this project is waiting on, one card each, highest stakes first, in plain language the user can answer without reading any machinery.

Enter the decision flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" decisions --project-id <id>` (the packaged console script is `bm-lead decisions`) and read its output; never assemble a decision from memory of this conversation, and never invent an option the records do not hold. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py decisions --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

Present each decision exactly as the decision card format in references/kickoff.md defines it: the recommended option first with its Why, each alternative with its Tradeoff on one line, two to four options in all. In Claude Code the card travels through the AskUserQuestion window, recommended option first; chat text carries the evidence and the context, never the option list. One card per decision, and only the highest-stakes one if the user asked for the single thing waiting on them.

The last option on every card is always the same one, and it is not optional: the user may take the decision and the work under it back into their own hands, and the project records where the work stopped and what would have been chosen. Read that option out as the command prints it, in its own words, without shortening it. A decision card that reaches the user without it is a defect, not a style choice, and the command is built so that such a decision cannot be recorded in the first place.

Two honesty rules apply to every card. A claim that rests on reasoning rather than on a check that ran says so, in the words the command prints, and is never read out as a settled fact. A decision that a person judged to be a founder's call rather than one the records detected says that too, on the card, so the user knows which kind of question they are answering.

If nothing is waiting, say so plainly in one line and name the one recommended next step instead. Do not manufacture a decision to fill the space.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-decisions` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:decisions` at `skills/decisions/SKILL.md` (an internal, hidden skill: reachable by exact name, not part of the nine advertised in `/help`). Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-decisions` remains.
