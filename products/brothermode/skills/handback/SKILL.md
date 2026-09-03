---
name: handback
description: Take a decision and the work under it back into your own hands, with nothing lost
argument-hint: <the decision you want to take back, and why>
disable-model-invocation: true
---

Outcome to produce: the user takes one decision and the work under it back, and gets one page a developer can pick up from, with nothing deleted and nothing hidden.

Enter the handback flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" handback --project-id <id> --decision-id <the decision the user named> --why "<their reason, in their own words>"` (the packaged console script is `bm-lead handback`) and read its output; never describe what happened from memory of this conversation. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py handback --project-id <id> --decision-id <the decision the user named> --why "<their reason, in their own words>"` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records. If the user has not named which decision, enter the decisions flow first and let them pick one; never guess.

Explain in plain language, before running it, what taking it back does, in this order, because the order is the safety property:

1. Autonomous work stops first. The authorisation the user signed is paused, so nothing else can start while the handover is being written. Pausing is reversible; the authorisation can be resumed later without signing again.
2. What is known right now is written down: what should happen next, what question is still open, and the evidence behind both.
3. The work in progress is set down rather than abandoned, and the file it was holding is released, so another person or another session can pick it up.
4. The project records that the user took this decision, what would have been chosen instead, what else was weighed and why not, and what would have changed that choice.
5. One page is generated for whoever takes over: what they are taking on, the decision that was in front of us, what would have been chosen and why, the open question, the files, the commands to reproduce the current state, any work that was still in flight, and where to pick up.

Where this skill reaches the user from matters, and it is worth saying once. The page that shows where the project stands carries the same offer, whether or not a decision is open, and it carries no button: nothing on that page can act on the project. What it has instead is the exact words to paste back into the chat, revealed under one expander, in this shape:

    Take back: <the decision, in the user's own words>
    Decision id: <the id printed beside the decision>
    Why: <their reason, in their own words>

If the user pastes that, they have already named the decision and the reason, so ask nothing further and run the command. A page that could act on the project on its own would be a second place decisions get made; the paste is what keeps a person in the loop by construction rather than by good intentions.

The page for whoever takes over is written in two forms from the same records: the page under `Handover/`, and the same eight sections as one page they can open in a browser (`python3 tools/bm_view.py brief-page --project-id <id> --insight-id <the decision id>`, the packaged console script being `bm-view brief-page`). The two carry the same sections and the same traceable claims, because the second is generated from the same rows rather than retyped beside the first. Offer whichever suits the person picking the work up, and say plainly that they are the same content in two forms.

Then say plainly what nothing here does: nothing is deleted, nothing already recorded is rewritten, and the choice that was not taken is kept alongside the user's own reason, so a reader a month later sees both. Work that was already in flight when control changed hands stays in flight and is listed by name on the handover page rather than silently cancelled.

If any step after the pause fails, report it with the error card format in references/kickoff.md (What happened, Impact, Recommended action, What remains safe), say plainly that the authorisation is paused and which steps did not run, and do not resume the authorisation to tidy the failure up. A half-finished handback that has been un-paused is a project running on its own again while a person believes they have the wheel.

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-handback` command (the founder's 2026-08-07 night rename decision, refutation ruling B5). It stays user-invocable by explicit name with `disable-model-invocation: true`, alongside `auto` and `stop`: reclaiming control is a safety-critical action a founder must be able to trigger directly (see skills/auto/SKILL.md's v3 note for the full reasoning). It does not call `tools/brothermode_cli.py`: `bm_lead.py handback` and `bm_view.py brief-page` are not among the ten verbs the boundary owns, so this stays a documented internal-adapter exception per ruling H4.
