---
name: next
description: Recommend the single best next step for the project
---

Outcome to produce: one recommended next action, stated first, with a short reason and a realistic time range. Alternatives only when they are materially useful.

Enter the next-step flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" next --project-id <id>` (the packaged console script is `brothermode next`) and read its `next:` line and the `WHY:` reason beside it: the single ready task (dependency-satisfied per the protocol's own definition of that state), picked by highest priority and then whichever was added first as the tie break. Never answer from memory of this conversation, and never read CANVAS.md by hand, since CANVAS.md is a generated view, not the source of truth. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/brothermode_cli.py next --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

Present that recommendation as exactly one next step, with its reason. Estimates follow the rules in references/forecasting.md: always ranges with a confidence level, never single numbers.

When a decision from the user is what blocks progress, `/brothermode:status`'s Next step field says so before this skill would find any ready task at all; present that decision first, as a decision card in the shape of references/kickoff.md, recommended option first with its Why. The last option on that card is always the user's own: they can take the decision and the work under it back, and the project records where the work stopped and what would have been chosen. Never drop that option to shorten the card.

v3 note: this skill is the canonical replacement for the legacy `/brotherme-next` command (the founder's 2026-08-07 night rename decision). Freeze answer 4 names `next` as one of the ten verbs `tools/brothermode_cli.py` owns, mapped to `bm_project.py next`; the legacy command instead read the Next step field off `bm_lead.py status`. Those are two different tools answering related but not identical questions (one recommendation drawn from the founder's eight-field status view, the other the single highest-priority ready task), so this v3 skill follows the boundary the freeze actually specifies rather than reproducing the legacy command's exact wiring; `/brothermode:status` still carries the Next step field for the decision-blocked case above.
