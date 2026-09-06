# Extractor notes, JBEQ-MDM blind round 7 (2026-09-06)

Files opened before any answer file was read or any decision made (blind phase):

- benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
- benchmarks/jbeq/mdm/fact-sheet-schema.json
- benchmarks/jbeq/mdm/prompts/AD-01.md through AD-10.md (10 files)
- benchmarks/jbeq/mdm/prompts/EO-01.md through EO-10.md (10 files)
- benchmarks/jbeq/mdm/prompts/HI-01.md through HI-10.md (10 files)
- benchmarks/jbeq/mdm/prompts/ID-01.md through ID-05.md (5 files)
- benchmarks/jbeq/mdm/prompts/MM-01.md through MM-10.md (10 files)
- benchmarks/jbeq/mdm/prompts/RQ-01.md through RQ-05.md (5 files)
- benchmarks/jbeq/mdm/prompts/SV-01.md through SV-10.md (10 files)
- benchmarks/jbeq/mdm/prompts/TM-01.md through TM-10.md (10 files)

70 case files plus the two spec files. No seed file, no runs directory, no regression
sheet, no baseline, no decision-rules-addendum, no generalization/identity-class/unseen
file, no scripts/jbeq_decide.py, and no test was opened before this notes file and
fact-sheets.json were written.

## Track vocabularies observed (copied verbatim per case, not assumed)

- address, entity-object, hierarchy, identifier, match-or-no-merge: all use the
  same 7-way AUTO-MERGE .. NO-DATA vocabulary in every case this round (hierarchy
  cases in this round all carry the full 7, not the 4-way subset shown in the
  schema's own worked example for that track).
- requirements (RQ): DECIDED / ASSUMED / INFERRED / UNKNOWN. Not a merge decision
  at all; it classifies whether an extracted rule is a decided fact, an assumption,
  an inference, or unknown. None of the merge-specific fields apply; every RQ sheet
  carries the schema defaults (object_type_a/b default to legal_entity purely to
  satisfy "always present", they carry no meaning for this track).
- survivorship (SV): corporate registry / tax authority / ERP / CRM / sales master /
  MDM / manual override / NO-DATA. A source-of-truth selection, not a merge
  decision. Populated contradicted_attributes when two sources genuinely disagree
  on a value, blank_fields when a value or its provenance metadata is missing, and
  effective_dates when the case states an override or registry-change date. Left
  everything else (stated_relation, tenant_boundary, hierarchy_parents, evidence_*)
  at schema defaults since those fields describe merge evidence, not source
  authority, and forcing them would be inventing facts the input doesn't state in
  those terms.
- temporal (TM): R1 / R2 / (R3 on TM-04 only) / NO-DATA. A which-record-is-
  authoritative-as-of-a-date question. Populated effective_dates (as_of = the
  query date, candidate_effective_date = the record transition date stated),
  lifecycle where a closed/reused/superseded state is explicit, history_exists
  where transaction history is explicitly at stake. Left contradicted_attributes
  empty for ordinary valid_from/valid_to succession (a name or terms change across
  two temporally sequential records is lifecycle succession, not a same-time
  contradiction) even where the same underlying rename appears elsewhere in the
  corpus as a match-or-no-merge case (MM-07 and TM-02 share the same rename
  scenario; TM-02 is framed as pure temporal authority, so no
  contradicted_attributes there).

RQ, SV and TM are the tracks scripts/jbeq_decide.py does not decide (to be
confirmed against UNSUPPORTED_TRACKS in phase 2); their sheets are shaped to be
schema-valid without inventing merge semantics the case text does not support.

## Off-vocabulary judgment calls (picked closest enum value, per the prompt's own
## instruction, rather than inventing a new one)

- HI-05 (franchise relation): the input states a brand/franchise relationship
  explicitly WITHOUT any capital tie or shared officers. None of stated_relation's
  seven values name a franchise/brand-license relation. Since the input's own
  emphasis is the ABSENCE of a capital relation, and none of the positive relation
  values fit a bare brand license, stated_relation is "none" here; the franchise
  fact itself is carried by the requested_action/requested_relation_type fields
  (a request to write it into the capital hierarchy anyway) and stated_difference
  = different_legal_entity.
- HI-07 ("group company" with no stated hierarchy type): stated_relation set to
  "parent_child" as the closest fit for a stated but untyped hierarchy claim;
  hierarchy_parents is left empty rather than guessing capital/trade_flow/
  reporting, and blank_fields carries "hierarchy_type" to flag that the type
  itself is exactly what the input withholds.
- HI-09 (two same-named hierarchy nodes with partially overlapping jurisdiction):
  no stated_difference value names a partial-jurisdiction-overlap fact. Used
  contradicted_attributes = ["governed_prefecture_range"] instead, since both
  nodes state a governed-area attribute and the stated values disagree (rule 4),
  which is the closer fit than forcing stated_difference to a value that doesn't
  describe this.

## Rule-4 (contradicted_attributes) calls worth flagging

- MM-06 (two different kanji spellings of the same company name, both read the
  same way, same city but different banchi, both corporate numbers unobtained):
  this is the extractor prompt's own two worked examples for rule 4 (a kanji
  variant with the same reading, and a lot number that differs) landing on ONE
  case simultaneously. contradicted_attributes = ["company_name_kanji_variant",
  "lot_number"]. Both corporate numbers being unobtained (not merely blank on
  one side) drives evidence_strength = weak, evidence_reasons =
  [missing_identifier], authoritative_identifier = absent.
  location_comparison stays null because MM is not the address track (the rule
  restricting that field to address-track cases is followed even though the case
  narrates location facts).
