Status: CURRENT. Founder direction, 2026-08-15.

# The chain: human intent to verified reality

This is the north star for everything both products do and how they do it.
It was given by the founder on 2026-08-15 and it is authoritative. Where an
earlier document conflicts, this one wins and the conflict is named here
rather than resolved silently.

    HUMAN INTENT
         |
         v
    DEVELOPMENT METHOD        Claude native | GSD | BMAD | Superpowers
         |
         v
    BROTHERMODE               execution provenance
         |
         v
    CHANGE PASSPORT
         |
         v
    BROTHERSBE                behaviour, business impact, risk, required
         |                    proof, evidence integrity, accountability,
         |                    release readiness, production observation
         v
    HUMAN DECISION
         |
         v
    RELEASE
         |
         v
    VERIFIED REALITY

## What each stage owns, and who may not touch it

HUMAN INTENT. A person wants something. This is the only stage no software
may originate. Everything downstream exists to carry it faithfully or to
report honestly that it could not.

DEVELOPMENT METHOD. How the work gets done: Claude native, GSD, BMAD,
Superpowers, or whatever comes next. DELIBERATELY INTERCHANGEABLE. Neither
product owns a method, competes with one, or requires one. A team that
already has a way of working keeps it. This extends the assurance product's
existing rule (it owns assurance and borrows execution) one level upward:
the method is borrowed too.

AND WHEN THERE IS NOTHING TO BORROW, founder correction 2026-08-15, because
the paragraph above had a hole in it. Interchangeable does not mean optional.
Most people arrive with NO method plugin installed, and for them this stage
is empty, which means they get whatever the assistant does by default. That
is precisely the third underlying problem the review named: everyone has
their own method, so context falls on the floor at handover.

THE RULE, therefore: BrotherMode ships a NATIVE FLOOR that fills this stage
well on a machine with nothing else installed, and steps aside cleanly the
moment a real method is present. Three properties, all binding:

- IT IS A FLOOR, NOT A CEILING. It must be genuinely good on its own, not a
  stub that exists to be replaced. A person with only BrotherMode gets a
  real method, walked through, not a law they are expected to have read.
- IT DEFERS, MECHANICALLY. `tools/bm_toolkit.py route` already decides which
  installed capability owns a task class and prints a degrade path when none
  is present. The floor is what that degrade path resolves to, so deferring
  is a routing decision that already runs rather than a promise in prose.
- IT NEVER BECOMES A REQUIREMENT. The floor may not block work, may not
  demand its own steps when another method is installed, and may not
  duplicate a capability the machine already has.

Status today, measured rather than assumed. A design for the floor was
produced and then attacked by two independent adversarial passes briefed to
refute it. BOTH returned survives: false, and what they found matters more
than the design would have.

THE SIX DEGRADE PATHS, audited by running each one and then verifying the
fallback it names actually does what the sentence claims:

    product-strategy      REAL         the store's goal, scope, success
                                       criteria and non-goals, exercised end
                                       to end in a scratch project
    long-phase-planning   PARTLY-REAL  the acceptance-checks column and its
                                       review-time refusal are real, and the
                                       CLI has no flag to set it
    tdd                   PARTLY-REAL  the gate battery exists; the
                                       "test-first law" it names exists
                                       nowhere in the repository
    security-check        PARTLY-REAL  write-site inventory and effect
                                       classes are real; "all already in the
                                       gate" is false, no suite scans the
                                       tree for secrets
    historical-recall     PARTLY-REAL  the store is real; the vault half is a
                                       human habit no tool reads or writes
    pr-review             PROSE-ONLY   no brief, no template, no tool. The
                                       named landing place, agents/reviewer.md,
                                       is 18 lines and contains none of it

One real, four partly real, one prose only. A degrade sentence that names a
capability which does not exist is worse than no sentence, because a reader
gets a more complete picture than the machine delivers.

THREE STRUCTURAL FINDINGS, each executed rather than reasoned, and each one
larger than the floor's design:

