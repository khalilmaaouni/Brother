# JBEQ-MDM qualification U2, extractor notes (blind phase, 2026-09-06)

## Files opened during Phase 1 (blind), in order

1. benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
2. benchmarks/jbeq/mdm/fact-sheet-schema.json
3. benchmarks/jbeq/mdm/unseen-2-prompts/V-01.md through V-40.md (all 40, id order)

No other file under /private/tmp/bh-u2-inputs was opened. A directory listing
(`ls`) of benchmarks/jbeq/mdm/ was run once, before the read list above, to
locate the prompts subdirectory; only file and directory names were seen in
that listing, no file content.

## General method

Each case's TRACK, input passage, question and allowed-answer list were read
verbatim (the allowed-answer list is the same 7-value set on every one of the
40 files, regardless of track). Facts were extracted per the four rules in
EXTRACTOR-PROMPT.md and the identity-kind field guidance; no field was set
toward an expected final answer.

## Judgment calls worth flagging (closest-fit choices, per the extractor
prompt's own instruction to say so rather than invent a value)

- **requested_action classification.** The schema doc states requested_action
  stays "none" on most cases ("proposing nothing and requesting nothing").
  I read `match_on_stated_basis` and `record_write` as requiring an EXPLICIT
  NARRATED PROPOSAL EVENT in the input (an engine, batch, geocoder, script,
  or steward stated to have proposed/requested something on a named basis),
  not merely a generic "may these be merged / how should this be handled"
  question. Under that reading:
  - match_on_stated_basis: V-04 (dedup engine, reading match), V-09 (dedup
    batch, numeric coincidence across identifier domains), V-26 (geocoder,
    address-string match), V-32 (steward, corporate-number match), V-33
    (merge proposal, digit coincidence across legal-person types).
  - record_write: V-16, V-17 (both a proposed field-value overwrite), V-22
    (retroactive office reassignment request), V-24 (transaction-history
    carryover proposal), V-39 (vendor-ID mapping proposal).
  - assignment: V-12, V-13 (both explicit requests to register a named
    hierarchy type's parent as a DIFFERENT entity than currently stated).
  - Cases with a stated match fact but no narrated proposal event (V-02,
    V-06, V-07, V-08, V-10, V-34 among others) were left at "none" even
    though a basis (address, corporate number, crosswalk) is present, since
    the ask is the track's own generic merge-or-not question, not evaluation
    of someone's specific proposal. This field gates no rule in the current
    engine per fact-sheet-schema.json's own doc, so a different reasonable
    split would not change today's score, but is recorded here for audit.

- **V-26 location_comparison.** The input states a housing-display
  renumbering means the SAME address notation now refers to a DIFFERENT
  block ("同じ表記が現在では別の街区を指す"). None of the six enum values
  precisely describes "identical text, different real place after a
  renumbering" (same_area_renamed is its near-opposite: different text,
  same place). Closest fit taken: different_administrative_area, since the
  two things being compared are, in current fact, different areas.

- **V-29 / V-31 (address / identifier tracks, kanji-variant name plus a
  differing lot number between two candidate records).** Treated as rule 4's
  cross-record contradicted_attributes case (building_name/lot_number for
  V-29; name_notation/lot_number for V-31), not rule 3's same-input
  self-contradiction, since two different candidate RECORDS disagree with
  each other rather than one record's own fields disagreeing internally, so
  evidence_reasons was left without "unexplained_conflict" for these two.
  location_comparison was left null for both: no enum value describes
  "differing building name and differing lot number between two candidate
  records" (same_chiban_different_notation is the opposite shape: different
  text, same chiban). authoritative_identifier was set to "absent" rather
  than "conflicting" for both, since neither case states an actual
  identifier value (corporate number, GUID, etc.) at all, only a name/lot
  disagreement; "absent" reads truer to "no identifier exists to govern
  this" than "conflicting" (reserved, in this extraction, for cases where
  two identifier VALUES are stated and disagree, e.g. V-04, V-09, V-33).

- **V-19 (survivorship, matching escrow account number via a shared
  collection bank).** No identifier VALUE is stated in the input, only that
  the two companies' escrow account numbers coincide; identifiers left
  empty rather than inventing a placeholder value. stated_relation left
  "none": the shared fact holds between each company and a THIRD PARTY (the
  collection bank), not between the two companies directly, per the round 6
  stated_relation repair note.

- **V-20 / V-21 / V-25 (survivorship/temporal, two authoritative sources
  disagreeing on one fact for a single entity, no cross-record dedup
  question).** Read as rule 3's same-input self-contradiction shape (two
  facts stated in the same input disagree) since only one entity is under
  discussion in each, not two dedup candidates. V-17 disagrees the same way
  but a governance policy explicitly resolves which source wins, so that one
  was marked "explained_conflict" rather than "unexplained_conflict".

- **V-33 / V-34.** Neither states the literal coinciding digit string /
  identifier values (only that they numerically coincide, or that a
  crosswalk maps one to the other); identifiers left empty in both rather
  than fabricating a placeholder value, consistent with "never guess a fact
  the input does not state."

- **V-27.** A pure fact-lookup question (what prefecture/municipality) with
  zero address text beyond a destination name; blank_fields carries
  prefecture and municipality, corroborating_facts left empty since nothing
  in the input corroborates either value.

No off-enum values were used in any sheet as originally written; no
post-validate repairs were needed (see RECORD.txt in the run directory for
the validate command's own output, captured after this file was hashed).
