---
name: brother
description: "The one Brother door. Say what you are trying to do, in plain language, and it routes to the right capability without you naming a product. Triggers include brother, what can brother do, start a project, check status, what's next, review this, ship this, verify this change, show the board, when will this land, what slipped, what is blocking us. Invoke as /brother."
argument-hint: "[what you are trying to do]"
---

# Brother

One door. Read `$ARGUMENTS` for what the work IS, never for a product name
the person is not expected to know. Never print a list of skills or
commands, and never answer "unknown command" for anything Brother-shaped.

## Bare `/brother`, or "continue"/"resume": follow the one decision order

`$ARGUMENTS` empty, or the words "continue"/"resume" on their own, follow
the single authoritative decision order in
`bundle/skills/using-brother/SKILL.md` ("Bare `/brother`: the authoritative
decision order"). Do not re-derive it here; the two steps below are this
command's own mechanics for carrying out that order's rows 1 and 2, not a
second rule that could disagree with it.

**Step 1, check for unfinished work before asking anything.** Run the
discovery form of the engine, resolver form first:

    "${CLAUDE_PLUGIN_ROOT}/runtime/brother-run" --continue --cwd <this repository>

or, inside a clone install or a checkout of the Brother repository itself:

    python3 scripts/brother_run.py --continue --cwd <this repository>

No unfinished work: it prints "no unfinished run found" at exit 0; fall
through to Step 2. Exactly one unfinished run: it resumes and prints one
sentence naming the OUTCOME; relay that sentence, never the run directory.
More than one: it lists them, numbered, by plain-language outcome, and asks
for a number; relay that numbered list and, once answered, re-run with
`--continue N`.

**Step 2, no unfinished work: ask the one question.** Say exactly one plain
sentence and ask exactly one question, nothing else:

"Brother turns AI-assisted work into something checkable instead of just
trusted. What are you trying to do right now: start or check on a project,
or get a change proven safe before it ships?"

Wait for the answer, then route with the rule below.

## BUILD IT: a plain implementation outcome runs the whole spine

**Before the assurance and project routing below**: when the ask is an
IMPLEMENTATION OUTCOME, a thing to build, add, fix or change in the
repository this session sits in ("add feature X", "build a tool that...",
"make the exporter handle..."), and no existing run needs continuation, do
not route to a menu. Run the engine, resolver form first:

    "${CLAUDE_PLUGIN_ROOT}/runtime/brother-run" "<the outcome, verbatim>" --cwd <this repository>

works on an installed bundle: a plugin install exports `${CLAUDE_PLUGIN_ROOT}`
for command content, so this resolves on its own, and the resolver runs the
packaged engine from ITS OWN directory (never this session's cwd), so it
works pointed at any target repository with no Brother checkout anywhere
nearby, and its run bookkeeping defaults to a per-user state directory
rather than the read-only plugin cache. Inside a clone install, where the
variable is unset (the same fallback `brotherme-brief`'s command uses), or
inside a checkout of the Brother repository itself, run the same engine
directly instead:

    python3 scripts/brother_run.py "<the outcome, verbatim>" --cwd <this repository>

Either form. It decomposes the outcome with a model,
validates the units deterministically, claims each durably, runs real
coding-model workers in isolated per-writer worktrees, audits every write
against the declared scope, integrates serially with each unit's own check
re-verified on the advancing canonical revision, and prints a delivery
report. Exit 0 means every unit integrated; nonzero names each refused unit
with the scheduler's or auditor's own reason. Relay the delivery report in
plain language: what integrated, at which revision, what was refused and
why. Ask a question first ONLY when the outcome is genuinely ambiguous
about something the decomposition cannot decide.

**RECOVERY is Step 1 above, not a second rule.** A bare `/brother` with no
outcome, or "continue"/"resume" said in plain words, is row 1 of the
decision order: check for unfinished work FIRST (Step 1's discovery
command), and only fall through to asking what to build when that discovery
reports none. Never run this section's engine call on a bare invocation
before Step 1 has had its answer.

## Routing by intent

**Assurance** when the ask describes a change to code or a system someone
will have to trust: a schema or migration, an API or service contract,
money, personal data, a production path, a pull request or diff to review,
or a number about to be claimed as true.

**Project** when the ask describes a project, an outcome, a plan, or a
status question: starting something new, checking where work stands, what
to do next, or closing with a delivery summary.

Both answer the same six verbs; only the exact command differs, because one
side has not yet renamed its short forms:

| Verb | Project (BrotherMode) | Assurance (BrotherSBE) |
|---|---|---|
| start | `/brothermode:brotherme-start` | `/brothersbe:start` |
| status | `/brothermode:status` | `/brothersbe:status` |
| next | `/brothermode:next` | `/brothersbe:next` |
| review | `/brothermode:review` | `/brothersbe:review` |
| deliver | `/brothermode:brotherme-deliver` | `/brothersbe:verify` |
| help | `/brothermode:help` | `/brothersbe:help` |

Pick the row from the words used (start, status, next, review, deliver,
help, or a plain synonym), pick the column from Assurance vs. Project above,
and enter there. When the ask names no verb, default to `start` if nothing
is in flight yet, `next` if it sounds like work already began.

## handover

**handover** (also "wrap up", "close the session", "end of day", "hand this
over"): close-of-session state capture, feeding the estate's own vault
instead of being lost in scrollback. This verb only works inside a checkout
of the Brother repository itself, because it runs that repository's own
`scripts/handover_ceremony.py`; if this session is not inside that
checkout, say so plainly rather than routing anywhere.

From the repository root:

    python3 scripts/handover_ceremony.py --collect --repo <REPO> [--repo <REPO> ...]

gathers the measurable close state: git HEAD and clean/dirty for each named
repo, open sbe tasks with their owners and ages, the day-plan ready set, and
open pull requests. When the session produced a lesson worth keeping
(a failure, a finding, a decision), write it into a `--lesson-file PATH`
(a JSON list of objects carrying `name`, `description`, `symptom`,
`what_happened`, `why_it_matters`, `how_to_apply`), then:

    python3 scripts/handover_ceremony.py --emit-vault <VAULT-DIR> --lesson-file <PATH>
    python3 scripts/handover_ceremony.py --emit-handover <PATH> --lesson-file <PATH> --repo <REPO> [--repo <REPO> ...]

writes one vault note per lesson (valid frontmatter, refusing rather than
writing anything whose status or type falls outside the vault's controlled
vocabulary) and the human START-HERE handover markdown, priority first.

The ceremony only EMITS: it never pushes, never commits, and never closes a
task. After running it, say in one plain sentence what it produced (how
many vault notes were written, where the handover markdown landed, and
whether anything was refused), and that committing or pushing those files
is a separate, deliberate step.

SHOW BOTH IN THE CHAT, EVERY TIME: send the handover zip as a file, and
also print the full mega prompt inline in the reply inside one fenced
block, so it can be copied into the next session without opening anything.
This is a deliberate exception to sending a single attachment: the handover
ceremony always sends the zip and the inline prompt together, and the
inline prompt is not a leftover file to tidy away.

## board

**board** (also "track", "the gantt", "when will this land", "what slipped",
"what is blocking us", "are we on time"): the readiness roadmap, its dates,
and what has missed them. Like `handover`, this verb only works inside a
checkout of the Brother repository itself, because it runs that repository's
own scripts; if this session is not inside that checkout, say so plainly
rather than routing anywhere.

    python3 scripts/gen_readiness_board.py     renders docs/plan/READINESS-BOARD.html
    python3 scripts/track_delivery.py          the delivery verdict per row

The board is rendered from `docs/plan/READINESS-ROADMAP-2026-08-29.json` and
is never hand edited. Waves in it are DEPENDENCY DEPTH, not dates: a row goes
READY when its dependencies have actually closed, never because a date
arrived. Every row carries its own done_check, its watchdog probe, its
estimate in hours, and the exact datetime it was promised for.

**A miss is recorded, never re-dated.** This is the reason this verb exists
at all. A plan that quietly moves a slipped
row teaches nobody anything: the miss vanishes and the blocker behind it
survives to cause the next one. So `track_delivery.py` EXITS NONZERO when a
row is past its promise with no blocker recorded, and a row that lands late
keeps its miss rather than being forgiven by having eventually landed.

### The intervention ladder

Applied per row, mechanically, from the roadmap's own `delivery_policy` so it
is data rather than prose:

| Miss | What happens |
|---|---|
| 1 | Record the blocker BY NAME and re-promise once. Most first misses are estimation error, not a systemic blocker, and escalating one is noise. |
| 2 | Fable reviews the ROW, not the worker. Twice missed usually means mis-scoped or carrying an unnamed dependency, and re-promising a third time without re-reading the row is the failure this ladder exists to stop. |
| 3 | Stop. Escalate to the user through the question UI with the blocker, what was tried, and two options. Never a fourth silent re-promise. |

One blocker CLASS hitting three DISTINCT rows is systemic, and becomes one
vault lesson rather than three row notes. Three misses on a single row is a
row problem, not a systemic one, and does not earn a lesson.

### The row contract: no abstract rows

A row must say clearly what is being shipped: the feature or code being
delivered, its role and why it is prioritized, the effect that will be
observable, and when it appears. A row that names work without naming what
it delivers is too abstract to act on.

Every row therefore carries five fields, and a board whose rows do not is
REFUSED rather than rendered with a warning:

| Field | The question it answers |
|---|---|
| `ships` | What feature or code is actually delivered. A file, a command, a capability, never a theme. |
| `role` | What it does in the system, and who it serves. |
| `why_now` | Why it sits at this point in the order rather than later. |
| `effect` | What somebody will OBSERVE that they cannot observe today. |
| `visible_when` | When that effect appears. |

`visible_when` is deliberately a separate field from `delivered_at`, and the
difference is not pedantry. A control's effect usually appears LATER than its
code: a recurrence counter exists the day it is written, but its rate means
nothing until roughly twenty real work units carry receipts. Conflating the two
is how a board reports a capability as live when only its source is.

Enforced in `scripts/gen_readiness_board.py` `validate()`, which returns exit 1
naming every row and field that fails, so an abstract row cannot reach the
rendered page. A whitespace-only field does not satisfy it. This is a control
rather than a convention on purpose: an unenforced rule tends to degrade to
optional under time pressure, which is exactly the failure this check exists
to catch.

### Start from the person, and name what you borrowed

Every row starts from the user, not from an abstract concept: the personas,
a smart and simple UI/UX, and intelligent design come first. Borrowing
matters too: ideas adapted from the wider competition, including
best-in-class players worldwide, are named as borrowed rather than
presented as invented from nothing.

So the row contract is eight fields, not five. The three added are the ones a
reader meets FIRST on the rendered page:

| Field | The question it answers |
|---|---|
| `persona` | Which of the four people this is for. |
| `their_moment` | The moment in that person's day when they meet it. |
| `what_they_see` | What is actually on screen. Not a capability, a screen. |

The four personas are fixed and named in the roadmap: the business analyst who
brings intent and cannot read a diff, the head of development who wants the diff
and will grab the wheel, the head of data science who needs to know where a
number came from, and the operator, who is the only user until the door
opens. They map onto the four views (Outcome, Data, Code, Balanced).

A feature carries two more fields, and they are the antidote to inventing
things nobody asked for:

| Field | The question it answers |
|---|---|
| `borrowed_from` | The product and the exact mechanism, read from its own docs. |
| `adaptation` | What Brother changes, and why. |

**A copy is not a steal.** If the adaptation reads "same as theirs", the feature
is cargo cult and does not ship. The adaptation is where Brother's own three
pillars turn somebody else's mechanism into something the original cannot do:
Cursor's checkpoints become durable because they land in the controller store,
Devin's playbooks become earned because the recurrence counter promotes them,
Qwen's consolidation runs backwards over the decaying tail rather than silently
over everything.

Mechanisms are read from the product's own documentation, and the reading is
recorded with its limits: documentation describes structure reliably and says
nothing about comparative quality. These are mechanisms to steal, never claims
that another product is better.

### The design doctrine: add just enough, and say no to most things

This is a core feature of this door rather than a note on one board: start
from the personas and a smart, simple UI/UX and intelligent design, rooted
in real world physics. Simplicity comes from adding just enough, not from
adding more, art is about removing. Success means saying no to most things
to say yes to what is most valuable. The weight of each decision matters.

Four laws, all enforced in `validate()`, none of them a poster:

1. **Person first.** Every item names a persona, the moment they meet it, and
   what is on screen. Not a capability. A screen.
2. **Grounded in real world physics.** Every item names the real constraint it
   answers: how long a person will actually wait, how much they will actually
   read, what actually breaks at 2am. A feature justified only by internal
   elegance has no physics and does not ship.
3. **Subtraction.** Every addition names what it REMOVES: a step, a file, a
   concept, a decision the user no longer has to make. If it removes nothing it
   must say why the addition is still net positive, and that sentence is read.
   A feature that only adds is suspect by default.
4. **Weight.** Every item states what it costs if it is wrong and how reversible
   it is. A cheap reversible bet and a one way door are different decisions and
   must not look the same on a board.

**The NO list is first class.** Success is saying no to most things, and a board
that lists only what will be built cannot show that. So refusals are recorded
with the same care as acceptances: what it was, why it was refused, and the FLIP
CONDITION that would reopen it. `validate()` refuses a board whose refusal has no
flip condition, because a refusal with no way back is a grudge, not a decision.

**Apply it to the board before applying it to anything else.** This doctrine's
first act was to cut the board that carried it: twelve parity features became
eight, four were refused with flip conditions, and 44 of 134 hours went
unspent. A doctrine of subtraction whose first act is to add a doctrine would be
self refuting, and the four cuts are on the page where anyone can argue with
them.

### One integrator, and every other stream feeds it

Commits are centralized under one main stream; every other stream is a
feeder that submits its work to be validated and pushed to live.

ONE INTEGRATOR holds a repository's canonical tree and is the ONLY session that
checks out, merges or pushes its main branch. Every other stream is a FEEDER: it
works in its own branch or worktree, never changes branch in the canonical
checkout, and submits a pull request carrying its declared write set, its
done-check output, its receipts, and its own statement of what it could not
verify. HANDBACK, not a push to main: a feeder finishing work pushes its own
branch at most and hands back to the integrator, who reviews, gates and
merges by pull request; `scripts/pre_push_gate.py`'s handback guard refuses a
feeder's push to the default branch mechanically, `BROTHER_MAIN_PUSH=allow`
being the loud, named exception.

The integrator validates AS IF MERGED against the current tip, never on the branch
alone. Testing a branch proves the branch works; it does not prove the branch
works with whatever landed while it was being reviewed. And the integrator MERGES
rather than rewrites: a submission needing changes goes back to its author,
because the author knows why the code is as it is and the integrator does not.

WHY, measured in one estate on one day with five sessions writing: about 500 lines
deleted and never committed, 83 lines claimed by two sessions costing three
sessions an hour, two efforts fixing the same defect within an hour, two dead
fences holding five contended files, a file written in no declared scope, a JSON
reserialize that would have hidden a content loss inside 1625 changed lines, and a
branch checkout performed over another session's live uncommitted edits. Every one
is a WRITE CONTENTION failure. None is a code defect.

Borrowed from Git's own integration-manager workflow, the Linux kernel's
dictator-and-lieutenants scaling of it, and the not-rocket-science rule behind
bors and GitHub's merge queue: automatically maintain a repository that always
passes its tests, by testing a change as if merged rather than on its branch.

Brother's addition is CONFLICT-AWARE BATCHING. A standard merge queue is FIFO and
serializes everything because it cannot know which changes interact. This estate
already computes write-set conflicts, so submissions with disjoint write sets are
tested TOGETHER in one speculative merge and only overlapping ones serialize. A
submission that declares no paths is serialized rather than batched, never
assumed safe.

THE RULE BINDS THE INTEGRATOR TOO. Today's checkout over somebody else's
uncommitted work was made by the session holding this role. Concentrating write
access reduces contention; it does not confer care. And the handoff is explicit:
the estate must be able to name who holds the role, because an integrator that
stops without handing over is a stalled queue, the same failure shape as a fence
with no expiry.

### The honest limit, stated rather than discovered

This ledger records what a session TELLS it. Nothing in it observes work
directly, so a session that never runs the tracker produces no misses and the
board reads perfect. That is the same could-not-go-red class this estate
keeps finding in its own controls, and the only mechanical answer is that
`scripts/check_all.sh` runs the tracker, so a battery run cannot avoid it.
Say this plainly when reporting a green board; do not let a clean tracker
line stand as evidence that work is on schedule when it may only be evidence
that nobody looked.

## What this never does

- Never a menu of skills or commands, at zero arguments or any other.
- Never "unknown command": any Brother-shaped ask maps to a cell above; a
  genuinely unclear ask gets the one routing question from Step 2 above.
- Never claims a capability that is not installed. Claim verification
  (BrotherDS, scoring a number against a real outcome) is experimental and
  not shipped by this bundle; say so plainly if that is what is being asked
  for, rather than routing anywhere.
- Holds no state and makes no PASS, FAIL or NO-DATA verdict itself; those
  belong to whichever product does the work. `board` is not an exception:
  the delivery verdict is `track_delivery.py`'s, and the door only routes to
  it and reports what it said.
- Never re-dates a missed row to make a board look healthy. A miss is
  recorded with its blocker or the tracker fails; there is no third option.
- Never renders a row that does not say what ships, its role, why now, the
  effect, when it appears, WHO it is for, the moment they meet it and what
  they see. An abstract board cannot be argued with, so the renderer refuses
  it rather than printing it with a warning.
- Never ships a feature whose adaptation column says the same as the thing it
  was borrowed from. A copy is not a steal.
- Never ships a feature that removes nothing, answers no real world
  constraint, and states no cost if it is wrong. That is not a decision.
- Never renders a board with an empty NO list. A plan that refused nothing is
  a wish list.
- Never pushes or merges a main branch from a session that is not the named
  integrator for that repository, and never changes branch in a canonical
  checkout that holds another session's uncommitted work.
