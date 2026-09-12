# How BrotherDS integrates with the other two, natively

Status: DESIGNED. Three items in it are REQUESTS to the sibling products rather
than decisions this repository may take alone, and they are marked REQUEST.
Date: 2026-08-16.

---

## The answer in one paragraph

Native means the third product occupies the chain that already exists rather
than running beside it. Concretely: BrotherDS adds no stage, mints no second
notion of "verified", keeps no second project record, and rebuilds none of the
data checks BrotherSBE already ships. It meets each sibling at exactly one
document, in one direction, and it fills the one stage at the end of the chain
that the founder-authoritative chain document records as owned by nobody. Where
that requires the siblings to change something, this file asks rather than
assumes, because they are separate projects with their own canonical roots.

---

## The four seam artifacts

Every seam in this triumvirate is a document, travelling one way. Nothing
travels back. Anything a downstream side needs that its document does not carry
is a defect in the document, never a licence to reach into upstream state.

| Artifact | From | To | Status |
|---|---|---|---|
| Change passport | BrotherMode | BrotherSBE | exists, three of five fields produced |
| Change passport, second read | BrotherMode | BrotherDS | REQUEST, see below |
| Handoff package | BrotherSBE | BrotherDS | content contracted, wire format never ratified |
| Claim receipt | BrotherDS | the person deciding | exists, `bds.py receipt` |
| Outcome record | the person observing | BrotherDS | exists, `bds.py score` |

The shape of it: the first two products hand a change along until a person
releases it. BrotherDS hands a claim along until a person acts on it. Then one
artifact closes both, because the outcome record is the only thing in the
triumvirate that reports what reality did.

---

## What BrotherDS reads, and what it may never read

**From BrotherMode: `<root>/.sbe/passport.json`, and nothing else.**

The five fields, exact spelling: `whatWasDone`, `whoDidIt`, `whatWasRun`,
`whatWasNotEstablished`, `whereItCameFrom`.

BrotherDS applies the producing side's own hollow-value rule verbatim rather
than inventing a third notion of "the field is present": an empty string, a
whitespace-only string, an empty list or null all read as absence, while 0 and
False are real answers. It separates two cases the producing side deliberately
distinguishes:

- a field ABSENT from the deposit is NO-DATA, because the producer omits what it
  cannot establish honestly;
- a field PRESENT and empty is a FAIL, because padding a field to look filled is
  exactly what the producing side's rule forbids.

Today a real deposit carries three of the five fields, so the honest verdict
against a live passport is NO-DATA. That is correct and is not a defect in this
reader.

**Never read:** `.brothermode/store.sqlite3`, `.sbe/tasks.json`, `.sbe/evidence/`,
`STATE.md`. The first is execution state, and the chain's own law is that
reaching into it to fill a missing passport field is the failure the seam exists
to prevent. The last is a generated view and not truth, by its own producer's
docstring.

**From BrotherSBE: the five-item handoff package.**

Contracted 2026-08-11: `dataset` with its grain, contract and snapshot id;
`evaluation_harness` with its split definition; `metric_definitions` by name and
formula; `labelled_holdout` with who labelled it and when; and `open_questions`,
stated rather than guessed. Anything not in the package is not handed over.

No wire format was ever ratified for it. This repository therefore proposes a
shape and refuses to certify it: `bds.py handoff` caps its own verdict at
NO-DATA for any package lacking a `ratified` marker, however complete the
package is. A tool that certified a contract nobody signed would be committing
the overclaim this whole product exists to refuse.

---

## One notion of verified, not two

BrotherDS uses BrotherSBE's verdict tuple exactly: PASS, FAIL, NO-DATA. NO-DATA
is never a pass and never a block. This is not a courtesy, it is the point: two
products with two different meanings of "verified" is the precise failure the
triumvirate was built to prevent.

BrotherDS does not implement WAIVED. That word belongs to gate aggregation on the
change side, and this product has no waiver mechanism to attach it to. If one is
ever added, it takes that spelling and that meaning, or it takes a different
word entirely.

---

## What BrotherDS must not rebuild

The line, from the founder's 2026-08-11 decision: BrotherSBE does everything
around the model and never the model; BrotherDS owns the model and the claim.

