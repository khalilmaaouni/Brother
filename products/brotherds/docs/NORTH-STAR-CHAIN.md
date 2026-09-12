# The BrotherDS north star chain

Status: DESIGNED, and enforced in code where it says so below. Two things in it
are founder-gated and marked as such: who owns value realization, and which of
the three paths in `OPTIONS.md` this serves.

Date: 2026-08-16. It follows the founder-authoritative chain that BrotherMode
carries, and it does not fork it.

---

## Rule one, inherited unchanged

Humans stay in the loop at four points: the question that starts the chain, any
forcing condition where guessing is the danger, the decision to act on the
number, and the acceptance that closes it. A change to this product that
removes, bypasses, or silently pre-answers one of those four is refused,
whatever it saves.

That rule is quoted from the shared chain with two words changed, question for
intent and decision to act for release, because the unit is different and
nothing else is.

---

## The thesis: BrotherDS adds no stage

The shared chain already runs from human intent to verified reality. The
temptation with a third product is to bolt a tenth stage onto the end of it, and
that would be wrong twice over: the chain already ends in the right place, and a
product that needs its own stage is a product that has not found its seam.

BrotherDS occupies the SAME stages the other two occupy, for a different unit.
The first two products take a CHANGE TO A SYSTEM as their unit. BrotherDS takes
a CLAIM, one number that reaches a decision.

So the integration is a mapping, not an extension. The mapping lives in code,
in `bds.py`'s `CHAIN` table, and the block below is the same table. The selftest
refuses to let the two disagree.

```chain
question -> intent
design -> method
provenance -> provenance
receipt -> passport
refusals -> required-proof, evidence-integrity
human-decision -> human-decision
decision-taken -> NONE
outcome -> production-observation
verified-reality -> verified-reality
```

Run it: `python3 bds.py chain`.

---

## The chain, stage by stage

### question, standing in the shared stage `intent`
The decision somebody actually has to make. Human-originated, and no software
may originate it, exactly as with intent on the change side. A number produced
without a question attached is the thing this product exists to refuse: it can
be perfectly true and still support no decision.
Owner: a person. Status: ENFORCED as a required field of every claim.

### design, standing in the shared stage `method`
The analytical design. Interchangeable, in the same way the development method
is interchangeable on the change side: this product does not care whether the
analysis was done in SQL, in a notebook, or in somebody's head, and it never
requires its own method. It requires only that the design be named where the
statement asserts causation.
Owner: the analyst. Status: ENFORCED by gate G4 for causal claims, UNENFORCED
for everything else.

### provenance, standing in the shared stage `provenance`
What was queried, fitted, and assumed. The claim-unit form of execution
provenance. Source identity is recorded at check time.
Owner: BrotherDS. Status: ENFORCED. Known ceiling: source identity is size and
modification time, not a content hash, so a source rewritten in place at
identical size is not detected.

### receipt, standing in the shared stage `passport`
The claim receipt: the page a person reads instead of the spreadsheet. It is the
seam artifact of this product, and it is the exact structural parallel of the
change passport. It carries the claim, its origin class, what re-derived it, its
uncertainty, and what it does not establish.
Direction of travel, the same law as the passport: produced where the analysis
happens, consumed where the decision happens, never travels back. Anything the
decision needs that the receipt does not carry is a defect in the receipt, not a
licence to reach into the analyst's session.
Owner: BrotherDS. Status: ENFORCED, `bds.py receipt`.

### refusals, standing in the shared stages `required-proof` and `evidence-integrity`
The ten gates. This stage stands in two of BrotherSBE's eight concerns and only
two. The other six, behaviour, business impact, risk, accountability, release
readiness and production observation, remain BrotherSBE's on the change unit and
are not reimplemented here.
Where BrotherSBE's data reviewer already covers grain, fan out, keys, system of
record, reconciliation, freshness and cost ON A CHANGE, BrotherDS consumes that
verdict rather than rebuilding it.
Owner: BrotherDS. Status: ENFORCED, ten gates, verdicts PASS, FAIL and NO-DATA
matching BrotherSBE's tuple exactly.

### human-decision, standing in the shared stage `human-decision`
Act on this number, or do not. Unconditional, and the same stage the other two
products stop before.
Owner: a person. Status: this is a refusal, not a feature.