1. NOTHING LOADS THIS PRODUCT'S HOOKS OR AGENTS on the founder's own machine.
   `installed_plugins.json` holds 68 plugins and no brothermode; the sibling
   IS there. BrotherMode is installed as a directory copy under
   `~/.claude/skills/brothermode`, which carries `hooks/hooks.json` and a
   plugin manifest that nothing reads, because a skill copy is not a plugin
   install. So every control this product claims through a hook or a
   registered agent is inert on the shape people actually have. Filed as M1.
2. THE CLOSING GATE CERTIFIES A PROJECT THAT DID NOTHING. Three commands, no
   goal, no acceptance check, no build, no review, no evidence: deliver
   prints success and writes a packet whose own text reads "no evidence
   recorded". Filed as M2, and it is the sharpest thing found today, because
   the whole chain exists to stop exactly that.
3. THE FLOOR'S OWN SPINE IS BROKEN. A task created through the CLI is born
   `planned`, and `next` reads only `ready`, so the handoff from planning to
   work never happens. Filed as M3.

Also filed: a task may be born in any of the ten lifecycle states through one
flag (M4), the acceptance-criterion link is opt-in so acceptance checks
enforce nothing (M5), the capability detector cannot tell installed from
absent in either direction and would wrongly call this stage empty (M6), two
degrade sentences overstate the machine (M7), and the guided walkthrough
stops at the brief so nobody is ever told to create a task (M8).

WHAT THIS CHANGES ABOUT THE FLOOR. It is not primarily a design problem. Most
of the floor's parts exist and are broken or unreachable rather than absent,
and the pieces that would deliver a method into a live session (hooks,
agents) are not loaded at all. So the order is: make what exists actually
work and actually load, then walk people through it, and only then consider
adding steps. Adding steps to a floor whose closing gate certifies an empty
project would raise the confidence of the output faster than its truth.

BROTHERMODE. Execution provenance: what actually happened while the work was
being done. Who wrote which file, in which session, under which claim, with
which verification run after which edit, and what was left unfinished. Not
process compliance. Not a method. The record of execution as it really
occurred, including the parts that went badly.

CHANGE PASSPORT. The one object that travels from execution to assurance,
and the ONLY interface between the two products. This is the anti-duplication
rule made concrete: one seam, one object, one direction of travel. A passport
carries what was done, by whom, under what claim, with what evidence, and
what the executing side knows it did NOT establish.

BROTHERSBE. Eight concerns, listed by the founder, in his order:

    behaviour               what the software must do under stated conditions
    business impact         what it is worth and what it costs to get wrong
    risk                    how bad failure is, which decides ceremony
    required proof          what must be shown before this may proceed
    evidence integrity      whether the proof is real, current, and traceable
    accountability          who decided, who reviewed, who owns the result
    release readiness       whether this may go, stated as a judgement
    production observation  what it did once it was actually running

HUMAN DECISION. A person decides to release, or not. This node is
UNCONDITIONAL and no automation may remove it, shorten it to a rubber stamp,
or pre-approve it. The system's job is to make the decision cheap and
well-informed, never to make it unnecessary.

RELEASE. The change reaches the world.

VERIFIED REALITY. Whether it actually worked, observed rather than asserted.
This is the terminal state of the chain, and it is deliberately NOT "the gate
went green". A green gate is a claim about proof. Verified reality is a claim
about the world.

## Inside BrotherMode: what execution provenance actually means

"Provenance" is one word for a plain idea: an account of what happened that
somebody else can check. Not a diary, and not a compliance report. Six things
produce it, and each one produces a RECORD rather than a feeling.

1. THE GROUND MAP, before anything is written. `git status` first. Files
   somebody else changed mean coordinate, never overwrite. This is the
   cheapest step and it is the one that catches the expensive mistake:
   during the session that wrote this page, seven files changed underneath it
   because another session was working the same repository, and the ground
   map is how that was noticed rather than merged over.

