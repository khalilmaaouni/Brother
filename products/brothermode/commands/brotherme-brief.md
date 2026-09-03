---
description: Ask for the short catch-up on where the work stands, what it cost, and what is waiting on you
---

> This command works and is supported. Its current name is `/brothermode:brief`, and both names do exactly the same thing.

Outcome to produce: one short catch-up the user can read in under a minute, built from that project's own records, or an honest line saying that the last one still stands.

Enter the catch-up flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" brief --project-id <id>` (the packaged console script is `bm-lead brief`) and read its output; never write a catch-up from memory of this conversation. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py brief --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records.

The catch-up is at most six lines and the command prints them: where we are, what changed, what it cost, what was decided, what is still uncertain, and the options open to the user. The last line is always there and always last, and it always includes handing the work back. Read them out as printed. Costs already spent are facts and print as numbers; anything still to come is a range with a confidence level, never a single number, per references/forecasting.md.

Nothing happened since the last one is a real answer, and this command gives it rather than filling the space. When there is nothing new to report, the command writes no new catch-up at all: it names the one that still stands, says how long ago it was, gives the one recommended next step, and repeats the options open to the user. Say exactly that, in plain words. Do not restate the old catch-up as if it were fresh, and do not assemble a new one by hand: a timeline padded with empty entries is worth less than a short one a reader can trust.

When there has never been a catch-up for this project, the command says so and names what would produce one. Read that out too, rather than inventing a first catch-up out of the conversation.

The same catch-up arrives on its own, without being asked for, once enough real work has accumulated or when the work crosses a boundary such as a step opening or closing. Asking for one here never doubles it up: whichever came first is the one that was recorded, and this command reads that record.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-brief` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:brief` at `skills/brief/SKILL.md` (an internal, hidden skill: reachable by exact name, not part of the nine advertised in `/help`). Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-brief` remains.
