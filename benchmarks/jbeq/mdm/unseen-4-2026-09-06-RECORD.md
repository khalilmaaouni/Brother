# JBEQ-MDM unseen-4-2026-09-06, authorship record

## Authorship

Authored blind, in one pass, with no access to any ground truth or any prior
unseen set's content. Every company, store, person, address and identifier in
this file is invented; no real corporation, no client term, and
no team member name appears anywhere in it. No em or en dashes were used.

## Files opened

- `benchmarks/jbeq/README.md`
- `benchmarks/jbeq/mdm/fact-sheet-schema.json`
- `benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md`
- `benchmarks/jbeq/mdm/decision-rules-addendum.md`
- `scripts/jbeq_mdm.py`, restricted by `grep -n` and `sed -n` to: the
  `決定語彙` / `VOCABULARY` block, `CANONICAL_TRACKS` and its comment,
  `load_seed`, and `write_prompts` (plus enough of `score`/`cmd_score` to see
  which case keys those two functions actually read: `id`, `track`, `input`,
  `question`, `allowed`, `expected`, `critical`, `critical_class`, and the
  top level `scoring.merge_answers` key; this was necessary to match the
  loader's own contract rather than guess at the shape).
- `~/.claude/evidence/FIX-DIRECTIVE-2026-09-06.md`, sections 15 to 22.
- `~/.claude/evidence/audit-unseen-set-3-2026-09-06.md`, read in full to
  locate its structural/design content (the file has no headings; its
  paragraphs mix specific case ids, phrases and company names from set 3
  with general design lessons). Only the general lessons were used below;
  no case id, case phrase, company name or answer from that file appears
  anywhere in this set.

## Files not opened

`benchmarks/jbeq/mdm/seed-2026-09-05.json`, `unseen-2026-09-06.json`,
`unseen-2-2026-09-06.json`, `unseen-3-2026-09-06.json`, any `*-RECORD.md`
other than this one, anything under `benchmarks/jbeq/mdm/runs/` or any
`*-prompts` directory (other than the one this task generated from this
file), any generalization or identity-class file, `scripts/jbeq_decide.py`,
and any test file.

## Design

Eight canonical tracks (`address`, `entity-object`, `hierarchy`,
`identifier`, `match-or-no-merge`, `temporal`, `survivorship`,
`requirements`), five cases each, 40 total, ids `W4-01` to `W4-40`.

Design lessons carried in from the FIX-DIRECTIVE sections and the audit's
structural findings, applied without reusing any specific case:

- Legal identity is not operational-object identity: several cases put a
  shared, valid corporate number on two records that are stores, accounts,
  or roles rather than the legal entity itself, so the correct answer links
  the relation instead of collapsing the records (directive section 17).
- AUTO-MERGE needs strong, unexplained evidence and no confirmation reason;
  medium evidence, an unvalidated crosswalk, or a confirmation reason
  (history, irreversibility) downgrades to SUGGEST MERGE; weak evidence (a
  match score alone, or a missing identifier) never merges regardless of
  urgency or irreversibility (directive section 18, addendum rule 9). Nine
  critical cases expect AUTO-MERGE or SUGGEST MERGE so a coward engine that
  refuses to ever merge fails them.
- A merge question always offers the merge answers in its allowed list, so
  the set cannot be passed by an engine incapable of merging, and cannot be
  passed by one that merges reflexively either.
- REJECT MATCH requires a fact that actually refutes identity (a different
  valid corporate number, a stated tenant boundary, a stated reused
  identifier, a different identifier domain); weak or merely absent support
  stays KEEP SEPARATE (addendum rules 2, 6, 10). Nine cases propose a match
  or a hierarchy write on an explicitly stated ground and expect REJECT
  MATCH once a refuting fact is present, so a shallow acceptance of whatever
  basis is offered fails them.
- No single lexical tell should predict an answer: proposal wording, blank
  fields and merge language were varied across tracks and labels rather
  than clustered on one answer, and no case states its own conclusion in
  its input (no "this is merely a coincidence" style sentence). A blind
  audit on 2026-09-06 found this claim false as first written (four inputs
  used the words for "coincidentally" or "merely" to announce their own
  conclusion); the post-audit pass reworded all four to state the same fact
  without the tell, and the claim now holds, see "Corrections after blind
  audit" below.
- The ESCALATE versus NO-DATA boundary is exercised both ways: NO-DATA cases
  simply never state the deciding fact anywhere, without announcing the gap
  in words; separate cases carry an unexplained internal conflict and expect
  ESCALATE rather than NO-DATA, since a conflict is something to judge. The
  same audit found this claim false as first written (both NO-DATA inputs,
  W4-15 and W4-25, carried a clause announcing the absence outright); the
  post-audit pass deleted those clauses so the gap is simply never filled
  rather than announced, and the claim now holds.
- Independent hierarchy types (capital, trade-flow, reporting) can each
  carry a different valid parent without that being one conflict to resolve
  to a single field; forcing one choice erases a true relation, so those
  cases expect LINK AS RELATED, not ESCALATE or KEEP SEPARATE (addendum
  rule 11).
- Identifier reuse after closure or dissolution, and numeric coincidence
  across different identifier domains, both invalidate a proposed match on
  that code alone (addendum rule 10; the fact-sheet schema's own
  `authoritative_identifier: conflicting` worked example, read as an
  allowed file).
- Every critical case carries a `critical_class` from the fixed list in
  `benchmarks/jbeq/README.md` section 28 (`WRONG STORE ASSIGNMENT`,
  `CROSS-CUSTOMER CONTAMINATION`, `HISTORICAL REASSIGNMENT`, `WRONG PAYER`,
  `WRONG LEGAL ENTITY`, `WRONG TAX IDENTITY`,
  `UNREVERSIBLE MERGE WITHOUT EVIDENCE`, `HIERARCHY REVERSAL`,
  `SOURCE PRECEDENCE VIOLATION`, `CROSS-TENANT DATA LEAK`); no non-critical
  case carries one.
- Inputs are written as a ledger entry, a registry excerpt, a closure memo,
  a governance policy quotation, or a dispute note would state them, never
  as an exam question, and no question repeats another question's wording.
- Industries were varied across a small deliberately invented pool (a
  fishery cooperative, a regional bus operator, a school uniform maker, a
  dental clinic group) with a distinct sentence structure and a distinct
  scenario per case, rather than one template reused with names swapped.
- No identifier value (corporate number, internal code, invoice number,
  lot id) is reused across two different invented companies except inside
  the cases that are specifically about identifier reuse or coincidence
  (`W4-16`, `W4-17`, `W4-18`, `W4-39`).

## Counts

- 40 cases, 5 per canonical track (`address`, `entity-object`, `hierarchy`,
  `identifier`, `match-or-no-merge`, `requirements`, `survivorship`,
  `temporal`), verified by `scripts/jbeq_mdm.py`'s own `CANONICAL_TRACKS`.
- 32 of 40 critical (8 non-critical, one per track).
- 9 critical cases expect AUTO-MERGE or SUGGEST MERGE (at least 6 required).
- 9 cases (by count of REJECT MATCH answers reached through a stated
  proposed basis, several phrased with an explicit grounding clause and
  several with the grounding stated in substance) expect REJECT MATCH after
  a match, link, or write was proposed on a stated basis; a simple substring
  check for the connector "根拠に" alone already finds 7 of these (at least
  6 required either way).
- Record-write shaped questions (asking to move or overwrite data other
  than a hierarchy parent): `W4-03`, `W4-17`, `W4-31` (at least 3 required);
  `W4-13` and `W4-38` additionally ask to write a hierarchy parent, a
  related but distinct write shape.
- Relocation or same-area-renamed cases: `W4-21`, `W4-22`, `W4-24`, `W4-26`
  (at least 4 required).
- Identifier reuse or cross-domain coincidence cases: `W4-16`, `W4-17`,
  `W4-18`, `W4-39` (at least 3 required).
- Exactly 2 NO-DATA cases: `W4-15`, `W4-25`.
- Label distribution after the post-audit corrections: REJECT MATCH 12,
  LINK AS RELATED 6, AUTO-MERGE 7, KEEP SEPARATE 6, ESCALATE 5,
  SUGGEST MERGE 2, NO-DATA 2 (W4-32 moved from SUGGEST MERGE to AUTO-MERGE,
  see "Corrections after blind audit" below).
- Label distribution after the 2026-09-06 founder-ruling corrections below:
  REJECT MATCH 12, LINK AS RELATED 4, AUTO-MERGE 6, KEEP SEPARATE 6,
  ESCALATE 5, SUGGEST MERGE 5, NO-DATA 2 (W4-21 and W4-24 moved from LINK AS
  RELATED to SUGGEST MERGE, W4-26 moved from AUTO-MERGE to SUGGEST MERGE;
  see "Corrections after the 2026-09-06 founder rulings" below).
- Every `expected` value is inside that case's own `allowed` list (0
  violations); every `track` is one of `CANONICAL_TRACKS` (0 violations); 0
  duplicate ids; every critical case carries a `critical_class` and no
  non-critical case does (0 violations either way).