2. CLAIMS, one writer per file. Before an agent or a session writes, it
   claims the files it will touch, and a hook refuses a write that another
   live claim covers. In this session such a refusal happened: an edit to
   `tools/bm_effects.py` was blocked by name because a different session held
   it, and the work went into a delta instead of a collision.

   CORRECTED THE SAME DAY, and the correction matters more than the example.
   That refusal came from the SIBLING's hook, `sbe_fence_hook.py`, which is a
   registered plugin here. BrotherMode's own fence hook was not running at
   all: `bm_fence_hook` appears zero times in every settings file on this
   machine, and this product is absent from the 68 entries in
   `installed_plugins.json`. So the mechanism described in this bullet is
   real, and on the machine that wrote this page it was the other product
   protecting the work. See docs/KNOWN-LIMITS.md, first entry, and M1.

3. THE WORK RECORD, in one store rather than in chat. A project with a goal,
   scope, success criteria and non-goals. Tasks that walk a ten-state
   lifecycle (planned, ready, active, blocked, awaiting review, verified,
   accepted, delivered, monitored, closed) and may not skip a stage. Every
   move records who made it, when, and why. Evidence rows attach to the task
   they belong to.

4. THE DONE-CHECK RULE. Nothing is called done without a verifying command
   run AFTER the last edit, with its output quoted. This is the single rule
   that separates a report from a claim, and it is why every status line in
   this document names what was run.

5. HANDOVER, so a session ending is not work lost. When context fills or a
   session closes, a pack is produced that the next session reads in a stated
   order: what is finished, what is in flight and at which exact step, what
   was never started, which claims are still held, and what the founder was
   asked and has not answered. A ceremony refuses a hollow pack, a missing
   status line and any session still holding unparked claims.

6. ESCALATION, so a stuck run reaches a person instead of quietly stopping.
   Four mechanical triggers: a named danger where guessing is the risk, three
   distinct approaches failed, two failures sharing one root cause, or a
   declared budget spent with nothing passing. It produces a decision packet,
   never an answer.

WHAT BROTHERMODE DELIBERATELY DOES NOT DO, because the boundary is the point:
it does not decide whether a change is safe, does not classify risk, does not
own review, and does not judge the work. It records what happened accurately
enough that somebody else can judge it. A product that both does the work and
grades it is grading itself.

## The change passport, in full

THE PROBLEM IT SOLVES. Two products need to hand work between them. Without a
defined object, each one reaches into the other's internals: the assurance
side starts reading execution state it does not own, the execution side starts
guessing what assurance will want. Both drift, and neither can be replaced.
One defined object means either side can be swapped for something better as
long as it can fill in or read the same five fields.

WHAT IT CARRIES, and nothing else:

1. WHAT WAS DONE. The change identity, the range of commits, the files
   touched. The facts a reviewer needs to find the work.
2. WHO DID IT. Which sessions and agents wrote, which claims they held, and
   the human accountable for the result. Accountability is a name, never a
   role.
3. WHAT WAS RUN. Every verification executed after the last edit, its result,
   and where it came from: a build system or somebody's laptop. Provenance of
   evidence is part of the evidence.
4. WHAT WAS NOT ESTABLISHED. The executing side's own list of what it did not
   check. MANDATORY, and it may never be empty, because a passport claiming
   nothing is unexamined is the exact lie this whole chain exists to prevent.
   Regression, performance, cross-device, interface behaviour and translated
   copy belong here by default unless something actually examined them.
5. WHERE IT CAME FROM. The development method used, named and not judged.

DIRECTION OF TRAVEL. BrotherMode produces it. BrotherSBE consumes it. It never
travels back. If the assurance side needs something the passport does not
carry, that is a defect in the passport, not permission to reach into
execution state.

A WORKED EXAMPLE, so the shape is unambiguous:

    what was done      add an optional "fax_account_id" field, pulled from
                       the CMS through the API to the client. 4 commits,
                       6 files.
    who did it         one session, holding claims on those 6 files; the
                       accountable human is the engineer who opened it.
    what was run       the unit suite (green, on the build system, run 47812);
                       the type check (green, on the build system).
    what was NOT       no regression pass, no cross-device check, no
    established        performance measurement, no interface review, and the
                       translated strings were not read by anyone who speaks
                       the language.
    method             the team's own specification-first flow.

That fourth field is what makes the passport worth carrying. A reviewer
reading it knows in one line where to spend their attention, and the QC lead
who finds interface problems nobody wrote down is being handed the exact list
of places nobody looked.

