---
name: using-brother
description: "Use whenever someone starts real work another person will later have to trust: adding or changing a database column or table without breaking a report or export, reviewing a migration or pull request before merging, explaining why a number or weekly report looks wrong, pulling a list of customers or records that feeds a decision, or touching money, customer data, logins, or a live production path. Reads what the work is and applies the right amount of checking, never a menu. Also the route for any 1.1.0 skill name (brotherme-, brothermode-, brothersbe-). Routes only: it owns no verdicts, no task registry and no release decision. Invoke as /brother:using-brother."
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
2. **Write the units yourself, then run the engine.** No model call can be
   assumed available inside a session. Supply a JSON list with `id`,
   `objective`, `done_check`,
   `writes`, `deps`. `writes` must name EVERY file the unit touches, or the
   file changed outside it fails the scope audit and the unit reads
   QUARANTINE, never integrated. MODEL_WORKER_CMD is a script that edits
   only those files, and it must exit 0 when it is done, or the worker
   returns before committing and the edit is lost:

       MODEL_WORKER_CMD="python3 write_the_change.py" \
           python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" \
           --cwd <repo> --plan plan.json --contract contract.json \
           --runs-root "${CODEX_HOME:-$HOME/.codex}/brother/runs"

   (`--runs-root "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/brother/runs"` under
   Claude Code.) In a coding session a plan without its outcome contract is
   refused (exit 2): write `contract.json` first (references/intake.md) and
   check it with
   `python3 "$BROTHER_PLUGIN_ROOT/runtime/contract_check.py" contract.json`.
   The plan, the contract and the runs root stay OUTSIDE the
   target repository (inside dirties the
   tree, and a read-only install cannot write it) and outside any temp
   directory (a receipt under $TMPDIR is gone at the next reboot). The five
   rules a plan must satisfy: references/router-details.md.
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

For an iPhone, iPad or SwiftUI outcome, load references/native-mobile.md.
It supplies reference, research, design, motion, build and handoff steps
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

Routing phrases: start a run until HH:MM, stop the loop, loop status, what did the loop land, why is nothing landing,
resume the loop. The order of the verbs, and the one that runs before any of them:

1. The canary. `python3 runtime/loop/loop_canary.py` from the checkout root replays landed builds through every judging
   stage (grade, probes, spec gate, land apply, status word, checker hold) and must print WOULD LAND. A run may not start
   on a canary that refuses; an unmeasured stage is named, never counted as a pass.
2. The intake. `python3 runtime/loop/loop_intake.py prepare --deadline HH:MM --budget-usd N --words "<the owner's words>"`
   records one budget for one run and refuses READY until the canary is green.
3. The driver. `bash runtime/loop/loop_until.sh HH:MM` runs passes until the deadline; every pass prints PULSE and BUDGET
   lines; `runtime/loop/loop_watch.py --log <the driver's log>` names the class that needs a hand; `runtime/loop/stop_loop.sh`
   stops everything of the loop and nothing else.
4. The report. `runtime/loop/loop_report.py --since-hours N` prints what landed, what it cost in both ledgers and the yield
   of every stage, each figure naming its file. The journal of a run lives in the run directory the driver announces.

Every number the loop reports is read from a ledger or a log. A run that ends with nothing landed says so, with the
refusal classes that stopped it; it never rounds silence up to progress.

## More detail: verbs, boundaries, handback, closing

Read when the verb table, a boundary this router must never cross, the
handback rule, or the closing ceremony is needed: references/router-details.md
