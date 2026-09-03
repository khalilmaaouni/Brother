---
name: decisions
description: Show the decisions waiting on you, highest stakes first, each with a recommended option
user-invocable: false
---

Outcome to produce: the open decisions this project is waiting on, one card each, highest stakes first, in plain language the user can answer without reading any machinery.

Enter the decision flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" decisions --project-id <id>` (the packaged console script is `bm-lead decisions`) and read its output; never assemble a decision from memory of this conversation, and never invent an option the records do not hold. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py decisions --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

Present each decision exactly as the decision card format in references/kickoff.md defines it: the recommended option first with its Why, each alternative with its Tradeoff on one line, two to four options in all. In Claude Code the card travels through the AskUserQuestion window, recommended option first; chat text carries the evidence and the context, never the option list. One card per decision, and only the highest-stakes one if the user asked for the single thing waiting on them.

The last option on every card is always the same one, and it is not optional: the user may take the decision and the work under it back into their own hands, and the project records where the work stopped and what would have been chosen. Read that option out as the command prints it, in its own words, without shortening it. A decision card that reaches the user without it is a defect, not a style choice, and the command is built so that such a decision cannot be recorded in the first place.

Two honesty rules apply to every card. A claim that rests on reasoning rather than on a check that ran says so, in the words the command prints, and is never read out as a settled fact. A decision that a person judged to be a founder's call rather than one the records detected says that too, on the card, so the user knows which kind of question they are answering.

If nothing is waiting, say so plainly in one line and name the one recommended next step instead. Do not manufacture a decision to fill the space.

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-decisions` command (the founder's 2026-08-07 night rename decision, refutation ruling B5). `user-invocable: false` fits: it is a read-only surfacing of decisions already on file, the same class of question the public `status`/`next` skills already field under Allowed classification. It does not call `tools/brothermode_cli.py`: `bm_lead.py decisions` is not one of the ten verbs the boundary owns, so this stays a documented internal-adapter exception per ruling H4.
