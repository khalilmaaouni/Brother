---
name: doctor
description: Check the BrotherMode install itself for problems. Use when the user explicitly asks to check the install, says something seems broken, or right after a fresh install or an update.
---

Outcome to produce: a plain-language readout of whether the BrotherMode install itself is healthy, not the project's own status.

Run the mechanical command `python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" doctor` (the packaged console script is `brothermode doctor`) and read its output; never guess at install health from memory of this conversation. A plugin install exports `${CLAUDE_PLUGIN_ROOT}` for skill and command content, so that path resolves on its own; on a clone install, where the variable is unset, run `python3 tools/brothermode_cli.py doctor` instead, from the BrotherMode root (`~/.claude/skills/brothermode`).

The command runs ten named checks, each read-only, each printing PASS, FAIL with one plain-word remediation a non-engineer can follow, or SKIP with the reason nothing could be checked there. A check never crashes the whole run: an unexpected error inside one check becomes that check's own FAIL, naming the exception, so one broken check cannot hide the other nine. The command exits 0 only when every check is PASS or SKIP.

Translate the result into plain language: say first whether everything is healthy or something needs attention, then, for any FAIL, the one plain-word fix it named. SKIP is not a failure and should not be read as one; say what it means in this context (most often that a check found nothing yet to look at, or a dirty working tree made a comparison impossible for the checksum check specifically) rather than leaving it unexplained. If the user asked because something feels broken, lead with the FAILs; if they asked as a routine check after install or update, lead with the plain confirmation that all ten came back clean.

v3 note: this skill is new in v3 (the founder's 2026-08-07 night rename decision, freeze answer 4, names `doctor` as one of the ten CLI verbs; the architecture refutation's finding M1 flagged it as a public-surface addition since no `/brotherme-doctor` command existed before, and the freeze names it explicitly rather than leaving it undeclared). Side-effect classification per plan section 3.3 is Explicit/contextual: it runs on an explicit ask, or contextually right after an install or an update, never auto-triggered on ordinary conversation about the project's own progress (which is `/brothermode:status`'s job, a different question about a different subject).