- `python3 scripts/jbeq_mdm.py prompts benchmarks/jbeq/mdm/unseen-4-prompts
  --seed benchmarks/jbeq/mdm/unseen-4-2026-09-06.json` wrote 40 files; a
  grep for `expected`, `rationale`, or `critical_class` across all 40
  prompt files returns 0 hits; a grep for the em or en dash across this file
  and the seed json returns nothing.

## Corrections after blind audit

A blind audit (`~/.claude/evidence/audit-unseen-set-4-2026-09-06.md`, opus
reviewer, hub PR 441) scored 33 of 40 exact (35 of 40 applying the founder's
2026-09-06 ruling on rule 11), all five disagreements critical, and flagged
five structural holes plus two false design claims. Every correction the
audit named in its section 5, plus the two structural holes it named without
a correction line (the W4-01/W4-36 duplicated mechanism and the three track
mismatches), landed in this post-audit pass. Thirteen seed corrections in
total, touching twenty-two cases once every input, question, allowed list,
expected value, critical_class, and rationale edit is counted individually:

- `W4-13`: input rewritten so the trade-flow parent is stated as the
  requested entity (Chubu sales block) itself rather than routed through a
  third-party warehouse, so addendum rule 11 applies directly; rationale
  rewritten to match; critical_class corrected from WRONG STORE ASSIGNMENT
  to HIERARCHY REVERSAL. Expected (LINK AS RELATED) unchanged.
