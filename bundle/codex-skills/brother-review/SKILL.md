---
name: brother-review
description: "Use when someone asks to review, verify, check or prove a change before it ships or before it is called done: a diff, a pull request, a migration, a number, a design, a contract, a production path. Judges the work against its bar and reports PASS, FAIL or NO-DATA per check with the evidence read. Invoke as /brother review, or by this skill's name where there is no slash command."
---

# brother review

Judge the work against its bar. Ceremony scales with risk, read from the work: a typo fix gets the definition of done and nothing else; a migration, a money or partner path, personal data, auth, a production path or a figure about to be claimed true runs the five assurance gates (numbers, migration, approval, ran, proof), each answering PASS, FAIL or NO-DATA on its own evidence. A gate with nothing to read is NO-DATA and never a pass. Report what passes, what does not, and the one next action. A design review judges the shape of a system before it is built: what it is for, how the process runs, the architecture and the data model, each against the law for that phase and nothing else.

This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is `/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md` decides (`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor). Codex and Antigravity have no slash command surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask the person to choose a product, an autonomy code, a plan format, a run id or a test framework.

The engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is `brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. Keep PASS, FAIL and NO-DATA distinct.

## 1.1.0 names that route here

- `brotherme-review`: Check the current work against the definition of done and report what passes and what does not.
- `brothermode-review`: Check the current work against the definition of done and report what passes and what does not.
- `brothersbe-design`: Use when someone is deciding or reviewing the shape of a system before it gets built: what it is for, how the process runs, what the arch...
- `brothersbe-prove-this-change`: Use when someone is changing an API response or request shape, an event or message contract, a partner or third party integration, a retr...
- `brothersbe-review`: Use when reviewing a diff, a pull request, or a colleague's change against the design it claims to implement.
- `brothersbe-verify`: Use when work is about to be called done, a figure that could reach a decision has been produced, a schema migration is part of the chang...

A request opening with one of them gets one line, `<old name> is now /brother review` (with the words the table gives it), then this verb with the rest of the words. The whole table: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md`.
<!-- generated-codex-surface: v1 -->