### decision-taken, standing in NO shared stage
The claim-unit parallel of RELEASE. The shared chain deliberately leaves
`release` out of the stages an item of work may serve, because the host performs
it and both products stop short. The same holds here: a person takes the
decision, and no item of BrotherDS work may claim to serve this stage.
`bds.py stage decision-taken` returns FAIL by name, which is the point.
Owner: a person. Status: ENFORCED as a refusal.

### outcome, standing in the shared stage `production-observation`
What actually happened. A realised value, the person who observed it, and the
date. The `observed_by` field is not decoration: it is the fourth human decision
point, the acceptance that closes the chain.
Owner: a person observes it; BrotherDS records it. Status: ENFORCED, `bds.py
score`.

### verified-reality, standing in the shared stage `verified-reality`
The outcome fell inside the stated uncertainty, or it did not. Three states:
HELD, MISSED, and UNSCOREABLE for a claim that stated no interval, which cannot
count either way.
Owner: RECOMMENDED to BrotherDS, FOUNDER-GATED. See
`docs/BOUNDARY-STUDY-2026-08-16.md`. The shared chain's own status table records
this stage as owned by nobody, and this product's north star is the only metric
in the triumvirate that measures whether reality agreed. Those are the same fact
seen from two sides. The founder ratifies it or he does not.
Status: ENFORCED in code, `bds.py ledger`. Reporting NO-DATA today, because no
claim has resolved yet.

---

## The north star, at the last stage

**Verified Claim Rate.** Of the decision-grade claims scored against reality,
the share whose realised outcome fell inside the uncertainty stated at the time.

Numerator: claims that resolved and held. Denominator: claims that resolved. A
claim that has not resolved is in neither and is reported separately.

It cannot be gamed by producing more claims, because producing more only
enlarges the denominator. It punishes overconfidence directly, because a narrow
interval that reality falls outside is a miss. And it is a statement about the
world rather than about proof, which is the reason this product is allowed to
both do the analysis and judge it: the judge is reality, and self-assessment
never passes.

**It reports nothing until claims resolve**, which is one to three months for a
monthly figure. Two leading indicators carry the wait, and neither is ever
presented as the north star: defects found, and claims that changed a sentence.

---

## What is enforced, and what is not

Following the rule that a limit without a file behind it must say so.

ENFORCED, with the check that decides it:

| Rule | Check |
|---|---|
| A stage this chain does not hold is a hard error | `bds.py stage`, selftest |
| The stage a person takes may not be served by an item | `bds.py stage decision-taken`, selftest |
| This document and `bds.py`'s CHAIN may not drift | selftest parses the block above |
| Every servable stage stands in a shared stage | selftest |
| An absent passport field is NO-DATA, a padded one is FAIL | `bds.py passport`, selftest |
| The handoff reader may not certify an unratified shape | `bds.py handoff`, selftest |
| A claim with no stated interval cannot count toward the rate | `bds.py score`, UNSCOREABLE |

UNENFORCED, stated plainly:

- **Nothing forces a claim to exist.** An analyst who does not write one is not
  caught. The engine checks claims offered to it.
- **Nothing forces an item of work to name its stage.** The checker exists and
  nothing calls it over a backlog, because this product has no queue file.
  BrotherMode enforces the equivalent through its own idle checker over its
  queue. Until BrotherDS has a queue, this rule is a discipline.
- **Nothing verifies that BrotherDS reads only the passport** and nothing else
  under `.sbe/`. The consumer takes a single path and reads that path, which is
  true by inspection and not by control.
- **The `not_established` field is checked for being non-empty, never for being
  honest.** One lazy line passes.

---

## What would disprove this chain

1. **A tenth stage turns out to be needed.** If a real claim cannot be placed in
   any of the nine stages above without distortion, the thesis that BrotherDS
   adds no stage is wrong, and the mapping is a convenience rather than a truth.
2. **The receipt does not transfer trust.** If a person reads a receipt and still
   asks for the underlying file before believing the number, the seam artifact
   has failed at its only job.
3. **Nothing ever resolves.** If no claim reaches an outcome within 45 days, the
   last two stages are decorative and the north star has no fuel.
