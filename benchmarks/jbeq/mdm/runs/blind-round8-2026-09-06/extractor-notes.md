# JBEQ-MDM blind round 8 extractor notes, 2026-09-06

Extractor: a fresh Claude subagent (sonnet, effort high), reading only
benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md,
benchmarks/jbeq/mdm/fact-sheet-schema.json, and the seventy case prompts.
No seed, no runs directory, no regression sheets, no baseline, no
decision-rules-addendum.md, no generalization or identity-class files, no
unseen files, no scripts/jbeq_decide.py, and no test was opened before all
seventy sheets were written and hashed.

Files opened before hashing:
1. benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
2. benchmarks/jbeq/mdm/fact-sheet-schema.json
3. benchmarks/jbeq/mdm/prompts/AD-01.md through TM-10.md (all 70 case files)

## Judgment calls worth flagging

- AD-08: prefecture is blank on side A; town name (宮西町) and lot number
  (2-24) match side B exactly, but the input explicitly notes 府中市 exists
  in both Tokyo and Hiroshima, so a matching town name inside it does not
  resolve which prefecture. location_comparison is left null: none of the
  six enum values (notation_variant_only, same_chiban_different_notation,
  different_administrative_area, different_unit_in_building,
  internally_inconsistent, same_area_renamed) describes "blank field plus
  a duplicate city name across two prefectures". blank_fields=["prefecture"]
  and corroborating_facts carries the town-name-and-lot-number match, per
  the blank-field-with-corroboration pattern in the schema's own worked
  example (example_rule_A_blank_field_escalate).

- AD-09, EO-08, ID-02: all three questions ask, in different words,
  "which existing record should this incoming data attach to" or "may we
  finalize using this identifier". A record_write reading (write/reassign
  data other than a hierarchy parent) was considered for all three, since
  each proposes attaching or confirming something. Resolved to "none" for
  all three: none names a specific write the way the prompt's own example
  does (moving transaction history to a new account), and the prompt's
  default is "none" for a case that asks what the identity or relation IS
  rather than proposing a specific write. Flagged here so the rule lane can
  weigh the other reading if it matters.

