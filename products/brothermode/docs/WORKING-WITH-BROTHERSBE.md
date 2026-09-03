Status: CURRENT.

# Using BrotherMode and BrotherSBE together

For a team where several people deliver against one codebase, some on the
backend, some on the front end, some on data. Each person can use BrotherMode
entirely alone and get value from it on day one. This page is about what
changes when more than one of you is using it, and where the sibling product,
BrotherSBE, takes over.

Every command and every block of output below was run on 2026-08-11 against a
throwaway repository built for the purpose. Nothing here is described from
documentation. Where a command failed, the failure is printed, because the
failures are the useful part.

Roles below are roles, not people: a backend engineer, a front end engineer, a
reviewer, a delivery lead. Substitute your own.

## The one sentence

**BrotherMode governs one person's session. BrotherSBE governs one change's
passage between people.** Neither replaces the other, and using both is not
twice the work, because they meet at exactly one object: the change you are
about to hand to somebody else.

## Where you type them, which is the thing that confuses everybody first

This is not a detail. It is the first thing a new team member gets wrong, and
it costs an afternoon.

| | BrotherMode | BrotherSBE |
|---|---|---|
| Where you type it | Inside Claude Code, as `/brothermode` | In your terminal, as `sbe` |
| What it is | A plugin and skill set for your agent session | A command line tool |
| Is there a `brothermode` shell command? | No | Not applicable |
| What it owns | Your session: the plan, the file list, one writer per file, the refusal to call work done | The change: its risk tier, who must review it, what evidence exists |
| What it produces for other people | A self-contained page showing where the work stands | A routing and impact verdict, and the evidence receipts behind it |

Checked on this machine, not assumed:

```text
$ which brothermode sbe
/Users/…/.claude/plugins/cache/brothersbe/brothersbe/1.0.0-rc.1/bin/sbe
$ brothermode version
/bin/bash: brothermode: command not found
```

That is correct behaviour, not a broken install. BrotherMode is driven from
inside your agent session. If somebody on your team reports that BrotherMode
"is not installed" because a shell command is missing, this is what they hit.

## Walk one: a backend engineer delivers a feature

The change: add region filtering to an orders API. Two files move, and one of
them is the contract a front end team reads.

### Step 1, BrotherMode, at the start of the session

You open Claude Code and invoke `/brothermode`. Underneath, the deterministic
boundary is one command line, and running it directly shows what it does. On a
repository that has never held a project it refuses, and names the fix:

```text
bm_project: refused: no store exists at …/.brothermode/store.sqlite3;
run `python3 …/tools/bm_store.py init` to create one
```

After `init`, starting the project prints two lines that matter:

```text
bm_store: initialized …/.brothermode/store.sqlite3 (root resolved via git)
bm_project: scaffolded PROGRESS.html from the template; refresh it at every
closed loop, and a box ticks only on quoted evidence
started project orders-region (0 task(s), no forecast)
```

**What that bought you.** A page exists before any work does. Not a report you
write at the end, a page that is generated from records as the work moves. The
second line is the contract it holds you to: a box ticks only when a check ran
and its output is quoted beside it.

Asking where things stand gives you eight fields in plain language and exactly
one next step:

```text
Goal: Add region filtering to the orders API without breaking the front end
Direction: not agreed yet
Progress: nothing planned yet
Time remaining: not forecast yet
Decision needed: none
Risk: none new
Evidence: no executed evidence recorded yet
Next step: agree what I am allowed to do on your behalf
  why: the outcome is recorded, and nothing can run until you authorise it
```

Note the last line. Nothing runs on your behalf until you say what is allowed.
That is the answer to the question a delivery lead asks first.

### Step 2, BrotherSBE, once the change exists

You commit on a branch as you normally would. Then, in a terminal:

```bash
sbe impact . --base main
```

Real output on this change:

```text
git diff main..HEAD over 2 changed file(s)
  UNMEASURED src/api/contract.py: no detector covers .py files; this tool did
            not read it and is not reporting it as clean
  UNMEASURED src/api/orders.py: no detector covers .py files; this tool did
            not read it and is not reporting it as clean
  UNMEASURED consumers: how many downstream things break if this is wrong
            cannot be read from a diff. Assumed 'none', which can only lower
            the proposal, never raise it.

proposed tier T0 (a floor, not a ceiling), declared tier none read
intake: no intake file at None
verdict: NO-DATA
```

**Read that carefully, because it is the most important output on this page.**
The change bumped an API path from v1 to v2 and added fields a front end
reads. The tool proposed **T0**, the lowest tier. It did not miss the risk and
it did not lie about it: it said three times that it had not measured, and its
verdict is **NO-DATA**, not PASS.

The danger is a person skimming, seeing `T0`, and shipping. The tier is a
floor, never a ceiling, and the tool says so on the same line. On a contract
change, you raise it yourself.

## Walk two: the handoff to a front end team

This is the part that has no tool and needs none, and it is where teams
actually lose changes.

