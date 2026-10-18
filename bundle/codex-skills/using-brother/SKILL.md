---
name: using-brother
description: "Use whenever someone starts real work another person will later have to trust: adding or changing a database column or table without breaking a report or export, reviewing a migration or pull request before merging, explaining why a number or weekly report looks wrong, pulling a list of customers or records that feeds a decision, or touching money, customer data, logins, or a live production path, or building or checking an iPhone, iPad, SwiftUI or simulator change. Reads what the work is and applies the right amount of checking, never a menu. Also the route for any 1.1.0 skill name (brotherme-, brothermode-, brothersbe-). Routes only: it owns no verdicts, no task registry and no release decision. Invoke as /brother:using-brother."
---

# Using Brother

Brother decides how much trust machinery a piece of work needs, then routes to
the capability that supplies it. It answers three questions and then gets out
of the way.

## Bare `/brother`: the authoritative decision order

The one order for bare `/brother`, for a bare request naming Brother under a
client with no slash commands, and for "continue" or "resume" alone.
Other files carry its intent; a session finding a competing version follows
this one and fixes the other. Evaluate in order, stop at the first match:

1. **Bare, and unfinished work exists here.** Discovery decides, never a
   guess: `python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" --continue
   --cwd <repo>` (`$CLAUDE_PLUGIN_ROOT` under Claude Code, `$PLUGIN_ROOT`
   under Cursor). One unfinished
   outcome: offer or resume it by its
   plain-language name. Several: number them and ask which. In every
   case: never a run id, never a run directory, before the person.
2. **Bare, nothing unfinished.** Ask one question: "What are you trying to
   do?" No menu of products.
3. **Explicit words.** "continue"/"resume": row 1's path. An implementation
   outcome: the execution spine, never accidentally resuming old work. An
   assurance ask (verify, review, migration, a number): the review verb in
   assurance mode. "Show me what happened": the status verb, which reads the
   run's receipt; no second run database.
4. **A 1.1.0 skill name opens the request** (`brotherme-`, `brothermode-` or
   `brothersbe-` and a verb): look it up in references/retired-names.md, say
   one line, `<old name> is now the brother-<verb> skill (in Claude Code:
   /brother <verb>)`, then route as that verb with the words the table gives
   it and the rest of the request. A name the table does not list is
   unknown: say so and ask row 2's question, never guess a verb.

Whatever the row, a normal task is asked at most one genuinely blocking
question, and never to choose a product name, an autonomy code, a plan
format, a run id or a test framework.

## Any task that names Brother: run the engine, then read the receipt

This section is written for you, the assistant, never for the person. The
person gives one plain sentence; you prepare and validate everything below
yourself. Never ask the person for plan.json, contract.json, a run id, an
autonomy code or MODEL_WORKER_CMD; never ask them to write JSON, and never
ask them to choose a worker. Ask at most one genuine product or permission
blocker; otherwise carry the work through to the receipt.

Codex has no slash commands, so under Codex a task that names Brother is done
by running the engine, never by hand-editing files and printing no receipt.
Write NO intake into the target repository first (no STATE.md, no `.sbe/`,
no fence file): any such file dirties the tree and the engine refuses the run
before the first claim. Full detail: the Codex smoke runbook in the Brother repository.

1. **Each unit's done check must fail BEFORE any work happens.**
   Confirm it exits nonzero. An already-passing check proves no change.
   Never write a bare-path check: a done check is judged on its RESULT,
   never on a missing file, because a check for a file that does not exist
   yet fails by way of "No such file or directory" and leaves no planner to
   hand back a replacement. Use `python3 -m unittest` plus one import-based
   line.