STATUS: the passport does not exist yet. This section is the specification
for it, written before either side grows half of one.

## Release, and why both products stop before it

WHAT HAPPENS. The change reaches the world: a merge, a deploy, a published
package. The host does this, meaning GitHub, Bitbucket, or whatever pipeline
the team already runs.

WHY NEITHER PRODUCT PERFORMS IT. Two reasons, and the second is the real one.
The first is scope: a deployment platform is a different product and doing it
badly would be worse than not doing it. The second is that a system which can
both approve and release has no gap in it for a person to stand in. The
release is the last point where a human can still say no, and the products
stay on the near side of that line so the node cannot be optimised away by
convenience.

WHAT MUST BE TRUE BEFORE IT. The human decision node is recorded: who
accepted, when, and against what. Today that record does not exist, which is
finding H2, and it means the current answer to "who accepted this" is nobody
knows.

WHAT CROSSES THE LINE. Nothing automated. Both products stop at the pull
request. A queue item may not claim `release` as its stage, and the queue
check refuses one that tries.

## Verified reality, and why a green gate is not it

THE DEFINITION. Whether the change actually worked, observed rather than
asserted. It is the only stage that reports on the world instead of on the
work.

THE DISTINCTION THAT MATTERS MOST IN THIS WHOLE DOCUMENT. A green gate is a
claim about PROOF: the checks that were run, passed. Verified reality is a
claim about the WORLD: the thing does what somebody needed. The two come
apart constantly, and the team review this work came from named exactly how:
a green result systematically under-represents how much checking remains,
because acceptance criteria are core functions and the problems a good tester
finds are interface behaviour, misunderstandings, and text a translator wrote
without context. Every one of those can sit inside a fully green change.

WHAT WOULD ACTUALLY COUNT, four observables rather than opinions:

- the change is not reopened for material rework within seven days;
- no rollback, incident or emergency fix is recorded against it;
- the acceptance held: the person who accepted it did not come back;
- the team's own queue numbers moved, or did not.

The seven-day test already exists in the ratified delivery definition, which
means the north star metric was already reaching for this stage before the
chain was drawn.

STATUS: nothing computes any of the four. There is no post-merge record at
all, so a tier cannot be compared with its real outcome and the success
measure cannot even be recomputed. Findings H1, H3 and H5, and they are one
hole: the system is complete up to the merge and blind after it.

## Who does what, stage by stage

The short version of the whole document. "The human" column is the one that
may never be automated away.

| Stage | BrotherMode does | BrotherSBE does | The human does |
|---|---|---|---|
| Human intent | records the goal, scope and success criteria as a project | nothing | states what they actually want |
| Development method | fills the stage when nothing else is installed, steps aside when something is | nothing | picks a method, or has none and is carried anyway |
| Execution | claims files, records the work, enforces the done-check, hands over, escalates | nothing | decides at any forcing condition |
| Change passport | produces it | consumes it | nothing |
| Assurance | nothing | risk, required proof, evidence integrity, behaviour, accountability, readiness | answers what only a person can answer |
| Human decision | presents the decision with a recommendation and a default | supplies what is proven and what is not | decides, and is named for it |
| Release | stops here | stops here | releases |
| Verified reality | should record the outcome (does not yet) | should compare outcome against tier (does not yet) | says whether it actually worked |

## The same chain, walked four ways

The chain is one shape, and it should feel different depending on who you are.
If it feels identical for all four, it is too heavy for the first and too
light for the last.

ONE PERSON, ONE SMALL CHANGE. Intent is a sentence. The method is whatever
they already do. Provenance is the claim, the record and the done-check, which
cost seconds. The passport is thin and its fourth field is honest: nothing was
checked but the unit suite. Assurance is the lowest tier, which requires two
artifacts rather than seven. The human decision is the same person, and the
system says so rather than pretending a review happened. This path must stay
fast or it will be skipped, and a skipped path records nothing.

A TEAM, A RISKY MIGRATION. Intent is a specification. Assurance is the highest
tier: both legs of the migration proven against a restore, real row counts,
an approval signed by somebody other than the author, and evidence produced by
the build system rather than a laptop. The passport's fourth field is long and
it is the most valuable page in the change. The human decision is a named
person who is accountable, and release waits for them.

