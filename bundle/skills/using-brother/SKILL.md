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
no fence file): any such file makes the tree dirty and the engine refuses the
run before the first unit is claimed. Full detail: docs/codex/SMOKE-RUNBOOK.md.

1. **Make each unit's done check fail right now, before any work happens.**
   Run it yourself in the repository and confirm it exits nonzero; a check
   that already exits 0 must fail BEFORE any work happens, or the engine
   marks the unit NO-DATA while a worker that changed nothing still reads
   integrated. Never write a bare-path check: a done check is judged on its
   RESULT, never on a missing file, because a check for a file that does not
   exist yet just fails by way of "No such file or directory" and leaves no
   planner to hand back a replacement. Use `python3 -m unittest` plus an
   import-based one-liner instead.
2. **Set both seams from the start, then run the engine.** No model call can
   be made from inside a Codex turn (a nested `codex exec` cannot start), so
   DOOR_MODEL_CMD (which units to write) and MODEL_WORKER_CMD (a script
   that edits only the files a unit's `writes` names) are both set from the
   first attempt:

       DOOR_MODEL_CMD="cat plan.json" MODEL_WORKER_CMD="python3 write_the_change.py" \
           python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" \
           --cwd <repo> --runs-root "$TMPDIR/brother-runs"

   `plan.json` is a list of units, each with `id`, `objective`, `done_check`,
   `writes`, `deps`. `writes` must name EVERY file the unit touches: a file
   changed outside it fails the scope audit and the whole unit reads
   QUARANTINE, never integrated. The worker script must exit 0 once it is
   done editing, or `model_worker.py` returns before committing and the edit
   is lost before anything is recorded. `--runs-root` stays OUTSIDE the
   repository: inside, it dirties the tree, and a read-only plugin install
   cannot write it at all.
3. **Print the receipt line, then read the receipt back.** The engine's last
   line is `brother_run: receipt: <path>`; open that file and report every
   per-file entry: the file, the check command, the exit code that decided it.
4. **Never claim done without the receipt.** A turn's exit code proves
   nothing: a write outside a granted sandbox root is dropped silently at
   exit 0. No receipt, or a refused entry, is a NOT DONE report. A NO-DATA
   unit (its check already passed before the work began) means the agent's
   own check or script was wrong: this is NOT a forcing condition, rewrite
   the check and rerun the engine in the same turn, without asking anyone.

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

The verb-to-slash-command table, what this router must never do, the
handback rule, and the four-step closing ceremony are in
references/router-details.md, next to this file. Load it when one of
those situations applies.
