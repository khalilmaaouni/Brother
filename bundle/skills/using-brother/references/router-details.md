# Router details: verbs, boundaries, handback, closing

LOAD WHEN: mapping a verb to a slash command, checking what the router must
never do, handing work back instead of pushing main, or closing a session.
(Extracted from SKILL.md; see SKILL.md for the routing decision itself.)

## One grammar, six verbs

Slash commands are Claude Code's surface. Codex has none, so under Codex
every row below is reached by running the engine as above, and typing one of
these strings runs nothing at all.

| Verb | BrotherMode (Claude Code) | BrotherSBE (Claude Code) |
|---|---|---|
| start | `/brothermode:brotherme-start` | `/brothersbe:start` |
| status | `/brothermode:status` | `/brothersbe:status` |
| next | `/brothermode:next` | `/brothersbe:next` |
| review | `/brothermode:review` | `/brothersbe:review` |
| deliver | `/brothermode:brotherme-deliver` | `/brothersbe:verify` |
| help | `/brothermode:help` | `/brothersbe:help` |

The long `brotherme-` names are the landed forms; never promise a short
form that does not exist.

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

## Why the DOOR_MODEL_CMD seam is the route under Codex, not a fallback

The engine's decomposer and worker are child processes of that turn, and no
model call can be made from inside one: a nested `codex exec` cannot start
there at all, and any other model CLI has every socket blocked and reports
itself not logged in. So under Codex, step 2's `DOOR_MODEL_CMD` seam is the
route, not a fallback.
