# The Brother Charter

This is the constitution the three products in Brother share. It does not change per product and it does not change per stage. What changes across Stage 0, Stage 1, and Stage 2 is how much of it runs as one system versus three; what is written here is true at every stage.

Read this charter for the vocabulary and the rules that bind all three products at once. Read `docs/MERGE-PLAN.md` for the staged plan, the decisions behind it, and how those two documents were reached.

## The chain

Written down by the founder on 2026-08-15, the chain has eight stages. Every product in Brother implements all eight, even where, today, that implementation lives in a different repository from this one.

1. **Human intent.** A person states what they want built and why. This is not a ticket auto-filed from a metric; it is a person's stated want, on the record.
2. **Development method.** The work that follows intent runs through a named, repeatable method, not an improvisation nobody could rerun.
3. **Execution provenance.** Every step of the work, as it happens, is logged: what ran, what changed, in what order. This is BrotherMode's core concern today.
4. **Change passport.** The change carries its own record from the moment it starts to the moment it ships: what it touched, what checked it, what it is waiting on. The passport is the internal module boundary this chain resolves to at Stage 1; it is not reinvented per product.
5. **Assurance.** The change is checked against its concerns before it ships, under hard gates rather than a reviewer's mood. This is BrotherSBE's core concern today.
6. **Human decision.** A person decides whether to ship the change. Assurance can recommend; it cannot decide.
7. **Release.** The change goes out, on the record, with what shipped and when.
8. **Verified reality.** After release, the real outcome is checked against what was claimed for it. This is BrotherDS's core concern today, and the newest, least proven stage of the eight.

## Four unconditional human decision points

Regardless of how automated the six stages between them become, four points on the chain are never delegated to a gate, a script, or a model:

1. **Intent.** Only a human states what is wanted.
2. **A forcing condition.** Partway through, something can force a human call that no automated check resolves on its own; the chain does not pretend that call away.
3. **Release.** Only a human decides to ship, informed by assurance, never overruled by it.
4. **Acceptance.** Only a human accepts that the delivered result is the one that was wanted.

## The unit: one claim vocabulary, three domains

Brother's shared unit is not a runtime store and it is not three unrelated ones either. It is one claim vocabulary with three domain subtypes, each answering a different question on the chain:

- **The work was done.** Execution provenance claims: did the logged steps actually happen. BrotherMode's domain. These resolve fast, on the order of a single change, so this store's natural lifetime is **seconds**.
- **The change is safe to ship.** Assurance claims: does the change clear its hard gates. BrotherSBE's domain. These resolve over a review and gate cycle, so this store's natural lifetime is **days**.
- **The number is true.** Verified reality claims: did the real outcome fall inside the uncertainty stated for it. BrotherDS's domain, extended to a fourth domain beyond the three capability areas: **master data**, the reference data a number's truth is checked against. These resolve only once a forecast or a reported figure meets its real outcome, so this store's natural lifetime is **months**.

Three stores, three lifetimes, one vocabulary. A claim in any of the three carries the same shape: what was claimed, what verdict it resolved to, and what evidence produced that verdict. That shape is what makes a BrotherMode claim and a BrotherDS claim comparable at all, without forcing BrotherMode's second-by-second store and BrotherDS's month-by-month store into one runtime table neither one needs.

## Why three stores, not one

A single runtime table holding a second-by-second execution log next to a month-by-month claim-versus-outcome record would either force the fast store to wait on the slow one, or force the slow store to be queried at a cadence it never needs. Neither is free. What Brother forces instead is the vocabulary: the same claim shape, the same verdict tuple, the same evidence law, so a person reading a BrotherDS claim and a person reading a BrotherMode claim are reading the same kind of statement, even though the two live in stores built for very different lifetimes. This is decision 2 of 2026-08-22, recorded in full in `docs/MERGE-PLAN.md`: one claim vocabulary, not one runtime store, not three unrelated units either.

## The three capability areas, in this charter's terms

BrotherMode answers stage 3 of the chain, execution provenance, most directly, and carries the guided beginner surface that makes intent (stage 1) approachable for someone new to the method. BrotherSBE answers stage 5, assurance, most directly, and is where the hard gates behind stage 6's human decision actually run. BrotherDS answers stage 8, verified reality, most directly, in the one domain, claims, where the chain's last stage is not yet proven end to end. None of the three owns the whole chain alone; each is strongest at a different stage of it, which is exactly why one shared vocabulary matters more than one shared codebase does at this point.