- EO-10 (full-width vs half-width company name notation, otherwise identical
  corporate number/role/billing): NOT flagged as a contradicted attribute. The
  case's own wording ("differs ONLY in notation") signals this is the same
  notation-variant class as the AD address cases, not a rule-4 disagreement
  (which the prompt illustrates with a genuine kanji VARIANT, i.e. different
  characters with the same reading, not the same characters in a different
  character width).
- MM-07 (two trade names for the same corporate number, registered trade-name
  change dated 2025-04-01): flagged contradicted_attributes = ["company_name"]
  per rule 4's own text ("whether or not a self-contradiction also exists"), and
  separately flagged evidence_reasons = ["explained_conflict"] because the
  registry date fully explains the name difference as a legitimate rename, not a
  sign of two different objects.

## HI-02 and HI-03 (named in the dispatch as needing a fresh extraction)

- HI-02: existing capital hierarchy is stated (a subsidiary company under a
  parent holding company). The request asks to REVERSE which side is parent
  within that SAME hierarchy type (capital), purely because the subsidiary's
  sales now exceed the parent's -- a reporting-convenience argument, not a
  change in actual capital ownership. Recorded requested_action = assignment,
  requested_relation_type = capital, and populated the round-6 optional
  requested_parent with the subsidiary's name (the entity the request wants
  promoted to parent), per the rule that requested_parent applies exactly when a
  stated hierarchy TYPE already exists and the request asks to flip its
  DIRECTION.
- HI-03: a business transfer effective 2026-07-01 moved 3 stores from an old
  parent to a new parent; revenue booked before that date was aggregated under
  the old parent, and the question is whether pre-transfer hierarchy rows may
  keep the old parent. Recorded effective_dates.candidate_effective_date =
  "2026-07-01" with conflict = false (a clean cutover, not two disagreeing dates
  about what governs today: the ask is whether the OLD, pre-cutover rows may
  correctly keep the OLD parent, which the effective date makes unambiguous).
  requested_action stays "none" since the question is about historical row
  correctness, not about writing a new assignment. No specific old/new parent
  company names are given in the input (only generic "old parent
  corporation"/"new parent corporation" labels), so hierarchy_parents carries
  only the generic old-parent label rather than inventing a name.

## Other notable calls

- AD-08 (a city name that exists in two different prefectures; record A's
  prefecture is blank, record B states the prefecture): blank_fields =
  ["prefecture"], corroborating fact = the matching town name and lot number
  between A and B (present but not confirming, since the input never states
  whether that town name also exists under the other prefecture's namesake
  city). location_comparison left null: none of the five enum values describe
  an unresolved multi-prefecture ambiguity, and forcing one would misstate the
  fact.
- ID-01 and ID-04 both hinge on a purely coincidental digit match across two
  DIFFERENT identifier numbering systems that the input explicitly says differ
  (an internal sequential customer number vs a corporate number; a store-ID
  scheme vs a site-ID scheme sharing the same digit format). Both get
  stated_difference = different_identifier_domain, evidence_strength = weak,
  authoritative_identifier = absent.
- EO-05's second role has no object_type enum value for "supplier/vendor"; used
  the generic "commercial_account" for both sides rather than inventing a new
  enum value, per the prompt's own guidance to prefer an existing field value
  that keeps the case answerable.
- SV-05/SV-06 (manual override lifecycle): the schema has no explicit "expiry"
  slot; recorded candidate_effective_date as the override's/registry change's
  own effective date and noted the actual expiry dates here in prose (SV-05
  override expires 2026-12-31, still active at the stated "now" of 2026-09-05;
  SV-06 override already expired 2026-03-31, before the stated "now" of
  2026-09-05).
- TM-09 has a genuine coverage GAP: the queried date (2026-03-15) falls after
  R1's valid_to (2025-12-31) and before R2's valid_from (2026-04-01), i.e.
  neither record's stated window covers it (the store was suspended). There is
  no schema field for a coverage gap as distinct from a two-date conflict, so
  this is recorded here in prose rather than forced into
  effective_dates.conflict (conflict is defined as two dates disagreeing about
  which governs today, not as neither covering it). lifecycle = "closed"
  captures the suspended state.
