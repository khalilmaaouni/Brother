# JBEQ-MDM U4 extractor notes (blind phase 1)

Files opened during blind extraction, in order:
1. benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
2. benchmarks/jbeq/mdm/fact-sheet-schema.json
3. benchmarks/jbeq/mdm/unseen-4-prompts/W4-01.md through W4-40.md (all 40 files)

No other file under /private/tmp/bh-u4-inputs was opened. No expected-answer file,
RECORD file, prior unseen set, decision-rules-addendum.md, scripts/jbeq_decide.py
or scripts/jbeq_mdm.py source, or any test file was read before the fact sheets
below were finished and hashed.

## Borderline or closest-fit calls, logged rather than silently forced

- W4-05: stated_relation left "none". The two clinics share only a city name,
  which is broader than the same_site_only definition (a physical address,
  floor or building), so same_site_only was not used.

- W4-09: stated_relation set to "same_site_only" on the strength of the
  question's own clause "本店所在地が一致していることを根拠に" (the basis
  offered for the merge is that headquarters addresses match). This is a
  fact carried by the question rather than the input body, but round 9's
  widening treats a stated basis anywhere in the passage as part of what is
  stated. Flagged as the closest fit, not a certain one.

- W4-19: authoritative_identifier set to "aligned". The input states the
  corporate numbers match ONLY in the last 4 digits and the upper digits
  are explicitly unconfirmed due to a possible transcription error. This is
  neither a clean "aligned" (nothing is stated to the contrary is not quite
  true) nor a clean "conflicting" (no confirmed disagreement is stated,
  only unconfirmed uncertainty) under the three-value enum. "aligned" was
  chosen as the closer of the two since no disagreement is actually
  confirmed, only unverified. evidence_strength was set to "medium" to
  carry the real uncertainty instead.

- W4-20 and W4-30: evidence_strength set to "weak" with evidence_reasons
  ["missing_identifier"] for a shared generic prefix / shared label that is
  explicitly stated as non-individuating (a regional office code shared
  broadly; a generic fiscal-year label on two different product lines).
  Neither case states a literal numeric match score, so "score_only" was
  not added; only "missing_identifier" was used, since no individuating
  identifier is present on either side.

- object_type_a/object_type_b: "legal_entity" was used as the safe default
  per the extractor prompt's own instruction whenever the input does not
  distinguish a more specific kind. "store" was used only where the input
  says 店舗 explicitly (W4-01, W4-03, W4-13). "site" was used where the
  input names a physical facility, plant, depot, or clinic premises
  (W4-21 through W4-26, W4-36) rather than a named legal entity comparison.
  "ship_to" and "payer" were used only in W4-04, where the input names
  those roles directly.

- Several fact-based fields (identifiers, hierarchy_parents,
  contradicted_attributes) were extracted as literal strings copied from
  the input's own wording where no cleaner enumerable value existed (for
  example the unconfirmed partial corporate number in W4-19, and the
  generic prefix in W4-20). These are extraction artifacts, not invented
  values; the underlying fact stated by the input is preserved.

No requested_action, requested_relation_type, authoritative_identifier,
stated_relation, or stated_difference value outside the schema's _enums
was used anywhere in the 40 sheets.
