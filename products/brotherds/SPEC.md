# BrotherDS

Status: V1 design, with a working engine and five real claims already running.
Date: 2026-08-16. Author: Khalil Maaouni.

## Mission

Every number that reaches a decision carries its proof, and is scored later
against what actually happened.

## The gap the other two leave

BrotherMode answers "was the work executed and delivered correctly". BrotherSBE
answers "can the business trust this change enough to ship it". Both take a
CHANGE TO A SYSTEM as their unit.

Neither of them says whether a NUMBER is true.

A pipeline can be perfectly built, perfectly reviewed, perfectly released, and
still produce a figure that is wrong, or right but incapable of supporting the
sentence somebody wrote next to it. The pilot warehouse proves this tonight: the
data is real, the pipeline works, and 2,350 product identifiers silently merge
different products, so every per-product figure ever computed on it has been
built on a key that is not a key. No amount of change assurance catches that,
because nothing changed.

BrotherDS takes the CLAIM as its unit: one number that reaches a decision.

## North star

**Verified Claim Rate.** Of the decision-grade claims that have been scored
against reality, the share whose realised outcome fell inside the uncertainty
they stated at the time.

Numerator: claims that resolved and held.
Denominator: claims that resolved.
A claim that has not resolved is in neither, and is reported separately.

Three properties make this the right metric and not a vanity count:

- It cannot be gamed by producing more claims. Producing more only enlarges the
  denominator.
- It punishes overconfidence directly. A narrow interval that reality falls
  outside is a miss, so stating honest uncertainty is rewarded rather than
  penalised.
- It is a statement about the world, not about proof. This is the exact
  analogue of the VERIFIED state at the end of the shared chain, and the reason
  BrotherDS is allowed to both do the work and judge it: the judge is reality.

**Honest limitation, stated up front.** This metric reports nothing until claims
start resolving, which for a monthly business figure is one to three months and
for an annual one is a year. Any plan built on it needs a leading indicator to
survive the wait. See "The waiting problem" below.

## The spine

    QUESTION
      the decision somebody actually has to make
        v
    CLAIM
      one number, with its origin class
        v
    EVIDENCE
      lineage, grain, independent re-derivation, uncertainty
        v
    WHAT WAS NOT ESTABLISHED
      mandatory, never empty
        v
    DECISION
      a person decides, and is named for it
        v
    OUTCOME
      what actually happened
        v
    VERIFIED CLAIM
      the outcome fell inside the stated uncertainty, or it did not

The last two stages are what nothing else in the market does. Confirmed against
primary sources: no product binds a number to its query, data version, method
and uncertainty; nothing scores a claim against the later outcome and keeps the
track record; and OSF pre-registration, the closest thing to a gold standard
that exists, explicitly does not verify that the executed analysis matched the
plan that was registered.

### How it sits inside the shared chain

BrotherDS does not add a stage to the north-star chain. It occupies the same
positions the other two do, for a different unit:

| Shared chain stage | Change (Mode plus SBE) | Claim (BrotherDS) |
|---|---|---|
| Human intent | what to build | the question a decision needs answered |
| Method | the team's development method | the analytical design |
| Provenance | BrotherMode: what happened | what was queried, fitted, assumed |
| Passport | Change Passport | Claim Receipt |
| Assurance | BrotherSBE: eight concerns | ten gates |
| Human decision | release or not | act on this number or not |
| Release | the host merges | the decision is taken |
| Verified reality | no reopen, no rollback | the outcome scored the claim |

The four unconditional human decision points are inherited unchanged, not
redefined.

## The unit: a claim

A claim is a JSON file. It is authored by a person or an agent, and checked by
the engine. Fields:

| field | required | meaning |
|---|---|---|
| `id` | yes | stable identifier |
| `statement` | yes | the claim in a sentence, in the language a decision-maker uses |
| `value`, `unit` | yes for numeric claims | the number itself |
| `origin` | yes | one of the five evidence classes |
| `question` | yes | the decision this serves |
| `decision` | yes | what changes depending on the answer |
| `grain` | yes | the level the number was computed at |
| `evidence.source` | for SYSTEM | the database, with its size and mtime recorded at check time |
| `evidence.derivations` | for SYSTEM | two or more independent SQL paths to the same number, plus supporting queries marked `computes_value: false` |
| `uncertainty` | yes | an interval and its method, or `NOT_ESTABLISHED` with a reason |
| `not_established` | yes, never empty | what this claim does not settle |
| `accuracy` | for predictive claims | history, actual, predicted, and the metric named |
| `design` | when the statement asserts causation | the identification strategy and the test of its assumption |
| `protocol` | for non-SYSTEM origins | the per-class interrogation, see below |
| `outcome` | filled later | the realised value, who observed it and when |