The backend engineer's change is now the front end team's problem, and the
question they need answered is not "is it done", it is **"what changed in the
thing I consume, and what has actually been checked".** Two artifacts answer
that, and both already exist:

1. **The page**, from BrotherMode. Self-contained, opens with a double click,
   needs no account and no sign-in, survives being pasted into whatever chat
   or wiki the receiving team already uses. Verified on a fresh project: the
   scaffolded page carries zero external references, so it opens with nothing
   installed and nothing to log into.

2. **The handover, done as a procedure**, from
   [docs/HANDOVER-BY-HAND.md](HANDOVER-BY-HAND.md). Four sections: what is done
   and the evidence proving it, what is in flight and its exact stopping point,
   what has not been started, and the open questions each with a named owner.
   Then the half that gets skipped: an explicit acknowledgment from the
   receiver. **Until the receiver confirms they have taken it, the sender still
   owns it.** That page works on paper with nothing installed, which is why it
   is the right thing to send a front end team that has never heard of either
   product.

The rule that makes the page worth more than a written update: **nobody edits
it by hand before sending it.** It is generated from records, which means it
will sometimes say something the sender would rather it did not. That is the
entire value. Edit it once and it becomes another status document written by
the person being assessed, and the advantage is gone permanently.

## Walk three: the reviewer

The reviewer is the person whose time is the constraint, so the combination is
aimed at them.

- From BrotherSBE they get the routing verdict: what kind of change this is,
  and therefore which specialist should look at it. On the change above, they
  also get the honest gap, that consumer count was not measured.
- From BrotherMode they get the evidence state: which checks ran after the
  last edit, quoted, and which boxes are therefore still open.

Neither tool approves, merges, or deploys anything. A reviewer approving on the
strength of a green line from either one has misread both.

## The traps, every one found by running it

**Trap one: `sbe review-route` does not exist in the installed version.** It is
the command an internal card recommends as the first thing to run. On the
version installed on this machine:

```text
sbe: error: argument command: invalid choice: 'review-route'
(choose from 'doctor', 'verify', 'review', 'design', 'gate', 'score',
'intake', 'decide', 'fences', 'version', 'impact', …)
```

It exists in the sibling's development branch, not in the released build your
team would install. Until it ships, `sbe impact` is the command to start with.
Anyone handed a card naming `review-route` will hit this on their first
attempt and conclude the tool is broken.

**Trap two: on the default branch it looks like nothing happened.** Without a
`--base`, the diff runs from the merge base with the default branch. Commit
straight to that branch and the merge base is your own commit, so you get zero
changed files, tier T0, and no reviewer. That reads exactly like approval. Work
on a branch, or pass `--base main` explicitly.

**Trap three: detection is narrower than it looks.** On a repository whose API
contract lives in a plainly named file, the estate scan reported:

```text
  languages: Python(2)
  migrations: False, dbt models: False, api contracts: False, ci workflows: False
```

`api contracts: False`, on a repository containing an API contract. It is not
lying, it is reporting what its detectors cover. Do not read a `False` here as
"we checked and there is none".

**Trap four: do not start a newcomer on `sbe verify`.** It is the obvious
looking command and the wrong first one. In a normal project it prints a screen
of checks about the tool's own housekeeping rather than about your change.

**Trap five: BrotherMode is one operator per session, on purpose.** Two people
pointing sessions at the same files is not what the fence protects against
across machines. Coordination between people is BrotherSBE's job. This is a
design decision, not a gap: a coordination layer nobody can audit is worse than
none.

## What the combination will not do

Stated here so nobody finds out at the wrong moment.

- It does not know how many downstream consumers you have. It says so on every
  run rather than quietly assuming zero.
- It does not merge, approve, or deploy anything.
- It does not read your database, your warehouse, or your running services.
  These commands read your git diff and your own records.
- An exit code of zero means no control failed. It does not mean a control
  passed. Absent evidence is NO-DATA, and NO-DATA is never a pass.
- Enforcement is cooperative, through hooks, not an operating system sandbox.
  [docs/KNOWN-LIMITS.md](KNOWN-LIMITS.md) states exactly what that misses,
  including one runtime where the write fence does not fire at all.

## Team use, stated honestly

A small technical team is a supported way to use BrotherMode today, in one
specific shape: **each person runs their own session, and the coordination
between people is carried by BrotherSBE.** There are no shared accounts, no
role based access control, and no central server. Your records are files in
your own repository.

One bar this project set for itself has not been met: an external security
review has not happened. Three of the four things the product direction
requires before recommending team adoption have landed (private vulnerability
reporting, a data purge you can rehearse before running, and a plain list of
where your data lives). The fourth has not. That is stated here rather than
left to be discovered, because a limit you read up front is judgement and the
same limit found later is overstatement.

## If you only remember one thing

Work on a branch. When the change is one another team consumes, run
`sbe impact . --base main` and read the UNMEASURED lines rather than the tier.
Then send the page and the handover, unedited, and wait for the receiver to say
they have it.
