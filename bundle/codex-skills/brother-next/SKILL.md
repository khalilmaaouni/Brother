---
name: brother-next
description: "Use when someone asks what to do next, which decision is waiting on them, or which packet to pick up. Returns exactly one recommended action with a one sentence reason, never a menu. Invoke as /brother next, or by this skill's name where there is no slash command."
---

# brother next

The one recommended action. Read the state the receipt and the repository show, then name one action and why it is first; a decision waiting on the person is shown highest stakes first with a recommended option. Never a list of options with no ranking. Inside Cursor, the next action may be the next harness packet: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/cursor-execute.md` carries those instructions.

This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is `/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md` decides (`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor). Codex and Antigravity have no slash command surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask the person to choose a product, an autonomy code, a plan format, a run id or a test framework.

The engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is `brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. Keep PASS, FAIL and NO-DATA distinct.

## 1.1.0 names that route here

- `brotherme-decisions`: Show the decisions waiting on you, highest stakes first, each with a recommended option
- `brotherme-next`: Recommend the single best next step for the project
- `brothermode-cursor-execute`: Inside Cursor, claim and execute the next BrotherMode harness packet, then return results
- `brothermode-decisions`: Show the decisions waiting on you, highest stakes first, each with a recommended option
- `brothermode-next`: Recommend the single best next step for the project
- `brothersbe-next`: Use when someone asks what to do next in their work.

A request opening with one of them gets one line, `<old name> is now /brother next` (with the words the table gives it), then this verb with the rest of the words. The whole table: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md`.
<!-- generated-codex-surface: v1 -->