Direction of travel: a claim is produced where the analysis happens and consumed
where the decision happens. It never travels back. Anything the decision needs
that the claim does not carry is a defect in the claim, not a licence to reach
into the analyst's session.

## The five evidence classes

The founder's own addition, and the part with the most established science
behind it. Many numbers are not in any system. Treating a vendor extract, an
expert's judgement and an open hypothesis as if they were all "data" is how
commercial analysis goes wrong quietly.

| Origin | What it is | Interrogation | Adjustment | Basis |
|---|---|---|---|---|
| `SYSTEM` | a query against a governed source | independent re-derivation; grain uniqueness; does the stated value reproduce | none; it either reproduces or it does not | this repo's G5, G7, G9 |
| `THIRD_PARTY` | vendor, panel or external dataset | provider, collection method, coverage, known biases | a coverage and bias discount that does NOT shrink as the sample grows | Datasheets for Datasets; Croissant; panel bias literature |
| `ELICITED` | expert or manual judgement | expert role, elicitation protocol, a calibration question, the seed score | performance based weighting rather than equal weighting | Cooke's classical model, which beat equal weighting in 32 of 33 cross validation studies |
| `ASSUMPTION` | stated and unverified | who stated it, the plausible range, the decision sensitivity | tornado, then Morris screening, then Sobol indices if it still matters | Saltelli, Global Sensitivity Analysis |
| `HYPOTHESIS` | openly untested | the test that would settle it, the cost of being wrong | may inform a test; may never alone reach a decision | Simmons Nelson Simonsohn 2011; Gelman and Loken 2013 |

**No blended trust score.** Each class is discounted by its own mechanism and
reported separately. A single number combining them would hide exactly what the
five literatures identify separately, and would be the "arbitrary 87 percent"
that the assurance product already refuses.

## The refusals

Ten gates, all implemented and all covered by the selftest. Verdicts are PASS,
FAIL and NO-DATA, matching BrotherSBE's canonical tuple. NO-DATA is never a pass
and never a block.

| Gate | Refuses |
|---|---|
| G1 not_established | an empty limits list. A claim asserting that nothing is unexamined is the precise lie this product exists to prevent |
| G2 origin | a claim that does not name one of the five classes |
| G3 uncertainty | a bare number. Either an interval with its method, or NOT_ESTABLISHED with a reason |
| G4 causal | causal wording with no named design, or a design whose identifying assumption was asserted rather than tested |
| G5 rederivation | a number reached by only one path. Supporting queries are separated from value-computing ones, so a month count is never checked against a percentage |
| G6 accuracy | MAPE where the actuals contain near zeros; an accuracy figure with no baseline; a seasonal claim on fewer than two full cycles |
| G7 value | a stated number that no longer reproduces, whether from the warehouse or from the recomputed metric |
| G8 protocol | a non-SYSTEM claim whose class protocol is incomplete (reported NO-DATA, since incompleteness is honest, not false) |
| G9 grain | a number with no declared level, and optionally a declared key that is not actually unique in the source |
| G10 definition | a named metric with no definition, and two claims using one name with two different definitions. First use is NO-DATA: one claim proposes, it does not define |

## Boundaries

BrotherDS is not, and will not become:

- a BI or dashboard tool
- a notebook environment
- an AutoML or model serving platform
- a data catalogue or lineage collector (it reads them; OpenLineage and dbt
  already do this well)
- an orchestrator
- a replacement for an LLM. It governs and scores analytical work; it does not
  try to be a better general analyst than the frontier models

And the line against its siblings, inherited from the 2026-08-11 decision:
BrotherSBE does everything around the model and never the model. BrotherDS owns
the model and the claim. Where BrotherSBE's data-reviewer already covers grain,
fan out, keys, system of record, reconciliation, freshness and cost on a CHANGE,
BrotherDS does not rebuild it; it consumes the verdict.

## LLM policy

An LLM may sit in a verification path only as first-pass triage whose output a
deterministic check or a human confirms. It may never be the final arbiter of
arithmetic, of whether an executed analysis matched its plan, or of whether a
claim cleared its uncertainty band.

This is not caution for its own sake, it is the measured state of the art:
Spider 2.0's best system scores 30.35 percent and a GPT-4-class model 6.0
percent; DABStep hard tasks 16 percent; DSBench 34.12 percent; MLE-bench reaches
Kaggle bronze in 16.9 percent of competitions. Databricks' own documentation
says single-model text-to-SQL "fails a lot in production".

