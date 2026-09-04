---
name: using-brother
description: "Use whenever someone starts real work another person will later have to trust: adding or changing a database column or table without breaking a report or export, reviewing a migration or pull request before merging, explaining why a number or weekly report looks wrong, pulling a list of customers or records that feeds a decision, or touching money, customer data, logins, or a live production path. Reads what the work is and applies the right amount of checking, never a menu. Routes only: it owns no verdicts, no task registry and no release decision. Invoke as /brother:using-brother."
---

# Using Brother

Brother decides how much trust machinery a piece of work needs, then routes to
the capability that supplies it. It answers three questions and then gets out
of the way.

## Bare `/brother`: the authoritative decision order

The one order for bare `/brother` and for "continue" or "resume" alone.
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

## One grammar, six verbs

| Verb | BrotherMode | BrotherSBE |
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

## The one thing worth remembering

A green verdict is not the end of the chain. The chain ends in observed
reality: a change that shipped, a person who accepted it, and where a number
was claimed, an outcome that scored it. Everything above is machinery for
getting there honestly.
