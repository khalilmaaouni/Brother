---
name: brother-status
description: "Use when someone asks where things stand, what happened, what a run did, what it cost, or what is waiting on them. Reads the run's receipt and answers in plain language with PASS, FAIL and NO-DATA kept distinct, never from memory. Invoke as /brother status, or by this skill's name where there is no slash command."
---

# brother status

Where things stand, read from evidence. "Show me what happened" is this verb: run the engine's own discovery, `brother_run.py --continue --cwd <repo>`, which names the unfinished outcome or prints `no unfinished run found`, then open the newest receipt the engine wrote under the runs root (its last line names the path, `brother_run: receipt: <path>`) and report every entry: the file, the check, the exit code that decided it. There is no second run database and no second registry; the receipt is the record. A missing receipt is NO-DATA, said as such, never a pass. Never show a run id or a run directory; name the outcome. The page that shows where a project stands is written by `python3 "${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_view.py"` (`${CLAUDE_PLUGIN_ROOT}` under Claude Code) from the project's own records; offer it, never retype it.

This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is `/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md` decides (`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor). Codex and Antigravity have no slash command surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask the person to choose a product, an autonomy code, a plan format, a run id or a test framework.

The engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is `brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. Keep PASS, FAIL and NO-DATA distinct.

## 1.1.0 names that route here

- `brotherme-auto-status`: Show where the Full-Auto controller run stands right now, in plain language
- `brotherme-brief`: Ask for the short catch-up on where the work stands, what it cost, and what is waiting on you
- `brotherme-status`: Show where the project stands right now, in plain language
- `brotherme-view`: Write the page that shows where this project stands, and offer it to the user
- `brothermode-auto-status`: Show where the Full-Auto controller run stands right now, in plain language
- `brothermode-brief`: Ask for the short catch-up on where the work stands, what it cost, and what is waiting on you
- `brothermode-status`: Show where the project stands right now, in plain language
- `brothermode-view`: Write the page that shows where this project stands, and offer it to the user
- `brothersbe-status`: Use when someone wants to know where their work stands.

A request opening with one of them gets one line, `<old name> is now /brother status` (with the words the table gives it), then this verb with the rest of the words. The whole table: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md`.
<!-- generated-codex-surface: v1 -->
