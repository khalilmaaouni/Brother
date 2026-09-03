# The north star

Founder direction, 2026-08-15. This is the canonical chain for both products
and for how work is done. Every backlog addition names which node it serves, or
it goes to the parking lot rather than the backlog.

```
                    HUMAN INTENT
                         |
                         v
                 DEVELOPMENT METHOD
      +-----------+-------+-------+------------+
      | Claude    |  GSD  | BMAD  | Superpowers|
      | native    |       |       |            |
      +-----------+-------+-------+------------+
                         |
                         v
                    BROTHERMODE
                execution provenance
                         |
                         v
                  CHANGE PASSPORT
                         |
                         v
                     BROTHERSBE
      +----------------------------------------+
      | Behaviour                              |
      | Business impact                        |
      | Risk                                   |
      | Required proof                         |
      | Evidence integrity                     |
      | Accountability                         |
      | Release readiness                      |
      | Production observation                 |
      +----------------------------------------+
                         |
                         v
                   HUMAN DECISION
                         |
                         v
                      RELEASE
                         |
                         v
                  VERIFIED REALITY
```

## What the chain asserts, read carefully

It is not a picture of the current products. It is four claims, and each one
changes something.

**1. The method layer is plural and swappable, and it sits ABOVE BrotherMode.**
Claude native, GSD, BMAD and Superpowers are peers. BrotherMode does not compete
with them and must not become a fifth. The existing promise, "bring any agent,
use any tool, prove every change", extends by one word: **bring any method.**
Whatever produced the plan, the chain below it must still work.

**2. BrotherMode's job is execution provenance, and it CARRIES a method rather
than requiring one.** Provenance means what happened, who did it, in what order,
against which claim, and what was left unfinished. That is the part no external
method provides and the part BrotherMode must own.

The method layer is where planning and task decomposition live, and the critical
qualifier, founder direction the same day: **a user with none of those plugins
installed must still get the role filled, and filled well.** Not a degraded stub
with a message recommending an install. The built-in path is a first-class
method that a person could use forever without ever hearing of GSD, BMAD or
Superpowers.

So the rule has two halves and both are load bearing:

- **BrotherMode never competes.** An external method present means BrotherMode
  YIELDS the planning role to it and keeps provenance underneath. It does not
  duplicate, override, or quietly re-plan what that method decided.
- **BrotherMode is never a dependency in the other direction.** No plugin is
  required for the chain to work. With nothing else installed, the suite fills
  every node of this diagram on its own, properly.

The failure this rules out is the common one: a product that is excellent when
you have its whole ecosystem and thin when you do not. The measure is that a
person who installs only this suite gets a complete method, and a person who
brings their own keeps it intact.

**3. The Change Passport is the seam, and it is a named object rather than a
handoff.** Everything above it produces one; everything below it consumes one.
If the passport is right, a team can change methods without changing assurance,
and change assurance without changing methods.

**4. Human decision is a node, not a courtesy.** It sits between all the proof
and the release, unconditionally. The machine never releases. And the chain does
not end at release: it ends at VERIFIED REALITY, so nothing is finished at merge
and production observation closes the loop.

## Where the products sit on it today

Read the provenance column before the state column. CHECKED means confirmed
against the code in the session that wrote this file, with the file or the
search named. CARRIED means it comes from the previous handover pack and was not
re-checked here.