## Master data, the fourth domain

The three capability areas are BrotherMode, BrotherSBE, and BrotherDS. Underneath the claims domain sits a fourth domain that is not a capability area in its own right: master data, the reference data a claimed number is checked against to know whether it was true. BrotherDS's own gates G11 through G14 are where this domain is checked today; G1 through G10 are the general gates every claim passes through regardless of domain.

Master data earns its own line in this charter rather than being folded silently into "claims" because it answers a different question than a claim does. A claim says: this number, with this uncertainty, will turn out to be true or not. Master data says: this is the reference a claim gets checked against, and if the reference itself is wrong, no claim checked against it can be trusted regardless of how carefully its own uncertainty was stated. A verified claim rate computed against corrupt master data is a precise measurement of the wrong thing.

## The verdict tuple

Every claim resolves to exactly one of three verdicts:

- **PASS.** The evidence supports the claim.
- **FAIL.** The evidence contradicts the claim.
- **NO-DATA.** The evidence has not arrived yet.

NO-DATA is never treated as a pass, and it is never treated as a hard block either. It is a distinct, honest third state: the correct verdict for BrotherDS's own north star right now, because no claim in its store has resolved. A system that quietly upgrades NO-DATA to PASS to keep a dashboard green is lying about what it knows; a system that treats NO-DATA as an automatic FAIL punishes claims for the crime of being new. Brother does neither.

## The evidence law

No completion claim stands without a verifying command that ran after the last edit, with its output quoted next to the claim it supports. This binds every product in Brother and it binds this repository: nothing in this charter, the merge plan, or the progress page is allowed to say "done" without a command run after the change it is describing, whose exact output appears beside the claim. A number nobody can reproduce is not evidence.

## The north star

The chain to verified reality governs the whole of Brother; it is the thing every stage ultimately serves. Verified Claim Rate is reported for the claims domain only, defined as: the share of resolved decision-grade claims whose realised outcome fell inside the uncertainty stated for it at the time the claim was made. It is not reported for the execution-provenance or assurance domains, because those domains resolve a different question (did the work happen, is the change safe) that a realised-outcome rate would misrepresent. Today, Verified Claim Rate is NO-DATA, honestly, because no claim in the claims domain has resolved yet.

## How the last stage resolves per domain

Stage eight, verified reality, does not have one shared mechanism across domains; it has one shared question, answered differently by the two products that implement it today:

- **Assurance** computes the change verdict from two inputs it already has: readiness (is the change ready to ship) and observation (what happened once it ran). The change verdict is a function of those two, computed, not asserted.
- **Claims** computes the claim verdict from a different pair: an outcome, once it is known, scored against the uncertainty the claim stated up front.

Neither implements the other's mechanism, and this charter does not force them to converge on one. What they share is the chain's shape and the vocabulary the verdict is expressed in, not the arithmetic that produces it. A future attempt to merge these two computations into one function would be solving a problem neither domain has.

## The surface caps, withdrawn

Withdrawn 2026-08-22. Decision 9 of 2026-08-22 had set the target: roughly 79 surfaces across the three products, cut to about 24 inside Brother; decision 11 then fixed the real floor at 31, keeping all thirteen agents. The withdrawal supersedes both numbers. The architecture of record, `docs/plan/ADR-2026-08-23-one-brother-repository.md`, is in this tree and reads the architecture back off the files that implement it; the longer deciding record it was written from, with the founder's own words and the full decision table, is `products/brothermode/docs/plan/ADR-2026-08-23-one-brother-repository.md`. It chose Option B: one repository, three plugins, one marketplace. Its criterion C4 freezes the tool surface, no skill or command renamed, no new public command, and its criterion C3 keeps each product installable alone. Its inventory found the reason a shared cap cannot hold: five colliding skill names across the three trees, and folding them into one namespace to fit under a cap would require renaming them, which C4 forbids. A cap that forces deletion is the wrong control for an architecture that keeps three surfaces deliberately separate, so it is withdrawn here rather than kept on the books and ignored. The ADR is the overriding source; where this charter and the ADR ever seem to disagree about the surface, the ADR wins.

