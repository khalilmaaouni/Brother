---
name: auto-status
description: Show where the Full-Auto controller run stands right now, in plain language
user-invocable: false
---

Outcome to produce: one short status view of the CONTROLLER run (distinct from `/brothermode:status`, which reports the project's own beginner-facing status) that the user can read in under a minute.

NAMING NOTE for whoever maintains this file: `/brothermode:status` already exists and reports the founder's eight-field view, a different, established skill this file must never collide with or silently redefine. This skill is named `auto-status` on purpose, alongside `auto` and `stop`, so both status views can be reached without either one shadowing the other.

Enter the Full-Auto status flow. Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_controller.py" status --project <id>` and read its output; never answer from memory of this conversation about where the run stands. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/bm_controller.py status --project <id>` instead, from the BrotherMode root (`~/.claude/skills/brothermode`). Either way, run it from the user's project folder so it reads that project's own records.

Translate the report into plain language: the run's state (and what that state means for what happens next, per `docs/FULL-AUTO.md`'s state-machine section), how many units are in each stage, any unit whose result is still awaited (name it, since that is exactly what would block the run from making further progress without the user's help), the spend verdict, and any open, founder-only step. If there is no controller run for this project yet, say so plainly and point at the full-auto flow to begin one.

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-auto-status` command (the founder's 2026-08-07 night rename decision, refutation ruling B5). It is read-only, so `user-invocable: false` is the fit here rather than the invocable-but-model-restricted shape given to `auto` and `stop`: Claude can surface it whenever the user asks how a running Full-Auto session is doing, the same class of question `/brothermode:status`'s Allowed classification already covers for the project's own status. It does not call `tools/brothermode_cli.py`: `bm_controller.py status` is not one of the ten verbs the boundary owns, so this stays a documented internal-adapter exception per ruling H4.