The rule follows: **the LLM may propose, the mathematics disposes.**

## V1 scope

In:
- the claim schema and the ten gates (done)
- SYSTEM claims against DuckDB (done)
- the human-readable receipt (done)
- the five origin protocols as required fields (done as fields, protocols
  documented, adjustment methods not yet computed)
- a Claude Code plugin surface wrapping the engine

Out of V1, deliberately:
- the accuracy ledger's scoring loop beyond a single recorded outcome
- Snowflake, Databricks and Power BI connectors
- any LLM in the path
- the plan versus execution diff
- integration into the BrotherMode passport

## What is UNENFORCED, stated plainly

Following the rule that a limit without a file behind it must say so:

- Nothing forces a claim to exist. An analyst who does not write one is not
  caught. The engine checks claims that are offered to it.
- The `not_established` field is checked for being non-empty, never for being
  honest. A single lazy line passes G1.
- The origin protocols for THIRD_PARTY, ELICITED, ASSUMPTION and HYPOTHESIS are
  checked for completeness of fields only. No adjustment method is computed yet,
  so Cooke weighting and Sobol indices are documented intent, not code.
- Nothing yet scores a claim against its outcome, so the north star currently
  has no numerator and no denominator. The `outcome` field exists and the
  receipt reports its absence; nothing populates it.
- Source identity is size and mtime, not a content hash, so a source rewritten
  in place at identical size would not be detected.

## Reconciliation with prior work, and one thing the founder must decide

This spec was drafted as if BrotherDS were greenfield. It is not. Two earlier
bodies of founder-directed work exist and were reconciled against it after
drafting. What follows is the correction.

### The founder's scope is 13 items, not one

The directive of 2026-08-12 (`docs/SCOPE-2026-08-12-founder-directive.md`)
covers data science, business science, data analytics, business intelligence,
upstream business analysis, project management and delivery framework, value
realization tracking, a self-improvement loop, assistance to individuals and
teams, answers through humans or agentic workflows, integration with the first target
estate's data stack, personalization by persona, and low inference cost.

This V1 serves items 1 to 5 directly and item 8 in embryo. It defers 9 to 13.
It takes no position on items 6 and 7, for the reason below. Narrowing is
deliberate and reversible; deletion is not proposed.

### THE OPEN BOUNDARY QUESTION, which this spec must not answer

Section 3 of that directive states that items 6 and 7, project management and
value realization, sit on ground BrotherMode and BrotherSBE already hold, and
that the resolution is **for the founder, not for an implementer.** Three
options are recorded, with A recommended and not ratified.

**This spec presumes Option A**: BrotherDS owns the question, reads the other
two products' records, and builds no second delivery framework. That presumption
is stated here rather than left implicit, because an earlier draft behaved as
Option A silently, which is exactly what the directive forbids.

If the founder chooses B or C, the Boundaries section above is what changes.

### The definition problem, and gate G10

The 2026-07-31 red-teamed corpus at `~/Documents/BrotherData-Copilot-Spec`
states as its headline finding: *"A definition is not a file. The one thing the
architecture unifies is the one thing that was never the problem."*

Gates G5 and G7 prove a number REPRODUCES from its own source. They cannot see
that two teams mean different things by the same word. In an estate where gold
modelling and the semantic layer sit on opposite sides of a seam, that is the
failure that actually happens.

**Gate G10 was added in response.** A claim names the metric it computes and
defines it. A registry holds the reference definition per name. First use is
NO-DATA, because one claim proposes rather than defines. A second claim using
the same name with a different definition is a FAIL that names the claim it
collides with, so the argument becomes about the definition rather than about
whose number is right. Registration refuses to overwrite a conflicting entry
silently.

This is the simplest honest form of the cross-surface agreement test that the
prior corpus designed. It does not replace a semantic layer; it detects
disagreement between whatever definitions people are actually using.

### Why the pilot estate first, which the earlier draft did not justify

Not arbitrary, and not merely convenient. The 2026-07-31 red team directed proof
onto pilot-estate figures because that is where the evidence is: real data the founder
owns outright, with a documented history of numbers later proven wrong. The first
target estate remains the founder-named one. The sequencing is the pilot estate
to earn the evidence, the target estate to spend it.

### What a Snowflake-facing version must not rebuild

The first target estate is recorded as Snowflake-only, agentic-first, bilingual, with a checkpoint
gate on 2026-10-16 and no data permitted to leave the account. Its native
surface already ships Cortex Analyst, Cortex Search, Cortex Agents, semantic
views, dbt model contracts, data metric functions and Snowflake Trail.

