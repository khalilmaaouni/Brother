# Router details: verbs, boundaries, handback, closing

LOAD WHEN: mapping a verb to a slash command, checking what the router must
never do, handing work back instead of pushing main, or closing a session.
(Extracted from SKILL.md; see SKILL.md for the routing decision itself.)

## One grammar, six verbs

Slash commands are Claude Code's surface. Codex has none, so under Codex
every row below is reached by running the engine as above, and typing one of
these strings runs nothing at all.

THE TABLE ITSELF IS NOT HERE, and that is the fix rather than an omission.
The door (`commands/brother.md`) carries one row per verb either product
ships, GENERATED from the installed skills by `scripts/gen_door_table.py`
and never typed by hand. A second table typed here drifted from it, which is
exactly the failure a generated table exists to prevent, so read the door's
own table and trust nothing else. The long `brotherme-` names are the landed
forms; never promise a short form that does not exist.

## What this router must never do

It holds no state. Specifically:

- no second task registry, and no second idea of who owns a file
- no PASS, FAIL or NO-DATA of its own; those belong to the capability that
  gathered the evidence
- no assurance logic and no claim arithmetic, which live in their own products
- no release decision, no tag, no merge; a person decides those, always
- no menu recited at someone who did not ask for one
- no asking the person to choose between BrotherMode, BrotherSBE or BrotherDS;
  the router reads the work and decides, silently, which one applies
- no invoking a capability merely to demonstrate that it exists

## Handback, not a push to main

A sub-session, chip session, or lane finishing work never pushes the default
branch. It commits on its own branch, pushes that branch at most, and reports
back to its dispatcher (or leaves the pull request open unmerged). Merging is
the reviewing session's act after the gates, so every merge carries a review
and an attribution. Where a repository installs Brother's pre-push gate,
`check_handback` refuses the push mechanically and `BROTHER_MAIN_PUSH=allow`
lifts it once, loudly. Where it does not, the rule still holds.

## Closing a session: the handover ceremony

Four steps, in this order. The order is load bearing.

**1. Decide the queue, do not drain it.** Every open pull request gets a
decision. "Parked", with its reason and flip condition written into the pull
request, counts. A row nobody mentioned does not.

**2. Write lessons as data.** Add them to the wisdom lessons file in the
Brother repository; an install ships no copy. Each needs
a `symptom` phrased as what a reader would OBSERVE. "every check is green and
the change still cannot reach a user" is findable; naming the cause is only
findable by someone who already knows.

**3. Commit the notes, then re-index the vault.** The vault is whichever
root this install is configured with: `BM_VAULT_ROOT`, else
`BROTHERMODE_VAULT`, else the vault recorded in Brother's own config. With
none of them set there is no vault, the tools report NO-DATA and index
nothing, and that is the correct answer rather than a failure.

Commit the notes BEFORE indexing. The index reads committed state, so an
uncommitted note is invisible to it and the resulting empty index reads
exactly like the tool having failed.

**4. Hand over one zip** holding every file it refers to.

## Writing the units yourself: what makes a plan schedulable

D-001 (persona dogfood 2026-09-07). Inside a coding session the engine does
not ask a model to decompose the outcome, because the decomposer and the
worker are child processes of the turn and no model call can be made from
inside one: a nested `codex exec` cannot start there at all, and any other
model CLI has every socket blocked and reports itself not logged in. It
hangs or exits with an empty error, three attempts running, and no worktree
is ever made. So the plan comes from the session and enters through
`--plan <file>`, a JSON list of 2 to 9 units:

    [{"id": "U1", "objective": "what is true when this unit is done",
      "done_check": "one shell command that decides it",
      "writes": ["path/one.py"], "deps": []}]

