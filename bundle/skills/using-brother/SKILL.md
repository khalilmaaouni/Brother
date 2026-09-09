---
name: using-brother
description: "Use whenever someone starts real work another person will later have to trust: adding or changing a database column or table without breaking a report or export, reviewing a migration or pull request before merging, explaining why a number or weekly report looks wrong, pulling a list of customers or records that feeds a decision, or touching money, customer data, logins, or a live production path. Reads what the work is and applies the right amount of checking, never a menu. Routes only: it owns no verdicts, no task registry and no release decision. Invoke as /brother:using-brother."
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
   --cwd <repo>` (`$CLAUDE_PLUGIN_ROOT` under Claude Code). One unfinished
   outcome: offer or resume it by its
   plain-language name. Several: number them and ask which. In every
   case: never a run id, never a run directory, before the person.
2. **Bare, nothing unfinished.** Ask one question: "What are you trying to
   do?" No menu of products.
3. **Explicit words.** "continue"/"resume": row 1's path. An implementation
   outcome: the execution spine, never accidentally resuming old work. An
   assurance ask (verify, review, migration, a number): BrotherSBE.

## Any task that names Brother: run the engine, then read the receipt

Codex has no slash commands, so under Codex a task that names Brother is done
by running the engine, never by hand-editing files and printing no receipt.
Write NO intake into the target repository first (no STATE.md, no `.sbe/`,
no fence file): any such file dirties the tree and the engine refuses the run
before the first claim. The complete portable procedure is the rest of this
installed skill and its `references/` directory.

1. **Make each unit's done check fail right now, before any work happens.**
   Run it yourself and confirm it exits nonzero; a check that already exits
   0 must fail BEFORE any work happens, or the engine marks the unit NO-DATA
   while a worker that changed nothing still reads integrated.
   Never write a bare-path check: a done check is judged on its RESULT,
   never on a missing file, because a check for a file that does not exist
   yet fails by way of "No such file or directory" and leaves no planner to
   hand back a replacement. Use `python3 -m unittest` plus one import-based
   line.
2. **Write the units yourself, then run the engine.** No model call can be
   made from inside a session or a Codex turn (a nested `codex exec` cannot
   start, and any other model CLI has every socket blocked), so the plan is
   yours: a JSON list, each unit with `id`, `objective`, `done_check`,
   `writes`, `deps`. `writes` must name EVERY file the unit touches, or the
   file changed outside it fails the scope audit and the unit reads
   QUARANTINE, never integrated. MODEL_WORKER_CMD is a script that edits
   only those files, and it must exit 0 when it is done, or the worker
   returns before committing and the edit is lost:

       MODEL_WORKER_CMD="python3 write_the_change.py" \
           python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" \
           --cwd <repo> --plan plan.json \
           --runs-root "${CODEX_HOME:-$HOME/.codex}/brother/runs"

   (`--runs-root "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/brother/runs"` under
   Claude Code.) Both stay OUTSIDE the target repository (inside dirties the
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

Route on what the work IS, not what was asked for. `/brother` is the one door.

**Execution provenance, BrotherMode.** A substantial change someone must
later trust: several files, several sessions, anything a person will be asked
to accept.

**Change assurance, BrotherSBE.** The work touches risk: money, partner
contracts, personal data, auth, a migration, a production path, or a figure
reaching a decision. Absent evidence is NO-DATA, never a pass.

**Claim verification, BrotherDS.** A decision-grade number is about to be
stated; the claim registers BEFORE the outcome is known, then scores against
reality. Experimental, not in the bundle.

## More detail: verbs, boundaries, handback, closing

Read when the verb table, a boundary this router must never cross, the
handback rule, or the closing ceremony is needed: references/router-details.md
