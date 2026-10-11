---
name: brother-deliver
description: "Use when work is finished and must close with evidence in hand: a delivery summary, a handover to another person, taking a decision back, or turning a lesson into a rule. Reads the receipt and refuses to call anything done without it. Invoke as /brother deliver, or by this skill's name where there is no slash command."
---

# brother deliver

Close with evidence in hand. Package the finished work into one delivery summary built from the receipt: every changed file, every check with its command and exit code. A handover names the receiver and what they accept or reject; a handback returns a decision and its work to the person with nothing lost; a lesson becomes a shared rule with its symptom written as what a reader would observe. The handover pages another person takes a project over from are written by `python3 "${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_handover.py"` (`${CLAUDE_PLUGIN_ROOT}` under Claude Code) from the records, never by hand. No receipt, or a refused entry, is a NOT DONE report.

This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is `/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md` decides (`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor). Codex and Antigravity have no slash command surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask the person to choose a product, an autonomy code, a plan format, a run id or a test framework.

The engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is `brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. Keep PASS, FAIL and NO-DATA distinct.

## 1.1.0 names that route here

- `brotherme-deliver`: Package the finished work into one delivery summary with the evidence that it works
- `brotherme-handback`: Take a decision and the work under it back into your own hands, with nothing lost
- `brotherme-handover-pack`: Generate the handover pages that let another person take this project over
- `brothermode-deliver`: Package the finished work into one delivery summary with the evidence that it works
- `brothermode-handback`: Take a decision and the work under it back into your own hands, with nothing lost
- `brothermode-handover-pack`: Generate the handover pages that let another person take this project over
- `brothersbe-handover`: Use when someone wants to hand this change's ownership to another named human, or when a receiver wants to inspect and accept or reject a...
- `brothersbe-learn`: Use when a lesson from an incident, a repeated correction, a review finding or a measured outcome should become a shared rule, or when a...

A request opening with one of them gets one line, `<old name> is now /brother deliver` (with the words the table gives it), then this verb with the rest of the words. The whole table: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md`.
<!-- generated-codex-surface: v1 -->
