# JBEQ-MDM qualification run U1, extractor notes (blind, 2026-09-06)

One line per case. Notes flag every place a value was approximated to the
nearest allowed vocabulary entry, or a judgment call was needed to fit the
two-sided schema shape, per the extractor prompt's instruction to say so
here rather than invent an off-list value.

U-01: object_type_a/b set to "site" (branch/office records, not the parent
legal entities) since the input names 支店/営業所 explicitly; one_to_many_object
set true on the same reading as example_rule_5 (same building, same floor,
no other tie) even though both sides are named companies rather than one
shared consolidation point plus tenants; flagged as a judgment call.

U-02: object_type_a="address" (fragment only), object_type_b="store";
authoritative_identifier set to "absent" since no identifying data
(prefecture, city, postal code) is present at all, rather than defaulting
to "aligned".

U-03: location_comparison set to "different_administrative_area" as the
nearest fit for a banchi notation that is identical except for the renamed
administrative area following a 1990s municipal merger.

U-04: object_type_a/b set to "address"/"address"; no store or site kind is
named for either side in this case, unlike U-02.

U-05: location_comparison set to "notation_variant_only" for the kanji vs
Arabic numeral difference in the banchi.

U-06: distinct_operational_attributes left false. The input names two
different business-purpose accounts (wholesale billing, vending-machine
maintenance) sharing a corporate number and sales office, but never states
a distinct delivery/billing/shelf destination per side the way the schema's
definition requires; flagged as borderline rather than assumed true.

U-08: stated_relation approximated to "role_pair" as the nearest enum value.
The input states a ship-to record is explicitly the delivery window tied to
a named store record, which no listed stated_relation value names directly.

U-09: identifier domain field ("phone_number") is free text since identifier
domains are not enumerated per the schema's own note.

U-12: hierarchy_parents type set to "reporting" as the nearest fit for a
branch stated to be organizationally under a head office; the input does
not use any of the three type words (capital/trade_flow/reporting)
verbatim.

U-14: requested_parent left omitted. The request asks to write a company as
parent of another where the input's own capital table names a different,
unrelated company as the real parent; this is not a same-type direction
reversal, so requested_parent does not apply per the extractor prompt's own
example of when to leave it null.

U-22: identifiers[0].value set to null since the input states the two
invoice registration numbers are identical without printing the literal
digit string.

U-25: object_type_a/b left at the "legal_entity" default. The input names
both records' role as 仕入先 (supplier), which has no dedicated object_type
enum value; legal_entity is used per the extractor prompt's own fallback
guidance for a bare identity comparison.

U-26: object_type_a/b set to "site"/"site" as a judgment call. This case is
a single lot record whose own two fields (product-name text vs a lot-code
lookup table) disagree about the manufacturing plant; it does not cleanly
map onto a two-candidate-record comparison, so both sides represent the two
competing plant readings rather than two separate input records.

U-27: evidence_reasons set to ["missing_identifier"] only, not "score_only",
since the requirement text states a name-match policy rather than citing an
actual numeric match score.

U-31: effective_dates.conflict set to false. The input frames the ERP value
as the current, higher-authority figure superseding an expired manual
override, not as an unresolved dispute about which value governs today.

U-33: identifiers left empty. The crosswalk is discussed only as "顧客ID"
generically on both systems; no literal ID string is printed for either
side, unlike U-17's OLD-4521/NEW-9910.

U-38: object_type_a/b set to "site"/"site" for the relocated store record as
carried by two systems.

U-39: requested_action/requested_relation_type left at "none"/null. These
fields are scoped by the extractor prompt to hierarchy-parent writes; the
request here is to reassign past transaction history, which is a different
kind of write the prompt does not name a field for.

Every other case (U-07, U-10, U-11, U-13, U-15 through U-21, U-23, U-24,
U-28 through U-30, U-32, U-34 through U-37, U-40) used only vocabulary
listed verbatim in the extractor prompt or fact-sheet-schema.json's _enums,
with no approximation.