- `W4-26`: input's post-move record filed at the old address deleted, so
  the AUTO-MERGE rests on clean, unexplained evidence instead of an
  unaddressed contradiction; rationale rewritten to match. Expected
  (AUTO-MERGE) and critical_class (HISTORICAL REASSIGNMENT) unchanged.
- `W4-32`: expected corrected from SUGGEST MERGE to AUTO-MERGE, matching
  W4-34's mechanism (an authoritative-versus-non-authoritative attribute
  conflict is a survivorship question, never an enumerated confirmation
  reason under rule 9); rationale rewritten to match.
- `W4-04`: rationale rewritten to rest the refutation on the stated
  separate legal entity holding the payer role, not on the settlement
  account and credit line, which rule 7 treats as a relationship signal
  rather than a refutation.
- `W4-15`, `W4-25`: input shortened so the NO-DATA gap is simply never
  filled, rather than announced in words ("他に手がかりとなる記載が一切無
  い", "郵便番号その他、地域を裏付ける記載も無い" both deleted); rationale
  of `W4-15` rewritten to match.
- `W4-30`, `W4-35`: input reworded so the coincidence is inferred from a
  stated fact rather than announced by the word for "coincidentally"
  (`たまたま`, `偶然`); `W4-35` additionally rewritten in full (see the
  track-mismatch correction below).
- `W4-06`, `W4-18`, `W4-20`, `W4-37`: input reworded to drop the remaining
  literal occurrences of the same conclusion-announcing tells (`偶然`,
  `にすぎず`, `記載も無い`) that the audit's own recount turned up beyond
  the four cases it named by id, so no input anywhere carries one.
- `W4-23`: input gains a stated separate tenant on each floor ("テナントA",
  "テナントB"), so the CROSS-TENANT DATA LEAK class and the REJECT MATCH
  answer rest on a stated fact rather than an inferred building convention;
  rationale rewritten to match.
- `W4-21`, `W4-24`: allowed list widened to include AUTO-MERGE and SUGGEST
  MERGE, since both ask an identity question with no affirmative merge
  answer previously offered. Expected (LINK AS RELATED) unchanged.
- `W4-37`: input's role gloss corrected from `出荷先(sold-to)` to
  `販売先(sold-to)`, since `出荷先` is ship-to, glossed correctly elsewhere
  in the set at `W4-04`.
- Six critical_class corrections, each case's own class read as the harm of
  the answer the seed itself expects rather than a class the case's actual
  mechanism supports: `W4-07` WRONG TAX IDENTITY to UNREVERSIBLE MERGE
  WITHOUT EVIDENCE (no tax identifier is in play); `W4-12` SOURCE
  PRECEDENCE VIOLATION to HIERARCHY REVERSAL (no source hierarchy is in
  play); `W4-13` WRONG STORE ASSIGNMENT to HIERARCHY REVERSAL (a reporting
  parent, not a store, is in play); `W4-19` WRONG PAYER to CROSS-CUSTOMER
  CONTAMINATION (no payer role is in play); `W4-28` SOURCE PRECEDENCE
  VIOLATION to HISTORICAL REASSIGNMENT (no authoritative source is named);
  `W4-29` HISTORICAL REASSIGNMENT to UNREVERSIBLE MERGE WITHOUT EVIDENCE (no
  reassignment is stated in the case). Every corrected class is one of the
  ten in this file's own top-level `critical_classes` list.
- Two false design claims restated (see the "Design" section above): "no
  case states its own conclusion" and the NO-DATA cases' claim of "without
  announcing the gap in words" were both false as first written; both now
  hold after the corrections above.
- Three track mismatches, each fixed by rewriting the case's content to fit
  its existing track rather than moving the case to a different track
  (which would have broken the five-per-track count): `W4-03` rewritten
  from a closed-store/new-store temporal narrative into an entity-object
  case (one legal entity's corporate number shared by two distinct store
  objects, each keeping its own warranty-claim history by the ledger's own
  note), keeping track `entity-object`, critical_class HISTORICAL
  REASSIGNMENT, and expected REJECT MATCH; `W4-19` rewritten from a generic
  corporate-number match into an identifier-track case (a partial, unverified
  four-digit suffix match against a full corporate number), keeping track
  `identifier` and expected SUGGEST MERGE (critical_class corrected above);
  `W4-35` rewritten from an identifier-coincidence narrative into a
  survivorship case (an authoritative and a non-authoritative source
  disagree on one attribute, but no fact states the two records are even
  the same patient, so source priority never comes into play), keeping
  track `survivorship`, critical false, critical_class null, and expected
  KEEP SEPARATE.
- `W4-01`/`W4-36` duplicated mechanism: both cases shared the exact
  sentence "それぞれ別の設備台帳と別の従業員名簿を持つ" and the same
  fishery-cooperative, two-processing-sites archetype. `W4-36` rewritten in
  a different industry (a dental clinic group instead of the fishery
  cooperative) with a different sentence structure (a booking calendar and
  a chart numbering scheme instead of an equipment ledger and a staff
  roster, and a governance-requirement quotation rather than a plain
  ledger note), keeping its label (track `requirements`, critical_class
  WRONG STORE ASSIGNMENT, expected LINK AS RELATED) and its own corporate
  number 6070001112223, distinct from every other case's identifier.

After these corrections: `python3 scripts/jbeq_mdm.py prompts
benchmarks/jbeq/mdm/unseen-4-prompts --seed
benchmarks/jbeq/mdm/unseen-4-2026-09-06.json` was re-run against the
corrected seed; see the done-check output for the regenerated prompt count
and the leak/dash/banned-term scans.

## Corrections after the 2026-09-06 founder rulings

Two founder rulings, both 2026-09-06 at about 20:4x JST in the question UI,
corrected three of this set's labels as recorded seed defects (the HI-01 and
U-17 precedent: `label_corrected` names the ruling, the `expected` value
changes, the rationale is rewritten in the ruling's own words, nothing else
about the case changes). Neither ruling edits `scripts/jbeq_decide.py` in
this file's own commit history beyond what the ruling itself required; the
engine change for the first ruling lands beside these corrections.