SOMEBODY WHO DOES NOT WRITE CODE. A business analyst or a tester. They meet
the chain at three points only: stating intent, writing what the software must
do under stated conditions, and deciding whether what came back is right. They
should never meet a claim, a fence, a tier or a receipt. The measure of
whether this product works for them is whether the first hour is spent on the
work or on installing prerequisites, which today it is not, and that is
finding P1.

A CONSULTANCY PROVING DILIGENCE. The chain is the deliverable. Every stage
leaves a record a client can read: what was intended, what was decided and
why, what was proven, what was honestly not proven, who accepted it, and what
happened afterwards. The fourth passport field is what makes this credible
rather than a marketing document, because a report that lists only what went
well is read as one.

## The two rules this chain imposes on everything

RULE ONE, THE HUMAN LOOP IS NEVER OPTIMISED AWAY. Humans stay in the loop at
four points: the intent that starts the chain, any forcing condition where
guessing is the danger, the release decision, and the acceptance that closes
it. A change to either product that removes, bypasses, or silently
pre-answers one of those four is refused, whatever it saves. The system may
make a human decision faster, better informed, or better packaged. It may
never make it disappear.

The mechanism for re-entering the loop mid-flight already exists as of today:
`tools/bm_escalate.py` and `docs/ESCALATION.md`. When the work is stuck, that
tool converts the stuck state into a named decision for a person, with a
recommendation and a default action. It is the chain's back edge to HUMAN
DECISION, and it is the reason "always keep humans in the loop" is a
mechanism here rather than an intention.

RULE TWO, EVERY ITEM OF WORK NAMES ITS STAGE. Backlog items, plans and
proposals name the stage of this chain they serve. An item that cannot name
one goes to the parking lot rather than the backlog, which is the existing
order-of-work law with this chain supplying the objectives it refers to.

## Where the products actually stand against this chain, today

Checked against code read on 2026-08-15, not against intentions.

| Stage | State | What exists |
|---|---|---|
| Human intent | PARTIAL | No clarify or discussion step is enforced anywhere (problem P3). Intent arrives, and nothing records that it was explored. |
| Development method | PARTIAL | Method-neutral by design, and the routing that picks an installed capability per task class exists and runs. The NATIVE FLOOR for a machine with no method plugin does not exist: six task classes print a degrade path naming a few tools and one law, which is not a method anybody is walked through. |
| Execution provenance | GOOD, with one hole | Claims, fences, sessions, verification after last edit, handover packs. The one-writer control is INERT in the assurance repository (hole H9). |
| Change passport | DOES NOT EXIST | This is the largest structural gap in the chain. See below. |
| Behaviour | SHIPPED 2026-08-15 | The behaviour table, required from tier T1 up, with a check refusing four ways to fake it. |
| Business impact | DOES NOT EXIST | Nothing anywhere records what a change is worth or what getting it wrong costs. |
| Risk | SHIPPED | Tier from five intake answers, and the additive-versus-breaking split landed today. |
| Required proof | SHIPPED | The verification plan, tier-required artifacts, the four hard gates. |
| Evidence integrity | PARTIAL | Evidence binds to a commit, and provenance (who produced it) does not exist yet (problem P6). |
| Accountability | PARTIAL | Approval needs a verified signed trailer; reviewer load is invisible (hole H7). |
| Release readiness | DOES NOT EXIST | There is no readiness judgement. A green gate is being read as one, which is exactly the failure the review's closing finding named. |
| Production observation | DOES NOT EXIST | Nothing is observed after release. No deployed ref, no outcome, no reopen (holes H3, H5). |
| Human decision | PARTIAL | Escalation to a human shipped today. There is no acceptance record and no accepted state (hole H2). |
| Release | NOT OURS | The host does this. Both products stop at the pull request by design. |
| Verified reality | DOES NOT EXIST | Nothing computes whether anything worked. The agreed success measure cannot even be recomputed (hole H1). |

## The convergence worth noticing

