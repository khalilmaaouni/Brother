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
by running the engine. Four steps, in this order. Writing the patch by hand
instead is the one failure this section exists to stop: a turn that edits
files and prints no receipt has not used Brother at all.

These four steps are the WHOLE ceremony. Write NO intake into the target
repository first: no STATE.md, no `.sbe/`, no fence file, nothing a unit did
not declare. Every such file makes the tree dirty, and the engine refuses the
run before the first unit is claimed.

1. **Make each unit's done check fail right now.** A done check is a
   command a machine runs that FAILS when the change is wrong, and it
   must fail BEFORE any work happens: run it yourself, in the repository,
   before the engine ever sees it, and confirm it exits with a code other
   than 0. A check that already exits 0 on the untouched tree cannot
   prove the work: the engine runs it, marks the unit NO-DATA, and a
   worker that changed nothing still reads integrated. For a behaviour
   change the check names the new behaviour. On the runbook's toy the
   starting shape is:

       python3 -c "import mathlib, unittest; t = unittest.TestCase(); t.assertRaises(TypeError, mathlib.add, 'a', 'b')" && python3 -m unittest

   Adjust only if that exact line does not behave: red on the untouched toy,
   green once the guard is written. Never write a bare-path check either: a
   done check must be runnable on the untouched tree and judged on its
   RESULT, never on a missing file. `python3 test_add_rejects.py` for a file
   that does not exist yet just prints "No such file or directory", which
   the engine treats as a broken check and refuses before any worker starts,
   and under `cat plan.json` there is no planner left to hand back a
   replacement check, so the unit is pulled out instead. Use
   `python3 -m unittest` plus the import based one-liner above instead.
2. **Set both seams from the start, then run the engine.** Under Codex no
   model call can be made from inside the turn at all: a nested `codex exec`
   cannot start ("failed to initialize in-process app-server client"), and
   every socket the sandbox lets a command open still leads nowhere for
   reaching one. So DOOR_MODEL_CMD (which units to write) and MODEL_WORKER_CMD
   (how to make the change) are BOTH the agent's from the first attempt,
   never something reached for only after a door refusal. DOOR_MODEL_CMD
   prints the plan; MODEL_WORKER_CMD is a script that edits only the files
   the unit declares in `writes`, in the current directory, and nothing
   wider. The engine runs that script inside the unit's own isolated
   worktree, passes the unit brief as its last argument (ignore it, the
   script already knows what to write), then commits whatever changed, runs
   the done check there, audits scope, and writes the receipt. The worker
   script must exit 0 once it is done editing: a non-zero exit, whether a
   stray traceback or a `set -e` trip firing after the writes already
   landed, makes `model_worker.py`'s `main()` return 3 before
   `collect_artifacts` or `commit_changes` ever run, so the edit is lost
   before anything is committed and the unit's receipt reads NO-DATA.

       DOOR_MODEL_CMD="cat plan.json" MODEL_WORKER_CMD="python3 write_the_change.py" \
           python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" \
           --cwd <repo> --runs-root "$TMPDIR/brother-runs"

   Under Claude Code the plugin root is `$CLAUDE_PLUGIN_ROOT`. `plan.json` is
   a JSON list of units, each with `id`, `objective`, `done_check`, `writes`
   and `deps`; the engine still isolates every unit, still runs every
   `done_check`, and still writes the receipt, only the decomposition came
   from you. `writes` must name EVERY file the unit will change or create,
   both `mathlib.py` and `test_mathlib.py` for the toy task; a file changed
   outside `writes` fails the scope audit and the whole unit reads
   QUARANTINE, never integrated. `--runs-root` holds the engine's own
   records: keep it OUTSIDE the repository, because inside, the records make
   the tree dirty, and the default under a read-only plugin install cannot
   be written at all.

   Running the engine with either seam unset costs three refused door
   attempts and, worse than a refusal, a worker that changes nothing in
   about a second: the default worker argv is that same unreachable nested
   `codex exec`, so it exits having touched no file, and a unit whose check
   was already green (the failure step 1 exists to rule out) then reads
   integrated with nothing done.
3. **Print the receipt line, then read the receipt back.** The engine's last
   line is `brother_run: receipt: <path>`. Print that line, open the file it
   names, and report every per-file entry: the file, the check command, and
   the exit code that decided it.
4. **Never claim done without the receipt.** A turn's exit code proves
   nothing about writes: a write outside a granted sandbox root is dropped
   silently at exit 0. No receipt, or a receipt whose entries are refused, is
   a NOT DONE report naming what refused, never a done. A receipt whose unit
   reads NO-DATA because the check already passed before the work began, or
   whose files changed reads none, means the agent's own check or script was
   wrong. It is NOT a stated-versus-observed contradiction and NOT a
   forcing condition under any injected law (BrotherSBE's own hooks inject
   rule L6 into Codex, which pauses on exactly that shape): rewrite the check
   so it fails before the work, fix the script, and rerun the engine in the
   same turn, without asking anyone. The turn ends with a receipt whose entries
   carry exit codes and changed files, or with NOT DONE naming what refused,
   never with a question and never with a request for permission to retry.

Codex defaults to the read only sandbox, which refuses every write, and plain
`workspace-write` still refuses the `.git` write unit isolation needs, so the
turn needs `-s workspace-write` and a writable roots grant on the
repository's git directory. In a git worktree that directory is NOT
`<repo>/.git`, which is a file there: it is what `git rev-parse
--git-common-dir` prints.

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
