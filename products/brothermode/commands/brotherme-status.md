---
description: Show where the project stands right now, in plain language
---

> This command works and is supported. Its current name is `/brothermode:status`, and both names do exactly the same thing.

Outcome to produce: one short status view the user can read in under a minute, leading with what has been achieved, not with process.

Enter the status flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" status --project-id <id>` (the packaged console script is `bm-lead status`) and read its output; never answer from memory of this conversation about where the project stands. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py status --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

The command prints the default status view of references/status-view.md itself: exactly Goal, Direction, Progress, Time remaining, Decision needed, Risk, Evidence, and Next step, in that order, each field computed from that project's own records rather than translated out of a report. Read them out as printed. A field the records cannot answer says so in its own words, and that stays in: "not forecast yet" is information, and a smoothed-over sentence is not. Time remaining is always a range with a confidence level and never a single number (references/forecasting.md). When a decision is waiting, it travels as a decision card in the shape of references/kickoff.md rather than as prose, and the last option on that card is always the user's own: they can take the decision and the work under it back. For what counts as worth flagging, follow references/pulse.md.

After the eight fields have been read out, and never before them, offer the page in ONE line: the same eight answers plus the drawings, the history of what was learned and the standing offer to take the work back are in `PROJECT-VIEW.html`, and `/brotherme-view` writes it. One line, at the end, and only once there is something in the records worth looking at. A status answer that opens by pointing somewhere else has not answered the question that was asked.

Deeper detail only on an explicit request, and never by default. `--advanced` adds the nine machinery items references/status-view.md lists, for that one request only; the next status returns to the eight fields. `--ic` is the separate engineer's view described on the same page, and every render in that mode names the switch that turned it on. When the user asks how the project got here, run `python3 tools/bm_project.py status --history N` and read out the last N recorded actions (who did what, and when); leave that off the default view.

---

## Maintainer note, not for the reader above

Kept verbatim from where it used to sit at the top of this file. It was moved on 2026-08-29 because it was the first thing anybody read: the team reported finding fifteen commands and every one of them declaring itself a legacy compatibility shim, which reads as an abandoned product. The mechanism is unchanged and nothing was removed.

> DOCUMENTATION NOTICE, 2026-08-11 (V3 Final, task A2). This command file is not part of the six-name public surface. It keeps working exactly as it does today and is not deprecated in behaviour; only its documented status changed. Physical consolidation of these shims is a later tranche, so nothing here is removed in this release.

> LEGACY v2 COMPATIBILITY SHIM (the founder's 2026-08-07 night rename decision, recorded in this project's working history rather than a file this repository ships). Legacy surface: `/brotherme-status` under the pre-rename `brotherme` plugin id. Replacement: `/brothermode:status` at `skills/status/SKILL.md`. Reason: the founder's 2026-08-07 night namespace rename retired the flat `commands/` layout as the canonical public surface; this file is kept, unchanged below, only so a v2 install or a v2 habit still resolves during the migration window. Test: `tools/test_bm.py`'s `TestTheSeventhCommandAndTheDeepTourAreWired` (the fifteen-command inventory pin) and the naming/ACTIVE_DOCS scan in `tools/test_bm_docs.py` still exercise this exact file and path; do not rename or delete it without updating both. Removal condition: the v3.0.0 tag, at the release court described in freeze answer 14, once `claude plugin validate` and a repository grep show no live consumer of `/brotherme-status` remains.
