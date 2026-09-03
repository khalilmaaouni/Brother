---
name: brief
description: Ask for the short catch-up on where the work stands, what it cost, and what is waiting on you
user-invocable: false
---

Outcome to produce: one short catch-up the user can read in under a minute, built from that project's own records, or an honest line saying that the last one still stands.

Enter the catch-up flow of the brotherme skill. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_lead.py" brief --project-id <id>` (the packaged console script is `bm-lead brief`) and read its output; never write a catch-up from memory of this conversation. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_lead.py brief --project-id <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads and writes that project's own records.

The catch-up is at most six lines and the command prints them: where we are, what changed, what it cost, what was decided, what is still uncertain, and the options open to the user. The last line is always there and always last, and it always includes handing the work back. Read them out as printed. Costs already spent are facts and print as numbers; anything still to come is a range with a confidence level, never a single number, per references/forecasting.md.

Nothing happened since the last one is a real answer, and this skill gives it rather than filling the space. When there is nothing new to report, the command writes no new catch-up at all: it names the one that still stands, says how long ago it was, gives the one recommended next step, and repeats the options open to the user. Say exactly that, in plain words. Do not restate the old catch-up as if it were fresh, and do not assemble a new one by hand: a timeline padded with empty entries is worth less than a short one a reader can trust.

When there has never been a catch-up for this project, the command says so and names what would produce one. Read that out too, rather than inventing a first catch-up out of the conversation.

The same catch-up arrives on its own, without being asked for, once enough real work has accumulated or when the work crosses a boundary such as a step opening or closing. Asking for one here never doubles it up: whichever came first is the one that was recorded, and this skill reads that record.

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-brief` command (the founder's 2026-08-07 night rename decision, refutation ruling B5). `user-invocable: false` fits: it is a low-stakes catch-up read a user would naturally phrase as a question ("what's new"), so letting Claude recognize and answer it is the same restraint the public `status`/`next` skills already get under Allowed classification. It does not call `tools/brothermode_cli.py`: `bm_lead.py brief` is not one of the ten verbs the boundary owns, so this stays a documented internal-adapter exception per ruling H4.