Nine holes were found today by walking the lifecycle and asking what has no
tool at all, before this chain arrived. Five of the nine were one hole seen
five times: the system is complete up to the merge and blind after it.

This chain names that same hole from the other direction. Its last four
stages, release readiness, production observation, human decision and
verified reality, are exactly the four that do not exist. The gaps are not
scattered debt. They are the unbuilt half of the founder's own model, and
they were found independently within an hour of each other.

Mapping, so nothing is filed twice:

    H1 measurement           -> verified reality
    H2 acceptance            -> human decision
    H3 deployment binding    -> production observation
    H4 defect intake         -> the return edge, verified reality to intent
    H5 post-merge outcomes   -> verified reality
    H6 discovered work       -> intent re-entering mid-chain
    H7 reviewer load         -> accountability
    H8 duration              -> business impact
    H9 inert fence control   -> execution provenance

## The change passport, defined, because a seam nobody has defined gets built twice

It does not exist yet. Defining it now costs nothing and prevents the two
products from growing two half-versions of it. A passport carries exactly
five things and no more:

1. WHAT WAS DONE: the change identity, its diff range, and the files it
   touched.
2. WHO DID IT: the sessions and agents that wrote, the claims they held, and
   the human accountable.
3. WHAT WAS RUN: the verifications executed after the last edit, with their
   results and where they came from.
4. WHAT WAS NOT ESTABLISHED: the executing side's own honest list of what it
   did not check. This field is mandatory and may not be empty, because a
   passport that claims nothing is unexamined is the lie the whole chain
   exists to prevent.
5. WHERE IT CAME FROM: the method used, named but not judged.

The passport is produced by BrotherMode and consumed by BrotherSBE. It never
travels back. Anything the assurance side needs that the passport does not
carry is a defect in the passport, not a reason for the assurance side to
reach into execution state.

## Relationship to the existing north stars, stated rather than assumed

This chain does not replace the ratified metric. BrotherMode's north star
remains "every serious AI-assisted task ends in a resumable, review-ready,
evidence-backed delivery", measured as Confirmed External Verified Deliveries
per Week, and its ninth condition (not reopened for material rework within
seven days) is already a VERIFIED REALITY test written before this chain
existed. The assurance product's five owned things all sit inside the eight
concerns above.

What the chain adds is the SHAPE: which stage each thing belongs to, that the
method layer is interchangeable, that the passport is the only seam, and that
the chain ends in observed reality rather than in a verdict. Where the older
documents describe outcomes, this one describes the path, and the path is
what a backlog item has to name.

## The correction this document has to carry about itself

Measured 2026-08-15, after this file claimed the chain "arrives at every
session start":

    ~/.claude/skills/brothermode/VERSION   3.3.0
    <repo>/VERSION                         3.3.0
    SKILL.md                               DIFFERS (197 lines installed, 217 here)
    DIGEST.md                              DIFFERS, and the installed copy
                                           carries ZERO lines matching NORTH STAR

So the digest line was verified by running the session-start script IN THIS
REPOSITORY, and real sessions read the INSTALLED clone, which does not have
it. The claim was true about the wrong copy. The chain does not yet reach a
running session, and it will not until the installed tree is refreshed.

This is the same defect as the assurance product's SBE1, on this side of the
house, and it is the third form of finding H3 to appear in one day: a version
string that does not describe what it labels. It also decides something about
the native floor above. A floor that exists only in this repository is not a
floor, because the surface a person actually runs is the installed clone. Any
work on the method floor is finished when it runs THERE, verified across the
install boundary, not when the repository tests pass.

## What is enforced, and what is not, stated plainly

ENFORCED today: nothing in this file. It is a direction document, and a
direction document is not a control.

The nearest thing to enforcement that exists is the order-of-work law, which
says an addition that cannot name its objective goes to the parking lot, and
that law is human discipline as well.

NOT BUILT, and named so it is not mistaken for done: a check that every queued
item names a stage of this chain. Its home is `tools/bm_idle.py`, the module
that already reads and validates `docs/plan/QUEUE.json`, because a second
reader of that file would be a second parser of one format, which this project
has been burned by before. Until that check exists, this rule is discipline.
