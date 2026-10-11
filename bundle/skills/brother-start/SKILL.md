---
name: brother-start
description: "Use when someone wants to begin, resume or stop checked work: a project, an outcome, a change someone else must later trust, or an incident that is broken right now. Writes the outcome contract and runs the first check through the engine; never asks for a product name, an autonomy code, a plan format or a run id. Invoke as /brother start, or by this skill's name where there is no slash command."
---

# brother start

Begin, resume or stop checked work. Bare, with unfinished work in this repository, resume it by its plain language name (the door's Step 1 finds it); with nothing unfinished, take the outcome the person wrote and start. An incident (down, outage, hotfix) starts in assurance mode with the incident named verbatim. Stop means the running controller drains in flight work and releases every held claim; say what was released. The person answers at most one blocking question and never chooses an internal mode: execution provenance for a change someone must later accept, assurance when the work touches money, personal data, auth, a migration, a production path or a figure reaching a decision. `/brother start stop` ENDS the running controller run rather than starting one. A run has one driver, so a stop must speak as that driver: read the driver's session id from `python3 "${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_controller.py" status --project <project id> --json --raw` (the `run.session_id` field; `${CLAUDE_PLUGIN_ROOT}` under Claude Code), then run `python3 "${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_controller.py" stop --project <project id> --controller-id <controller id> --session-id <driver session id> --actor-name <your name>`. Without `--session-id` the command speaks as a fresh session and is refused ("a run has one driver"). If that driver is gone or the takeover is deliberate, first `python3 "${BROTHER_PLUGIN_ROOT}/runtime/hooks/brothermode/tools/bm_controller.py" adopt --project <project id> --session-id <your session id> --actor-name <your name>`, then stop with that same `--session-id`. The stop drains any in flight unit, releases every held claim and reports the state it moved from and to; open work left behind is still owed. A vague ask first becomes a measurable specification with acceptance criteria (the outcome contract), never a build. `/brother start dispatch` writes a packet for Cursor to execute, following `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/cursor-dispatch.md`; it REPLACES the engine run for that work and does not run brother_run.py.

This is one of the six verbs behind the one door. Under Claude Code and Cursor the door is `/brother <what you are trying to do>`, and the verb word is optional: the door's routing order in `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/SKILL.md` decides (`${BROTHER_PLUGIN_ROOT}` under Codex, `${PLUGIN_ROOT}` under Cursor). Codex and Antigravity have no slash command surface: there this skill is reached by its name, or through the `using-brother` skill. Never ask the person to choose a product, an autonomy code, a plan format, a run id or a test framework.

The engine owns execution, assurance and the receipt:

```bash
python3 "${BROTHER_PLUGIN_ROOT}/runtime/brother_run.py" "<outcome>" --cwd <repo>
```

(`${CLAUDE_PLUGIN_ROOT}` under Claude Code, `${PLUGIN_ROOT}` under Cursor.) Its last line is `brother_run: receipt: <path>`; open that receipt and report every entry before claiming anything. Keep PASS, FAIL and NO-DATA distinct.

## 1.1.0 names that route here

- `brotherme-auto`: Begin or resume the Full-Auto controller, which sequences a signed outcome to a checked deliverable
- `brotherme-start`: Start a project with a short guided conversation that ends in one clear project brief
- `brotherme-stop`: Stop the Full-Auto controller run right now, draining in-flight work and releasing every held claim (say `/brother start stop`)
- `brothermode-auto`: Begin or resume the Full-Auto controller, which sequences a signed outcome to a checked deliverable
- `brothermode-cursor-dispatch`: From Claude Code (Fable or Opus), dispatch a work packet for Cursor to execute under the BrotherMode harness (say `/brother start dispatch`)
- `brothermode-start`: Start a project with a short guided conversation that ends in one clear project brief
- `brothermode-stop`: Stop the Full-Auto controller run right now, draining in-flight work and releasing every held claim (say `/brother start stop`)
- `brothersbe-adopt`: Use when someone is adding risk and evidence checks to a repository that has never had them, or is checking whether an existing installat...
- `brothersbe-kickoff`: Use before designing or writing anything, when a backend, infrastructure, or data engineering change has not had its ground mapped yet an...
- `brothersbe-spec-and-data-prep`: Use when a vague stakeholder ask needs to become a measurable specification with acceptance criteria, when requirements are being written...
- `brothersbe-start`: Use as the single entry point when someone wants to begin or resume work and does not know, or does not care, which command comes next.
- `brothersbe-work`: Use when a plan already exists and its tasks are ready to be built, not just recommended or designed.

A request opening with one of them gets one line, `<old name> is now /brother start` (with the words the table gives it), then this verb with the rest of the words. The whole table: `${CLAUDE_PLUGIN_ROOT}/skills/using-brother/references/retired-names.md`.
<!-- generated-codex-surface: v1 -->
