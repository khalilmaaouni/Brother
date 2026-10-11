---
name: brother-help
description: "Use when someone asks what Brother does, how to use it, whether the install is healthy, or whether a newer release exists. Explains in plain language and checks the install; never prints a menu of skills. Invoke as /brother help, or by this skill's name where there is no slash command."
---

# brother help

Orient in plain language: Brother turns a plain language outcome into checked work with a rerunnable receipt, and the door is `/brother <what you are trying to do>` (on a host without slash commands, the `using-brother` skill). Say which verb fits the question asked, never a menu of all of them. A doubt about the install is answered by checking it with the install doctor, `python3 "${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/brothermode_cli.py" doctor` (the eleven install health checks; `${CLAUDE_PLUGIN_ROOT}` under Claude Code), and relaying its verdicts. A version question is answered from the installed manifest, never from memory.

This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is `/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md` decides (`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor). Codex and Antigravity have no slash command surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask the person to choose a product, an autonomy code, a plan format, a run id or a test framework.

The engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is `brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. Keep PASS, FAIL and NO-DATA distinct.

## 1.1.0 names that route here

- `brotherme-help`: Explain what BrotherMode does and how to use it, in plain language
- `brotherme-update`: Check the installed version against the newest release and explain how to update, in plain language
- `brothermode-brotherme`: v3 internal reference for BrotherMode's guided beginner flows (kickoff detail, the deep tour, the guided-loop delegation pattern).
- `brothermode-doctor`: Check the BrotherMode install itself for problems.
- `brothermode-help`: Explain what BrotherMode does and how to use it, in plain language
- `brothermode-update`: Check the installed version against the newest release and explain how to update, in plain language
- `brothersbe-help`: Use when someone asks how this system works or which command or skill to use.

A request opening with one of them gets one line, `<old name> is now /brother help` (with the words the table gives it), then this verb with the rest of the words. The whole table: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md`.
<!-- generated-codex-surface: v1 -->