`tests/test_surface.py` no longer counts skills, commands, agents, or hooks against any number. What it verifies instead, taken from its own assertions: this repository stays MIT licensed; no workflow file under `.github/workflows` carries an automatic trigger (push, pull request, pull request target, schedule) or names a macOS or Windows runner, per the founder's standing law against automatic cloud compute; `.claude-plugin/marketplace.json` lists at least two plugins, each naming its own source, so the three products can never be made one install unit; the claims product is listed there only once its own `plugins/brotherds` directory exists, never before its context is separated out; once `products/` exists, each product directory carries its own `.claude-plugin/plugin.json`, and the shared passport and handoff contracts live in a root `contracts/` directory, which mirrors `products/brothermode/schema/change-passport.v1.json` and `products/brothersbe/contracts/handoff-package.v1.json` byte for byte (`scripts/test_contracts_root.py` is the check); and `COORDINATION.md` names the ADR above as the architecture of record. Where an assertion cannot yet reach a verdict, at Brother's Stage 0, it reports NO-DATA by skipping rather than passing quietly.

## The refusal policy

The refusal this section once stated, that a new skill, command, agent, or hook is turned away unless it retires one that already exists or the caps above still hold, is withdrawn along with the caps it depended on. What holds in its place is the ADR's own C4: no existing skill or command is renamed, and no new public command lands without the founder deciding to add one on purpose. `tests/test_surface.py` no longer refuses a new surface on a headcount; it verifies the shape described above instead.

## A claim, walked through each domain

The same eight-stage chain, the same four human decision points, applied to one claim in each domain, to make the abstraction concrete.

**Execution provenance (BrotherMode).** Intent: a person asks for a specific change. Method: the change runs through BrotherMode's guided flow. Provenance: each step of that flow is logged as it runs. Passport: the change carries what it touched and what checked it. Assurance: the change is checked before it ships. Decision: a person approves it. Release: it ships. Verified reality: did the logged steps match what actually happened. Verdict: PASS if they match, FAIL if they do not, NO-DATA if nobody has checked yet. Lifetime of this claim in its own store: seconds to minutes, because a single change resolves fast.

**Assurance (BrotherSBE).** Intent: a person opens a change against a real system. Method: BrotherSBE's own design-then-verify order. Provenance and passport: same shape, carried by the change through review. Assurance: hard gates score readiness and observation. Decision: a human ships or does not, informed by the gate verdicts, never overruled by them. Release: the change goes out. Verified reality: the assurance verdict is itself computed from readiness and observation, so this domain's last stage is not a separate check bolted on afterward, it is the gate computation completing. Lifetime: days, the span of a review and gate cycle.

**Claims (BrotherDS).** Intent: a person states a number and the uncertainty they are willing to stand behind. Method and provenance: the number is produced through BrotherDS's own gates, G1 through G14. Passport: the claim carries its stated uncertainty from the moment it is made. Assurance: the claim clears BrotherDS's gates before it is treated as decision grade. Decision: a person accepts the claim as one worth tracking to an outcome. Release: the claim is recorded, uncertainty and all. Verified reality: months later, the real outcome is scored against the stated uncertainty. Verdict: PASS if the outcome fell inside it, FAIL if it did not, NO-DATA until the outcome is known, which is where every claim in this domain sits today.

## Glossary

- **Claim.** A stated fact, with the uncertainty or gate it must clear, waiting on a verdict.
- **Verdict.** One of PASS, FAIL, or NO-DATA, attached to a claim once evidence exists (or explicitly, honestly, does not).
- **Evidence law.** No completion claim without a verifying command run after the last edit, output quoted.
- **Change passport.** The record a single change carries with it from intent to release: what it touched, what checked it, what it is waiting on.
- **North star.** The chain to verified reality, as the thing every stage of Brother ultimately serves.
- **Verified Claim Rate.** The share of resolved decision-grade claims in the claims domain whose realised outcome fell inside its stated uncertainty. Reported for the claims domain only.
- **NO-DATA.** The verdict for a claim whose evidence has not arrived. Never a pass, never a hard block.
- **Surface.** One skill, command, agent, or hook wire. Once counted toward numeric caps in `tests/test_surface.py`; those caps were withdrawn 2026-08-22 (see "The surface caps, withdrawn"), and the file now verifies the ADR's shape instead of a headcount.
- **Capability area.** One of BrotherMode, BrotherSBE, or BrotherDS, each a working product on its own before it is anything to Brother.
- **Master data.** The fourth domain, alongside the three capability areas: the reference data a claimed number is checked against to know whether it is true.
- **Stage.** Brother's own program stage (0, 1, or 2), not to be confused with a stage in the eight-stage chain; the chain's stages are numbered 1 through 8 above and apply within every program stage.