2. **Write the units yourself, then run the engine.** A nested `codex exec`
   cannot start inside a Codex turn, so no model call can be assumed
   available inside a session. Supply a JSON list with `id`, `objective`,
   `done_check`, `writes`, `deps`. `writes` must name EVERY file the unit
   touches, or the file changed outside it fails the scope audit and the
   unit reads QUARANTINE, never integrated. MODEL_WORKER_CMD is a script
   that edits only those files, and it must exit 0 when it is done, or the
   worker returns before committing and the edit is lost. The plan, the
   contract and the worker script are saved by you in the `contracts`
   folder of the host's Brother state folder, never in the target
   repository or any temp directory. Use absolute paths and pass them to
   the engine. Under Codex the folder is
   `"${CODEX_HOME:-$HOME/.codex}/brother/contracts/"`; under Claude Code
   it is `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/brother/contracts/"`.
   The plan, the contract and the runs root stay OUTSIDE the target
   repository (inside dirties the tree, and a read-only install cannot
   write it) and outside any temp directory (a receipt under $TMPDIR is
   gone at the next reboot). The five rules a plan must satisfy:
   references/router-details.md. In a coding session a plan without its
   outcome contract is refused (exit 2): write the contract first
   (references/intake.md) and check it with
   `python3 "$BROTHER_PLUGIN_ROOT/runtime/contract_check.py" <the contract>`
   until it exits 0. Use absolute paths for the plan, contract and worker
   script, as in this example:

       MODEL_WORKER_CMD="python3 <absolute path of the worker script>" \
           python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" \
           --cwd <repo> --plan <absolute path of the plan> \
           --contract <absolute path of the contract> \
           --runs-root "${CODEX_HOME:-$HOME/.codex}/brother/runs"

   (`--runs-root "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/brother/runs"` under
   Claude Code.) Never say a second model is working, and never print a
   receipt unless the engine's own output shows it. If the engine cannot
   run a worker, tell the person one reason and the supported next step:
   run the engine from a terminal outside the Codex session, where it
   spawns the host's own worker (references/router-details.md); never write
   a receipt yourself.
3. **Print the receipt line, then read the receipt back.** The engine's last
   line is `brother_run: receipt: <path>`; open it and report every entry:
   the file, the check, the exit code that decided it.
4. **Never claim done without the receipt.** A turn's exit code proves
   nothing: a write outside a granted sandbox root is dropped silently at
   exit 0. No receipt, or a refused entry, is a NOT DONE report. A NO-DATA
   unit (its check already passed before the work began) means the agent's
   own check or script was wrong: this is NOT a forcing condition, rewrite
   the check and rerun in the same turn, without asking anyone.