- `W4-21`, `W4-24`: `expected` corrected from LINK AS RELATED to SUGGEST
  MERGE, `label_corrected` naming
  `docs/decisions/decision-p0-3-same-area-renamed-2026-09-06.json`. Ruling A:
  `location_comparison=same_area_renamed` (a street or town renaming with
  both records stating one facility) is a fact about the map, not
  confirmation the two registry rows are one, so it earns a merge with a
  person confirming, never an outright one; LINK AS RELATED is reserved for
  two different master data objects, which these inputs' own words deny.
  `scripts/jbeq_decide.py` rule L now answers SUGGEST MERGE for
  `same_area_renamed` (mutation id `renamed_area_needs_a_person`), so both
  cases move from a false disagreement (engine AUTO-MERGE, seed LINK AS
  RELATED) to a hit.
- `W4-26`: `expected` corrected from AUTO-MERGE to SUGGEST MERGE,
  `label_corrected` naming
  `docs/decisions/decision-p0-3-relocation-gate-2026-09-06.json`. Ruling A:
  the engine's lifecycle gate keeps its relocated arm; a relocation merge is
  always confirmed by a person, even when the registry states only the new
  address is valid and nothing else contradicts. `scripts/jbeq_decide.py` is
  unchanged by this ruling (the gate already answered SUGGEST MERGE); only
  the seed's label was wrong.