Freshness, null, duplicate and row-count checking are GA features there. A
Snowflake-facing BrotherDS reads those verdicts; it does not reimplement them.
The gates that remain genuinely ours are G1, G3, G4, G6, G8 and G10, because
nothing in that estate refuses an undesigned causal claim, an unstated
uncertainty, a wrong accuracy metric or a contested definition.

### The four survivors of the prior red team, each with its disposition

The 2026-07-31 red team named four things worth keeping out of a much larger
scope. Two were absorbed into this design without being credited, and two were
neither absorbed nor refused, which is the state that rots: a survivor nobody
argued with is not the same as a survivor somebody kept. All four are settled
here.

**Survivor 1, the trust mechanism: INCORPORATED as a named extension, not built.**
The prior design is a dual-run comparator whose RIGHT SIDE CHANGES ACROSS A
LIFECYCLE: first the incumbent artifact somebody already trusts, usually a
spreadsheet, then the system's own history once it has one. That is not what G5
does. G5 compares two independent derivations of one number inside ONE governed
source, which answers "is this internally reproducible" and cannot answer "does
this agree with the thing the reader believes today".

The distinction matters most at exactly the moment this product is supposed to
be useful, when a number replaces one somebody has been reading for years. It is
also, as the red team observed, the one item in that scope with no competing
vendor.

It needs no new machinery here. A claim may already carry derivations from
different evidence classes, so a dual run is a SYSTEM derivation and a
THIRD_PARTY or ELICITED comparison against the incumbent artifact, held side by
side and diffed, with the comparison's own provenance interrogated by the
protocol its class already owes. What is missing is that G5 currently requires
both paths to be SQL against the same source, so it cannot express the pairing.

**Not built, and stated as such.** Widening G5 to accept a cross-class
comparison is designed, not coded, and until it is, this product cannot do the
one thing the prior corpus rated highest.

**Survivor 2, the semantic and metric layer specified against an interface:
ABSORBED, in a smaller form.** Gate G10 and the definition registry are the
simplest honest version of the cross-surface agreement test that survivor
described. G10 does not replace a semantic layer and does not try to: it detects
that two claims use one metric name with two different definitions, which is the
failure the layer was wanted for. The section above on what a Snowflake-facing
version must not rebuild carries the rest of the interface reasoning.

**Survivor 3, the disproving test: ABSORBED verbatim.** The condition "the reader
wants the spreadsheet anyway" is the first of the three disproving tests below,
kept in the prior corpus's own words along with its discipline of naming the
test before building rather than after.

**Survivor 4, catalog and label remediation: DELIBERATELY DROPPED from this
product, with the reason.** It is scoped to the executive report set of the first
target estate and carries a dated coverage target. That makes it a PROGRAMME
DELIVERABLE of that estate, not a capability of this product, and building it
here would put a delivery commitment for one estate inside a tool meant to serve
any of them.

What BrotherDS does contribute to it is the mechanical half: G9 already checks
that a declared key is actually unique at the declared grain, which is the check
such a remediation runs thousands of times. The remediation itself, its target
and its schedule belong to whoever owns that estate's report catalog.

**Reversal condition.** If the remediation stalls for want of the mechanical
check rather than for want of people, that is evidence the check needs to be
packaged for that estate, and this drop should be revisited as a packaging
question rather than a scope question.

### What would disprove this product

Borrowed from the prior corpus, which insisted a disproving test be named before
building rather than after:

1. **The reader wants the underlying spreadsheet anyway.** If a person reads a
   receipt and still asks for the raw file before believing the number, the
   receipt has not transferred trust and the product has failed at its only job.
2. **Authoring cost.** Tonight's five claims took an expert session. If writing a
   claim cannot become a side effect of doing the analysis, this is ceremony,
   and ceremony gets abandoned. Measure it at week three.
3. **Nothing ever resolves.** If no claim reaches an outcome within 45 days, the
   north star has no fuel and the product is a receipt printer.

## The waiting problem

The north star cannot report anything for months. A plan that waits for it will
look dead for a quarter. Two leading indicators are available immediately and
neither requires a resolved claim:

1. **Defects found.** Real, countable, immediately valuable. Tonight produced
   one on the first evening of use: 2,350 identifiers merging distinct products.
2. **Claims that changed a sentence.** How often a receipt caused somebody to
   soften, qualify or withdraw a statement they were about to make. Self
   reported, so weaker, but it is the leading edge of the real metric.

Both are reported as leading indicators and neither is ever presented as the
north star.
