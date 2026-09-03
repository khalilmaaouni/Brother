---
name: handover-pack
description: Generate the handover pages that let another person take this project over
disable-model-invocation: true
---

Outcome to produce: one folder of generated pages, written from that project's own records, that a person who was not in this conversation can read and act on.

Enter the handover flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" handover-pack --project-id <id>` (the packaged console script is `bm-lead handover-pack`) and read its output; never write any of these pages by hand and never fill a gap in them from memory of this conversation. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py handover-pack --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records.

Seven pages are written into a `Handover` folder at the top of the user's project, in reading order:

1. `00-SITUATION.md`: where the project stands, what was authorised, what has been spent against the ceilings, how much of the planned work is accepted, the current forecast, and anything open that only a person can clear.
2. `10-DECISIONS.md`: every decision taken, newest first, grouped by what it was about, each with what else was weighed and what would have changed it.
3. `20-RISKS.md`: the risks still open, newest first, each with what would settle it.
4. `30-CALIBRATIONS.md`: the checks that were deliberately broken to prove a guard actually bites, each with what was broken and what was observed.
5. `40-LEARNINGS.md`: what was learned along the way that a reader would otherwise have to rediscover.
6. `50-TIMELINE.md`: every catch-up in order, oldest first, so a reader can replay the run forwards.
7. `60-HANDBACKS.md`: every decision the user took back, each with the decision it answers and the full page a developer picks up from.

Who this is for: a business analyst or a project lead who has to take the project over or account for it, and who has no access to this conversation. Every claim on every page carries how it was checked and the id of the record it came from, so any line can be traced back rather than trusted. A claim resting on reasoning rather than on a check that ran says so on its own line.

Two properties worth telling the user in plain words. The pages are generated, so regenerating them changes nothing unless the records changed, and a page written today still reads the same a month from now because it only shows what was known at the moment it covers. The pack covers one project: a folder holding several projects produces one set of pages per project rather than a merged view, and no page implies otherwise.

If the project has no records yet, say so plainly and point at `/brothermode:start`; do not generate a folder of empty pages.

Memory ceremony: distill mistakes into the vault. Before or as part of closing a handover, run `tools/bm_vault_distill.py` against a JSON list of this session's own named mistakes (`slug`, `title`, `detail`, `symptom` for each) so they land as searchable `40-Failures/` notes in the vault rather than staying only in this conversation. It searches the vault first, per the vault's own constitution, and skips writing when a close match already exists so a mistake never gets filed as a silent duplicate; this is the exact pattern proven in `tools/test_bm_vault_distill.py`. Command: `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_vault_distill.py" distill --input <path/to/mistakes.json>` (on a clone install, where the variable is unset, run `python3 tools/bm_vault_distill.py distill --input <path/to/mistakes.json>` from the BrotherMode root instead).

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-handover-pack` command (the founder's 2026-08-07 night rename decision, refutation ruling B5). `disable-model-invocation: true` fits: like `deliver`, it is a generation action producing a real output someone else acts on, a moment the user should control rather than Claude triggering because a conversation looked handover-shaped. It does not call `tools/brothermode_cli.py`: `bm_lead.py handover-pack` is not one of the ten verbs the boundary owns, so this stays a documented internal-adapter exception per ruling H4.