FX-A, THE SAME RULE FOR THE WORKER (2026-09-08). The decomposer was only
half of it: the per-unit worker is a child process too, so inside a session
the engine spawns none. It claims the ready units, opens one worktree for
each, prints them with their objective, their check and their declared
writes, and exits 3, which means the units are claimed and the work is
YOURS. Do each unit in its own worktree, run its `done_check` there, and
leave the work in that tree; committing it is the engine's job, not yours.
Then run the `--continue` line the block printed. That call commits each
lane, audits what changed against what the unit declared, re-runs every
check in the lane, merges what passes and writes the receipt. A batch whose
dependencies have not landed yet is not handed over with the first one: it
becomes yours on the continue after the one that merged what it waits on, so
a graph with edges takes one handover per batch. Nothing about this weakens
the gates: a lane you never wrote in fails its own check and is refused, and
a file written outside the unit's `writes` still reads QUARANTINE.

Outside a session, or with `MODEL_WORKER_CMD` naming a worker, the headless
path is untouched and the worker is spawned exactly as before, the same way
`DOOR_MODEL_CMD` opts back into the headless decomposer.

Five rules the engine enforces. Each refuses the whole plan, with nothing
written, rather than half-running it:

1. **Every unit needs a `done_check`**, or the verifier would close on an
   opinion. Run it yourself first and confirm it exits nonzero: a check that
   already passes marks the unit NO-DATA, and a worker that changed nothing
   still reads integrated.
2. **`writes` must name EVERY file the unit touches** and may not escape the
   repository. A file changed outside it fails the scope audit and the unit
   reads QUARANTINE, never integrated.
3. **`deps` names other unit ids only.** A dangling edge drops a unit from
   the ready set forever, so it is refused at declaration.
4. **Independence is the point**: at most three worktrees run at once, and
   two units that write the same path never run together.
5. **The plan file lives outside the target repository.** Inside, it dirties
   the tree and the engine refuses the run before the first claim.

Outside a session, a bare outcome with no `--plan` still spawns the host's
own headless client to decompose it, and `DOOR_MODEL_CMD` (or door.py's
`--model-cmd`) names a different one. That path is unchanged; it is simply
never the one taken from inside a session.

## The outcome contract, which comes before the plan

A-prime amendment 2 (the 2026-09-08 debate judgment, unit U6). A plan says
what will be built. It does not say what was ASKED, and the scenarios this
retires are the ones where the answer never read the question: an answer in
the wrong language, an outcome nobody asked for, a delivery with no named
proof behind it. So a record comes first, written by the intake
(`bm_project.py adopt`) and validated against
`docs/schema/outcome-contract-v1.json`: the requested `language`, the
`question` itself, the `success_checks` that would prove it done, `ticket`
and `audit` when they are required, `affected_products`, and the
`must_answer` fields the answer owes.

Pass it as `--contract <that record>`. Four rules, each refusing the whole
run with nothing claimed and no run directory opened:

1. **The record is checked before the plan is read.** A record that fails
   the schema refuses at exit 1 carrying the checker's own FAIL lines, in
   `scripts/contract_check.py`'s words, never a paraphrase of them.
2. **A `draft` record refuses**, naming its own open question. A draft means
   a question is still open, and a plan built on an unanswered question
   plans for the wrong outcome. Answer it, move `state` to `contracted`, run
   the command again.
3. **Every `success_checks` command must be run by some unit.** A check
   counts as covered when its command IS a unit's `done_check` or STARTS
   one, so a unit may add arguments or chain a second command after it, and
   may not quietly substitute a different command. Anything uncovered is
   refused by that check's own id: a green run that never ran the promised
   check proves less than the contract claims.
4. **Inside a coding session the `--plan` route carries a contract.** A
   `--plan` run without one refuses at NO-DATA, exit 2, the same shape as a
   missing plan. Outside a session (the documented headless path) it stays
   optional, so this is a routing rule and not the removal of a capability.
   The one in-session way to plan without a contract is `DOOR_MODEL_CMD`,
   the escape hatch a person names deliberately, and that is taken at its
   word here exactly as it is in the section above.

A run that was given one records the contract's path, language and question
in its own target marker, so delivery reads what was asked from the run
itself rather than being told a second time.

## The one thing worth remembering

A green verdict is not the end of the chain. The chain ends in observed
reality: a change that shipped, a person who accepted it, and where a number
was claimed, an outcome that scored it. Everything above is machinery for
getting there honestly.