| Node | State | Provenance |
|---|---|---|
| Human intent | PARTIAL | CHECKED: one outcome question at kickoff; no versioned intent object |
| Development method, plural | ABSENT | CHECKED: `grep -rilE '\b(bmad\|gsd\|superpowers)\b' src/ tools/ skills/` returns one test file and no ingestion path |
| BrotherMode, provenance | PARTIAL | CARRIED: lifecycle, fences, work packets, telemetry exist; scope wider than provenance |
| Change Passport | PARTIAL, consumer half only | CHECKED: `tools/sbe_passport.py` reads the five fields from the store and reports each carried or NO-DATA (`python3 tools/test_sbe_passport.py`, 19 tests, OK). Fields 2 and 5 are NO-DATA in every run here because they are owed by execution provenance. Still no stable identity across revisions |
| Behaviour | SHIPPED | CHECKED: `templates/dossier/08-behaviour.md`, `check_behaviour` in `tools/sbe_design.py` |
| Business impact | WEAK | CHECKED: intake asks whether money, partner or personal data is touched; no impact statement exists |
| Risk | SHIPPED | CHECKED: `compute_tier`, corrected this session to follow blast radius rather than answer shape |
| Required proof | SHIPPED | CHECKED: the Proof column per behaviour row, read by `tools/sbe_testkit.py` |
| Evidence integrity | PARTIAL | CHECKED: receipts, head-commit binding and CI origin labels exist; the hand-written receipt loophole is narrower and still open |
| Accountability | PARTIAL | CARRIED: the signed Approved-by trailer works on any host; nothing detects that a change NEEDED an approval |
| Release readiness | ABSENT | CHECKED: no per-change readiness verdict in `src/` or `tools/`. `sbe_release_invariant.py` governs the product's own releases, not a change's readiness |
| Production observation | ABSENT | CHECKED: no module observes production; the only match is a test asserting an approval report is derived rather than observed |
| Human decision | PARTIAL | CARRIED: the approval gate verifies a declared approval; no READY FOR HUMAN DECISION state |
| Release | EXTERNAL | by design: deployment systems stay the authority, we record identity |
| Verified reality | ABSENT | CHECKED: no post-release state exists in `src/brothersbe/` |

**The shape of that table is the finding.** The product is strong in the middle
and empty at both ends. It proves things well, and knows nothing about where the
work came from or what happened to it afterwards. Four nodes are ABSENT and
three of them are the last three steps of the chain: release readiness,
production observation, verified reality. The fourth is the first step, the
method layer.

Put plainly: **today the product certifies a change up to the moment it merges,
and the north star says the job is not finished there.** Release readiness was
graded WEAK in the first draft of this table and the search corrected it to
ABSENT, which is a fair illustration of why the provenance column exists.

## What this settles

- **The overlap question.** BrotherMode owns provenance, BrotherSBE owns
  assurance, the passport is the contract. Neither implements the other's
  mechanism. One fence owner, one evidence schema.
- **Whether BrotherMode competes with GSD, BMAD or Superpowers.** It does not. A
  team already using one keeps it and gains provenance underneath.
- **What finished means.** Not merge. Verified reality, after observation.
- **Where the human sits.** Not at the end as an approver of finished work, and
  not as a bottleneck to be automated away. At a named node the machine cannot
  pass through on its own.

## The human rule, which outranks every mechanism below it

Humans stay in the loop from start to end. Concretely, and each of these is
testable rather than aspirational:

1. **Nothing releases without a human decision.** Not a configured auto-approve,
   not a policy exception, not an agent with good evidence.
2. **Work taken off a person's plate is announced, referenced and documented.**
   They are told what was taken, where the record is, and how to take it back.
   Silent helpfulness is the failure this rule exists to prevent.
3. **A check that cannot reach a verdict names what it needs from a person**, in
   the interface, next to the thing it could not decide. Never in a log.
4. **No new blocking gate in front of a human queue.** A gate in front of a
   queue is not capacity. A new control must measure, reveal, or remove labour.
5. **Absent evidence is reported as absent.** NO-DATA is never a pass and never a
   block, and a green result states what it did not check.

## How this is used

**On every backlog addition.** Name the node it serves. An item that cannot name
one is parked, not queued.

**On every design decision.** Ask which side of the passport it belongs on. If
the answer is both, it belongs in the passport rather than in either engine.

**On every claim that something is finished.** Ask which node it reached. Merge
is not verified reality, and saying so is the difference between what this
product promises and what it currently delivers.

## Cross-references owed

This file is canonical. Two other surfaces must point at it. One now does.

- `docs/DIRECTION.md` in this repository, whose five things it owns predates the
  chain, is compatible with it, and did not name it. **PAID 2026-08-15:** it now
  opens by naming this file as canonical and above it, says the north star wins
  where the two disagree, and links the passport seam contract.
- `PRODUCT-DIRECTION.md` in the BrotherMode repository, which carries its own
  north star section and is the product authority there. **PAID 2026-08-15 by the
  companion's own session at `632226e`**, verified here by reading the file: line
  34 links `docs/NORTH-STAR-CHAIN.md`. It was written as still owed earlier in
  this same session, which was true when this file was read and stale by the time
  it was committed.

  **What checking it turned up is worth more than the cross-reference.** What
  `PRODUCT-DIRECTION.md` names is the companion's OWN chain document, not this
  file. So two documents are called the north star, one per repository, and
  nothing reconciles them. They already disagree: the chain document marks
  evidence integrity amber because evidence supposedly never records whether a
  build system or a laptop produced it, while the table above records CI origin
  labels as existing and `tools/sbe_passport.py` reads them out by name. Two
  canonical pages is the drift a canonical page exists to prevent. Not resolved
  from here: it is the companion's file, and which one is canonical is the
  founder's call rather than a session's.