These three corrections are the whole of this set's re-score under
`benchmarks/jbeq/mdm/runs/regression-2026-09-06-u4-renamed-area/`, which
re-decides U4's own `fact-sheets.json` against the corrected seed with the
ruling's engine change in place; see that run directory's own RECORD for the
before/after table.

## Blind audit

Row M8 (~/.claude/evidence/reflection-measures-2026-09-07.md):
`scripts/unseen_set_gate.py` reads this section before `scripts/jbeq_mdm.py`
will `prompts` or `score` against this set. See
`unseen-3-2026-09-06-RECORD.md`'s own "Blind audit" section for the note on
why this heading is new as of this row.

Auditor: opus reviewer, 2026-09-06, PR 441. Full audit:
`~/.claude/evidence/audit-unseen-set-4-2026-09-06.md`.

Auditor scratch hash (sha256, written and locked before the first read of
`unseen-4-2026-09-06.json`):
`661d52d459becf6032719578a3ea887e97b41764806e49568aed236be958e460`.

Agreement: 35 of 40 (section 1: 33 of 40 exact, 35 of 40 applying the
founder's 2026-09-06 ruling that REJECT MATCH and KEEP SEPARATE score as
one class, which `scripts/jbeq_mdm.py`'s scorer already applies).

Corrections (answer-level, field `expected`), applied in "Corrections
after blind audit" above and verified against this file's own `cases`:
- W4-32: expected, SUGGEST MERGE to AUTO-MERGE (APPLIED)