## What is enforced versus what is discipline

**Enforced, by a file, today:**

- The umbrella's structural shape (MIT license, no self-firing CI, the marketplace's plugin catalog, the layout once it lands, and `COORDINATION.md` naming the ADR): `tests/test_surface.py`, run by `/usr/bin/python3 -m unittest tests/test_surface.py`. The numeric surface caps this line once named were withdrawn 2026-08-22; see "The surface caps, withdrawn" above.
- No workflow that can FIRE BY ITSELF, and no macOS or Windows runner: the same test file. Stated precisely because the difference decides what the check means: the rule is NOT that `.github/workflows` is absent, it is that nothing in it can start cloud compute on its own. Today no workflow file exists here, so both of those assertions report NO-DATA by skipping and naming that reason. NO-DATA is not a pass. The moment a workflow file lands they become real assertions, which is the shape this check was built for.
- The client-name, attribution, and dash scans on this repository's own tree and history: `scripts/cleanse.sh`.

**Stated as discipline, not yet enforced by any hook or script:**

- The refusal policy itself, in its current, post-withdrawal form: nothing stops a future editor from renaming a skill or command, or adding a new public one, without checking the ADR's C4 first; the test file does not catch that kind of drift.
- The claim vocabulary and verdict tuple as a working, shared runtime: today this exists as a specification in this charter, proven in exactly one place, BrotherDS, and not yet forced as one running metric across all three products.
- The two Stage 1 gates: nothing pages anyone when BrotherSBE's open pull request count changes, or when BrotherDS scores its first claim; both are checked by hand, against the merge plan, until an owner builds the check.
- Verified Claim Rate's own computation: NO-DATA is the honest state of an unbuilt measurement, not the output of a running one.

This section is a promise to keep updating as each of these moves from stated discipline to an enforced file, not a permanent list.

## If a fourth capability area is ever proposed

This charter does not rule out a fourth capability area beyond BrotherMode, BrotherSBE, and BrotherDS, but it sets a high bar for one: it has to answer a question on the eight-stage chain that none of the three, plus master data, already answers, and it has to arrive with the same evidence law every claim in this charter already carries, not a promise to add evidence later. Absent that, a new idea is a feature inside one of the existing three, not a fourth area, and the refusal policy above governs it the same way it governs a new skill or command.

## Relationship to each product's own documents

This charter does not replace what BrotherMode and BrotherSBE already say about themselves. Each keeps its own README, its own release notes, its own queue. Where this charter and a product's own document appear to disagree about that product's internals, its own document wins; this charter only governs the vocabulary the three products share and the structural shape this repository enforces on itself, now that the numeric caps are withdrawn. `COORDINATION.md` is the live channel for reconciling the two when they drift.

## Open questions this charter does not answer

Written down here rather than papered over with a confident sounding sentence: this charter does not define what "finished" means for BrotherMode's backlog or BrotherSBE's open pull request queue, only that both must reach it before Stage 1. `docs/MERGE-PLAN.md` proposes a definition for each. The founder RATIFIED the BrotherSBE half on 2026-08-24: a DRAFT pull request does not count against gate two, because a draft is its author saying not ready, so it is not work in flight that a merge would have to rebase. The BrotherMode backlog half is still unratified, and this sentence says so separately rather than letting one ruling read as two. This charter does not specify the exact schema of `hooks/hooks.json` or what counts as one "logical hook wire" beyond the reading `tests/test_surface.py` uses today, because no product has moved a hook into this repository yet to force that question. And this charter does not say how the claims domain's month-long lifetime should be queried by something that needs an answer sooner than a month, because nothing in Brother has needed that yet either. Each of these gets answered when something real forces the answer, not before.

## This charter's own history

First written 2026-08-22, at Brother's Stage 0, alongside `docs/MERGE-PLAN.md` and this repository's first commit. It is expected to change as Stage 1's gates are checked and as the domain stores above move from specification to a working, shared runtime; each change to this file should say, in its own commit, which of the sections above moved.

No section of this charter is final. The parts most likely to change first, in order, are: the definition of finished referenced above, the hook wire schema once a real one lands, and the enforcement list, as each stated discipline above earns a file.
