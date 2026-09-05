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

1. **Write the unit or units, each with a real done check.** A done check is
   a command a machine runs that FAILS when the change is wrong. A test you
   added, run with `python3 -m unittest`, counts. "It looks right" does not.
2. **Run the engine, never a slash command:**

       python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" --cwd <repo>

   Under Claude Code the plugin root is `$CLAUDE_PLUGIN_ROOT`. The engine
   needs a configured model worker and refuses the run when none answers.

   If it refuses with `door: refused after N attempt(s), store untouched`,
   hand it the units you wrote in step 1 instead of leaving it to ask a
   model it cannot reach:

       DOOR_MODEL_CMD="cat plan.json" python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" --cwd <repo>

   `plan.json` is a JSON list of units, each with `id`, `objective`,
   `done_check`, `writes` and `deps`. The engine still isolates every unit,
   still runs every `done_check`, and still writes the receipt; only the
   decomposition came from you. This is a documented seam, not a way round
   the engine, and writing the patch by hand is still the failure above.
3. **Print the receipt line, then read the receipt back.** The engine's last
   line is `brother_run: receipt: <path>`. Print that line, open the file it
   names, and report every per-file entry: the file, the check command, and
   the exit code that decided it.
4. **Never claim done without the receipt.** A turn's exit code proves
   nothing about writes: a write outside a granted sandbox root is dropped
   silently at exit 0. No receipt, or a receipt whose entries are refused, is
   a NOT DONE report naming what refused, never a done.

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

The routes compose. A risky change usually wants provenance and assurance
together. Nothing forces all three.

## More detail: verbs, boundaries, handback, closing

The verb-to-slash-command table, what this router must never do, the
handback rule, and the four-step closing ceremony are in
references/router-details.md, next to this file. Load it when one of
those situations applies.

## The one thing worth remembering

A green verdict is not the end of the chain. The chain ends in observed
reality: a change that shipped, a person who accepted it, and where a number
was claimed, an outcome that scored it. Everything above is machinery for
getting there honestly.