A git worktree's `.git` write grant is what `git rev-parse
--git-common-dir` prints, never `<repo>/.git`.

## First, do nothing

Most work needs no trust machinery. Trivial, reversible, nobody would ask
for evidence afterwards: stay quiet and do the work.

## The three routes

Route on what the work IS, not what was asked for. `/brother` is the one door,
and six verbs stand behind it, each a skill named `brother-<verb>`: start,
status, next, review, deliver, help. The two routes below are modes those
verbs run in, read from the work, never a choice put to the person.

**Execution provenance, BrotherMode.** A substantial change someone must
later trust: several files, several sessions, anything a person will be asked
to accept.

**Change assurance, BrotherSBE.** The work touches risk: money, partner
contracts, personal data, auth, a migration, a production path, or a figure
reaching a decision. Absent evidence is NO-DATA, never a pass.

## Native mobile and creative work

For an iPhone, iPad, SwiftUI or simulator outcome, such as "check my change
on iPhone 16", load references/native-mobile.md. Its first section is the run:
doctor, profile-init, reference-init, run, ending in one receipt that names
the simulator runtime and the screenshot hash. It supplies reference, research, design, motion, build and handoff steps
within the existing execution route and product verdicts.

## Cursor-native agents

For the three shipped Cursor agent personas (`brother-planner`,
`brother-executor`, `brother-reviewer`), what each is allowed to do, and
how Cursor's own Plan/Agent/Ask modes map onto them, load
references/cursor-native.md. The harness packet instructions that shipped in
1.1.0 as the cursor-dispatch and cursor-execute skills are
references/cursor-dispatch.md and references/cursor-execute.md.


## The loop

Maintainer tooling, not a supported public feature of 1.1.0, and no endurance claim is made for it: unattended delivery, from a plan of sub units to landed receipts, is the loop: a driver that runs passes until a deadline
or a budget, lanes that brief a worker, grade the build in a sandbox, run adversary probes, repair, and land through the
gates, a watch that names what needs a person, and a recorder that leaves every decision and outcome in a journal. The
tools ship under `${CLAUDE_PLUGIN_ROOT}/runtime/loop/` (a Codex install reads `${BROTHER_PLUGIN_ROOT}/runtime/loop/`;
on a clone install they are `scripts/loop/` at the checkout root). They are executed from a stamped copy the installer
makes, never from the bundle in place, so a running loop keeps its own tools while a newer stamp is proven.

You do not type the loop's commands. Say what you want in plain words, and Brother runs one launcher,
`runtime/loop/start_run.sh`. On a clone install the same files sit in `scripts/loop/` at the checkout root, and the
launcher is run from the launch worktree. It checks, in order, that the tree is clean and equal to its upstream, that
both program pins are executable files, that no hold or pause is in force, that no other loop is alive, and that the
intake says READY. It then starts the driver and waits for its first line.

| You say | Brother runs |
| --- | --- |
| "start a run until 22:00 with 40 dollars", "run the loop overnight" | `runtime/loop/start_run.sh --deadline HH:MM --budget-usd N`, with `--plan <file under docs/plan>` when the person names a plan |
| "loop status", "is the loop healthy" | `runtime/loop/loop_watch.py --log <the newest driver log>`, then `runtime/loop/loop_intake.py status --running` |
| "why is nothing landing" | The class printed by `runtime/loop/loop_watch.py`, given to `runtime/loop/watch_act.py <class> <reason>`; name the one action it returns |
| "what did the loop land" | `runtime/loop/loop_report.py --run <the driver log>` |
| "the morning report", "deliver the loop report" | `runtime/loop/loop_report.py --since-hours N`, printed in the report shape, then the handover pack |
| "stop the loop" | `runtime/loop/stop_loop.sh --reason "<the person's words>"`, beside the controller stop under `/brother start stop` |

The launcher, from the launch worktree:

```bash
bash scripts/loop/start_run.sh --deadline 22:00 --budget-usd 40
```

A deadline and a budget are required. If the person names no deadline or no budget, ask that one question and start
nothing. If the person says "Claude only", add `--worker opus55 --adversary opus55 --documenter sonnet55`: those are
the Claude seats in the model registry, and the launcher's own defaults for the finisher (`opus55`) and the checker
(`sonnet`) are Claude already.

Say every line the launcher prints as it printed it. A refusal reads `START REFUSED [step]: fix`, where the step is
args, tree, pins, controls, live, intake, driver, launch or signal: say that line with the fix it carries, start
nothing, and do not try another route to the same start. When the fix names a lapsed Claude sign-in, say to sign in
once in a terminal by running `claude`, then ask to start again. When it names Brother's saved loop login
(brother-login), say to run `runtime/loop/brother_login.py` once in a terminal, then ask to start again. A hold or
pause is lifted only with `--lift-hold` and the person's own words. Without them the launcher refuses, and Brother
never writes those words for the person.

**What the launcher does**

Behind the launcher are the steps that used to be typed by hand. The intake (`runtime/loop/loop_intake.py prepare`)
records one budget for one run and refuses READY until the canary (`runtime/loop/loop_canary.py`, which replays landed
builds through every judging stage) is green. The driver (`runtime/loop/loop_until.sh HH:MM`) runs passes until the
deadline and prints PULSE and BUDGET lines. The report (`runtime/loop/loop_report.py`) prints what landed, what it
cost, and the yield of every stage, each figure naming its file. These are the explanation of the launcher, not
instructions to the person.

Every number the loop reports is read from a ledger or a log. A run that ends with nothing landed says so, with the refusal classes that stopped it; it never rounds silence up to progress.

## The cut: the owner approves from the app

Maintainer tooling, in a checkout of the Brother repository. A real cut waits
for the owner, and the session never gives its answer. When the cut is ready
to ask, run it once without `--approval`: with no terminal it pushes nothing
and prints one `APP QUESTION` line naming the version and the full sha (all
40 characters of the commit the cut will push). Relay that question word for
word to the owner, in the Claude app or a browser; he answers with the six
digit code from his phone authenticator. Run the verifier with his code at
once, then pass the one line it prints, unchanged, to the cut (or to the cut
driver, which passes it on) within 5 minutes:

```bash
brother-approve verify 1.2.3 <full sha> <his six digit code>
python3 scripts/cut.py --version 1.2.3 --resume <full sha> --release-notes-file <body> --approval "<the printed line>"
```

Never compute, guess, store or reuse a code or a line. A line is used once;
any refusal (the verifier, the sha, the version, the key id or the verifier
build pinned on hub/main, a line older than 5 minutes, an unenrolled key)
means nothing was pushed: say the refusal line as printed and ask the owner
for a new code. Enrollment is the owner's own hand, once, in Terminal with
Touch ID, from the CY1 enrollment page of the Brother repository. The
terminal `y` still works for an owner at his own terminal.

## More detail: verbs, boundaries, handback, closing

Read when the verb table, a boundary this router must never cross, the
handback rule, or the closing ceremony is needed: references/router-details.md