## Decisions taken on this chain, 2026-08-15

Three, through the question UI, with what they rejected.

**N1. The Change Passport is the SEAM, not a schema.** D7 stands unamended. The
passport names the contract between provenance and assurance. What exists today,
the dossier plus its intake plus its receipts, grows exactly two things: a stable
identity across requirement revisions, and a decisions array.
*Rejected:* building it as a specified object with its own lifecycle and state
machine, which is the advisor blueprint's version and a quarter of work before
the pilot team sees anything new.
*Flip condition:* two teams on different methods need to exchange passports.
Until then the seam is the deliverable and the schema is speculation.

*BUILT UNDER N1, 2026-08-15, and it does not amend it.* The consumer half of the
seam exists: `tools/sbe_passport.py` reads the five fields out of the store this
project already keeps and reports each one carried or NO-DATA. It adds no
required file, no state, no identity and no gate, which is what keeps it a view
rather than the object N1 rejected. Fields 2 and 5, who did it and where it came
from, are NO-DATA in every run here and are named as owed by execution
provenance. The contract and the producer half owed are in
`docs/specs/2026-08-15-change-passport-seam.md`.

**N2. The whole end of the chain gets built:** release readiness, production
observation, and a verified-reality state that closes each change. This is the
largest commitment on the page and it makes VERIFIED a real state rather than a
word on a diagram.
*Rejected:* readiness alone, and readiness plus observation. The founder took
the full scope deliberately.

*THE DEPENDENCY, named before the work starts rather than discovered inside it.*
Production observation requires production READ access, and this project's own
blast-radius law forbids apply rights on production state and forbids typing,
storing or logging a credential. Read-only observation is compatible with that
law, but it still needs a credential that only the founder can supply, and it
needs a decision about which signals may be read at all. So the sequence is
forced: readiness first, because it needs no production access whatsoever;
observation second, gated on that credential; verified reality last, because it
is a state computed from the other two. A session that starts with observation
will stall on an ask that should have been made on day one.
*What it buys:* the only honest answer to "did the change actually work", which
is the question every gate before it is a proxy for.

**N3. The method layer is proven with one foreign method before anything is
re-architected.** Take a method this suite does not own, run one real change
through it, and see what BrotherMode can capture as provenance without owning
the plan.
*Rejected:* narrowing BrotherMode's scope now on the north star's authority
alone, which would move planning out of a working product on the strength of a
diagram; and deferring the method layer entirely.
*Why this order:* evidence before architecture. If BrotherMode cannot capture
provenance for a plan it did not make, that is the finding, and it is cheaper to
learn from one change than from a refactor.

*AMENDED the same day, by direct instruction, and it changes the test:* the
built-in method must fill the role WELL for a user who has no other plugin
installed. So N3's experiment has two arms, not one, and the second is the one
that protects the ordinary user.

- **Arm A, the foreign method.** One real change planned by a method this suite
  does not own. Question: what provenance can BrotherMode capture without owning
  the plan? Failure here means the yield path does not work.
- **Arm B, the bare install.** One real change on a machine with nothing but this
  suite: no GSD, no BMAD, no Superpowers, and the Claude-native path only.
  Question: is the result a complete method or a thin one? Failure here means the
  product is excellent only inside its own ecosystem, which is the failure this
  amendment exists to prevent.

Arm B is the higher priority of the two, because a user without the other
plugins is the common case and a user with them is the lucky one. A degraded
experience there is not a gap in an integration, it is the product being bad for
most of the people who install it.

*Done-check for both arms:* the change reaches the same nodes of this chain, with
the same evidence, in both runs, and any node that one arm reaches and the other
does not is named rather than averaged away.

## What still needs the founder

Nothing blocks N1 or N3. **N2 stops at production observation** until a
credential and a signal list arrive. That ask belongs in the first session that
picks up N2, made before the work starts rather than at the point of stalling.
