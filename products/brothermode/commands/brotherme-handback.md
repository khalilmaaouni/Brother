---
description: Take a decision and the work under it back into your own hands, with nothing lost
argument-hint: <the decision you want to take back, and why>
---

> This command works and is supported. Its current name is `/brothermode:handback`, and both names do exactly the same thing.

Outcome to produce: the user takes one decision and the work under it back, and gets one page a developer can pick up from, with nothing deleted and nothing hidden.

Enter the handback flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" handback --project-id <id> --decision-id <the decision the user named> --why "<their reason, in their own words>"` (the packaged console script is `bm-lead handback`) and read its output; never describe what happened from memory of this conversation. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py handback --project-id <id> --decision-id <the decision the user named> --why "<their reason, in their own words>"` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records. If the user has not named which decision, run `/brotherme-decisions` first and let them pick one; never guess.

Explain in plain language, before running it, what taking it back does, in this order, because the order is the safety property:

1. Autonomous work stops first. The authorisation the user signed is paused, so nothing else can start while the handover is being written. Pausing is reversible; the authorisation can be resumed later without signing again.
2. What is known right now is written down: what should happen next, what question is still open, and the evidence behind both.
3. The work in progress is set down rather than abandoned, and the file it was holding is released, so another person or another session can pick it up.
4. The project records that the user took this decision, what would have been chosen instead, what else was weighed and why not, and what would have changed that choice.
5. One page is generated for whoever takes over: what they are taking on, the decision that was in front of us, what would have been chosen and why, the open question, the files, the commands to reproduce the current state, any work that was still in flight, and where to pick up.

Where this command reaches the user from matters, and it is worth saying once. The page that shows where the project stands carries the same offer, whether or not a decision is open, and it carries no button: nothing on that page can act on the project. What it has instead is the exact words to paste back into the chat, revealed under one expander, in this shape:

    /brotherme-handback

    Take back: <the decision, in the user's own words>
    Decision id: <the id printed beside the decision>
    Why: <their reason, in their own words>

If the user pastes that, they have already named the decision and the reason, so ask nothing further and run the command. A page that could act on the project on its own would be a second place decisions get made; the paste is what keeps a person in the loop by construction rather than by good intentions.

The page for whoever takes over is written in two forms from the same records: the page under `Handover/`, and the same eight sections as one page they can open in a browser (`python3 tools/bm_view.py brief-page --project-id <id> --insight-id <the decision id>`, the packaged console script being `bm-view brief-page`). The two carry the same sections and the same traceable claims, because the second is generated from the same rows rather than retyped beside the first. Offer whichever suits the person picking the work up, and say plainly that they are the same content in two forms.

Then say plainly what nothing here does: nothing is deleted, nothing already recorded is rewritten, and the choice that was not taken is kept alongside the user's own reason, so a reader a month later sees both. Work that was already in flight when control changed hands stays in flight and is listed by name on the handover page rather than silently cancelled.

If any step after the pause fails, report it with the error card format in references/kickoff.md (What happened, Impact, Recommended action, What remains safe), say plainly that the authorisation is paused and which steps did not run, and do not resume the authorisation to tidy the failure up. A half-finished handback that has been un-paused is a project running on its own again while a person believes they have the wheel.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-handback` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:handback` at `skills/handback/SKILL.md` (an internal, hidden skill: reachable by exact name, not part of the nine advertised in `/help`). Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-handback` remains.