On a CHANGE, BrotherSBE's data reviewer already covers grain, join fan out, keys
and integrity, system of record, reconciliation with a genuinely independent
second derivation, temporal correctness, money semantics, freshness and quality,
and cost and performance. BrotherDS consumes those verdicts and does not
reimplement them.

The one thing that looks like duplication and is not: BrotherDS checks grain on
a CLAIM. That is a different object. A change can pass every grain check at
review time and a claim computed on the same table a month later can still be
built on a key that is not a key, because nothing changed and so nothing was
reviewed. The pilot estate proved that on the first night of use.

Verification of analytics claims routes through BrotherSBE's existing evidence
auditor, extended with analytics cases, rather than through a new verifier
minted here. Reusing the strongest control beats minting a second one.

---

## The three requests to the sibling products

Written as requests, not applied as patches. Each names the file that would
change and the project that owns it.

**REQUEST 1, to BrotherMode and BrotherSBE: permit a second, read-only consumer
of the change passport.** The seam specification says the passport is produced
by BrotherMode and consumed by BrotherSBE. BrotherDS reads the same deposit and
never writes it, never reaches past it, and never asks for a field to be added
on its behalf. Owner of the change: `docs/specs/2026-08-15-change-passport-seam.md`,
which lives in the BrotherSBE repository. Until it is granted, this reader is
built and unauthorised, which is the honest state.

**REQUEST 2, to BrotherSBE: ratify a wire format for the handoff package.** The
five-item content is decided; the field names, file format and location are not.
The shape this repository reads is a proposal and says so in its own output.
Ratifying it costs one decision and unblocks a reader that already exists.

**REQUEST 3, to the founder: assign the `verified-reality` stage.** The shared
chain ends there and its own status table records the stage as owned by nobody.
BrotherDS's north star is the only metric in the triumvirate that measures
whether reality agreed. The case is in `docs/BOUNDARY-STUDY-2026-08-16.md`. This
is a founder decision, not an implementer's.

A fourth possibility, cheaper than all three and worth naming: BrotherMode's
`tools/toolkit_routes.json` is founder-editable by design and is the one genuine
data-driven extension point in that codebase. Registering BrotherDS there as a
routed capability requires no core edit in either sibling. It routes task classes
to capabilities rather than chain stages, so it does not satisfy requests 1 to 3,
but it is the shortest path to the two products knowing this one exists.

---

## Plain language, per the terminology law

The user never needs the internal terms to use this product. Internal terms
appear only when somebody has asked for the advanced view, and the next default
output returns to plain wording. Plain wording is not a euphemism: it states the
same fact.

| Internal | What is said to a person |
|---|---|
| claim | one number that reaches a decision |
| claim receipt | the page you read instead of the spreadsheet |
| origin class | where the number came from |
| not_established | what this number does not tell you |
| gate | a refusal |
| NO-DATA | nobody measured this, so it is neither proved nor disproved |
| verified claim rate | how often reality agreed with what we said |
| UNSCOREABLE | no range was stated, so reality cannot score it either way |
| decision-taken | the moment a person acts on the number |

---

## Enforced and unenforced

ENFORCED, with the check that decides it:

| Rule | Check |
|---|---|
| This document names every field the reader reads | selftest, against `PASSPORT_FIELDS` and `HANDOFF_ITEMS` |
| The chain mapping matches the code | selftest, against `docs/NORTH-STAR-CHAIN.md` |
| The isolation law: the engine may not name a path on the far side of the seam | selftest scans its own source |
| An absent passport field is NO-DATA; a padded one is FAIL | selftest |
| An unratified package shape cannot be certified | selftest |

The isolation check was calibrated by reinjection on 2026-08-16: adding a
reference to the execution store in a scratch copy made it fail by name, and it
passes on the tree as it stands. Its needles are assembled from pieces rather
than written whole, so the scan cannot match its own pattern.

UNENFORCED, plainly:

- The scan covers `bds.py` only. A second module added later is outside it until
  the scan is widened, and nothing widens it automatically.
- Nothing forces this repository to actually consume a passport before making a
  claim. The reader exists; using it is a discipline.
- Requests 1 and 2 above are unanswered, so the passport reader is built and
  unauthorised, and the handoff reader is built against a shape nobody ratified.
  Both states are reported honestly by the tools themselves rather than hidden.