- AD-10: the two addresses are stated as literally identical ("同一住所同
  一階"), not compared under two notations, so location_comparison is null
  even though the track is "address": nothing about notation variance is
  stated here, only a genuine identifier conflict (two different valid
  corporate numbers at one shared address).

- MM-02, ID-01, ID-04, ID-05: authoritative_identifier is set to "absent"
  rather than "conflicting" for these four cross-domain or reuse cases.
  Reasoning: "conflicting" is reserved for the same identifier TYPE stated
  on both sides with genuinely different values (EO-04's two distinct
  corporate numbers, SV-02's two distinct invoice numbers). MM-02/ID-01/
  ID-04 involve two DIFFERENT identifier domains that happen to share
  digits (a coincidence the input explicitly calls out), and ID-05
  involves the SAME code value whose reliability is broken by stated
  reuse across two different entities. In neither case does the input
  state a real value disagreement within one identifier type, so "absent"
  (no identifier that reliably governs) reads more honestly than
  "conflicting".

- EO-05: object_type_b is set to "commercial_account" for the 仕入先
  (supplier) role record, though the object_type enum has no literal
  "supplier" value. commercial_account is the closest generic fit; noted
  here rather than inventing a new enum value.

- EO-07, EO-10, MM-06, MM-07: contradicted_attributes is populated even
  where the two records' name difference is only a notation variant
  (full-width/half-width) or a same-reading kanji variant, per Rule 4's
  own text, which names exactly this pattern ("a name written in different
  characters, a kanji variant with the same reading... both sides state
  the attribute, and the stated values disagree"). MM-06 additionally gets
  "lot_number" for the stated different banchi, the rule's other named
  example.

- HI-04, HI-05, HI-10: each asks to write a specific hierarchy type
  (capital in all three) that hierarchy_parents does not currently carry
  any entry for (only a differently-typed fact: sales-office assignment,
  brand affiliation, or trade flow, is stated). Per the requested_parent
  rule's explicit carve-out ("null on every other case, including one that
  asks to write a type hierarchy_parents does not carry at all"),
  requested_parent is null in all three even though each request names a
  specific target entity, because there is no existing same-type entry to
  compare the request against.

- HI-01: mirrors fact-sheet-schema.json's own
  example_rule_D_hierarchy_keep_separate almost exactly (same three
  hierarchy types, same structure, different illustrative names), which
  gave high confidence in requested_action="none" for a three-parents
  consolidation question that never names a single target type.

- HI-07: the input states a group-company relation without naming which
  of the three hierarchy types (capital/trade_flow/reporting) it is.
  blank_fields=["hierarchy_type"] records this; hierarchy_parents is left
  empty rather than guessing a type value.

- HI-09: two hierarchy nodes of different KINDS (a location-hierarchy node
  and an org-hierarchy node) share a name but only partially overlap in
  the prefectures they cover. Neither stated_relation nor stated_difference
  has an enum value for "same name, different hierarchy kind, partial
  geographic overlap"; both are left at their "none" defaults and the fact
  itself is carried only in the case's own prose, not force-fit into an
  enum.

- MM-04: only the newer/duplicate record is explicitly stated to have zero
  transaction history ("片方には取引履歴がまったく無く"); the other side's
  history status is never stated. history_exists=false reflects the
  confirmed-empty side; the schema has no per-side history flag, so this
  is the closest honest reading rather than assuming the unstated side
  also has none.

- MM-09: one side's attributes are inaccessible because of access control,
  not because the underlying data is blank. This is left out of
  blank_fields (which is reserved for data genuinely absent from the
  record) and captured instead through evidence_strength="weak" plus
  evidence_reasons=["score_only","missing_identifier"], since the only
  visible fact is a bare 0.91 score.

- SV-03: the input states a contract document is attached to the ERP
  record, which is relevant to which source should be trusted, but this is
  NOT recorded in corroborating_facts, because Rule 2 restricts that field
  strictly to facts supporting a BLANK or missing field, and there is no
  blank field in SV-03 (both sources have stated payment-terms values that
  simply disagree). The contract-attachment fact is recorded here instead.

- SV-08: effective_dates.conflict is set true, since the input states two
  dates for the same fact (the legal name) that genuinely disagree about
  which should govern today: an ERP update that arrived today but carries
  an OLDER source_timestamp (2026-02-10) than a registry change already
  reflected (2026-04-01). This is the one case in the corpus judged to
  meet the "two dates disagree about which governs today" bar precisely.

- TM-01 through TM-10: contradicted_attributes is deliberately left empty
  for ordinary valid_from/valid_to succession (e.g., TM-02's name change,
  TM-05's future-dated payment terms), even though the two records state
  different values for the same attribute. Reasoning: Rule 4 was written
  for records being compared for identity/merge at the same moment; the
  temporal track's valid_from/valid_to fields already express ordered
  succession over time, and flagging every natural temporal change as a
  "contradiction" would apply the rule to the one track built specifically
  to describe change without conflict. contradicted_attributes is reserved
  in this run for TM cases with no such stated resolution mechanism
  (none arose in the frozen 70's temporal cases).

- TM-09: the store is stated as "休止" (suspended/paused), not permanently
  closed, and the query date (2026-03-15) falls inside the gap between
  R1's valid_to (2025-12-31) and R2's valid_from (2026-04-01), i.e.
  neither record's own window covers it. lifecycle is set to "closed" only
  because none of the four enum values (active/closed/relocated/
  identifier_reused) has a "temporarily suspended" option and "closed" is
  the nearest fit; effective_dates.conflict is left false since this is an
  absence of coverage, not two dates disagreeing.

- RQ/SV/TM tracks generally: most of the schema's MDM-specific fields
  (identifiers, hierarchy_parents, stated_relation, tenant_boundary, etc.)
  describe merge/hierarchy facts that simply do not arise in the
  requirements-status track (RQ) and only partly arise in survivorship
  (SV) and temporal (TM). Every required key is still present at its
  documented default on every case per validate()'s requirement; only the
  fields with an actual stated fact were changed away from default.
