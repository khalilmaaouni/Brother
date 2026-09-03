# Direction: what BrotherSBE is, and what it refuses to become

Ratified 2026-08-11 with the founder. This page is the distilled, public form
of the product direction; roles only, no client or person named. The release
plan that executes it is
[the V3 refocus and release plan](specs/2026-08-11-v3-refocus-and-release-plan.md).

**This page is not the top of the chain.** [The north star](../NORTH-STAR.md),
founder direction of 2026-08-15, is canonical for both this product and the
companion, and it is the page every backlog addition names a node from. This
page predates it, is compatible with it, and is narrower on purpose: the north
star draws the whole chain from human intent to verified reality, and this page
describes only the assurance stretch of it that BrotherSBE owns. Where the two
disagree, the north star wins and this page is the thing to correct.

The single crossing between the two products is the change passport. Its
contract, and which half of it exists today, is
[the change passport seam](specs/2026-08-15-change-passport-seam.md).

## Identity

BrotherSBE is the assurance layer for high-risk engineering changes: database
migrations, data pipelines and transformations, API and event contracts,
partner integrations, master data, infrastructure changes, backfills, and the
technical QA around them.

> Bring any agent. Use any tool. Prove every change.

The operating rule beneath everything: **BrotherSBE owns assurance. It borrows
execution.** A better coding agent, a better warehouse CLI, a better scanner
makes BrotherSBE better, because BrotherSBE is the layer that decides what
must be proven and verifies that it was.

## The five things it owns

1. **The change record.** One versioned record of intent, decisions, risk,
   ownership, evidence, and final state. More durable than any chat session.
2. **The assurance policy.** Risk classification, the obligations each risk
   carries, required reviewers, required evidence, exceptions with owners and
   expiry.
3. **The evidence contract.** What actually ran, against which final change,
   with which result and trust level. PASS, FAIL, and NO-DATA stay distinct,
   and absence of a result is never a pass.
4. **The team memory contract.** Durable, provenance-aware memory every agent
   and teammate can reuse: canonical in Git, projected for humans (the
   Obsidian projection), and never silently promoted into policy.
5. **The toolkit broker.** A governed way to discover, trust, invoke, and
   learn from external skills, MCP servers, CLIs, and APIs. Learn
   automatically, govern deliberately: popularity is a discovery signal, never
   a trust decision.

Everything else is an adapter over an external capability, a view over
BrotherSBE state, or out of scope.

## Who it is for

- The engineer accountable for a change, who should not repeat context to
  every reviewer.
- The reviewer and the technical leads, whose time is the team's real
  constraint, and who receive each change as one Assurance Pack: what
  changed, why, the computed risk, what was checked, what honestly was not,
  and what needs their decision.
- The platform and quality leads, who define the standard once instead of
  re-litigating it per change.
- The consultancy that must prove diligence to a client.
- The data scientist or analyst, as the fifth persona inside the same star.

## What it refuses to become

Not a coding agent, an IDE, a review bot, a CI system, a data catalog, a
lineage platform, a BI tool, a cloud console, an issue tracker, a wiki, a
deployment platform, or an autonomous production operator. Where a mature
product owns a capability, BrotherSBE integrates it and normalizes its
evidence; it does not rebuild it.

The admission test for any proposed feature, in order: does it strengthen one
of the five owned things; does a strong external product already own it; does
its absence block a real change record; would a thin adapter do; can its
output become normalized evidence or trusted memory; does it lower adoption
friction; and what gets removed when it ships.

## How it spreads

Paved road, not forced road. Nothing to install, nothing to learn, no intake
before work: the pipeline reports and never blocks, the reviewer receives the
Assurance Pack, and authors follow because the reviewer asks for it.
Enforcement is something an estate turns on later, after watching the
reporter catch something true. Low-risk work stays lightweight by design: the
correct outcome for a small change is one line saying existing CI is
sufficient, proceed.

## The measure

The star is the enforced-trust category: every assistant claim machine
checked. The measure of it is trusted change throughput: how many important
changes ship with final evidence bound to the final state, named human
accountability, and no material failure after merge. Usage is not the metric.
A claim this product cannot prove yet is labeled unproven, here and
everywhere.
