# JBEQ-MDM unseen-5 qualification run, extractor notes (phase 1, blind)

Files opened before writing fact-sheets.json and this file, in order:
benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md, benchmarks/jbeq/mdm/fact-sheet-schema.json,
then W5-01.md through W5-40.md under benchmarks/jbeq/mdm/unseen-5-prompts/. No other file
under benchmarks/jbeq/mdm was opened before this file and fact-sheets.json were written and
hashed: not unseen-5-2026-09-07.json, not unseen-5-2026-09-07-RECORD.md, not any SCORES file,
not any earlier run directory's answers, not decision-rules-addendum.md, not
scripts/jbeq_decide.py.

One deliberate exception, explicitly pre-authorized by the brief before phase 1 began: reading
round 12's own RECORD.txt and merge.py (git show
origin/wbs/jbeq-round12-extraction:benchmarks/jbeq/mdm/runs/blind-round12-2026-09-07/RECORD.txt
and ...:merge.py) to learn the phase 2 command sequence and file names. Those two files carry
commands and round-12 case ids/answers, never U5 case content, so they taught nothing about this
set's facts.

## General notes

benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md is the actual path of the extractor prompt;
the brief named benchmarks/jbeq/mdm/EXTRACTOR-PROMPT.md (no prompts/ segment), which does not
exist. Read the prompt from the prompts/ subdirectory, the only copy in the tree.

requested_parent is not in the extractor prompt's own OUTPUT key enumeration (the bullet list
near the top), but its own later section says it is "ALWAYS PRESENT as of round 7 ... set to
null, never omitted". Round 12's own fact-sheets.json (read only for its key shape, not its
values, via a one-off python3 -c that printed sorted(d[key].keys())) confirms every sheet
there carries requested_parent. Followed the more specific and more recent instruction: every
sheet below carries requested_parent, null unless a request literally asks for a different
parent than the one already stated for that hierarchy type.

Every prompt file's own boilerplate (決定語彙 / 境界 sections) is byte-identical across all 40
cases; the only per-case content is the TRACK line, the 入力 (input) paragraph, the 設問
(question), and the 許容される回答 (allowed answers) list. Confirmed by diff that the boilerplate
does not vary by track.

allowed_answers by track: address/entity-object/hierarchy/identifier/survivorship/
match-or-no-merge/requirements all carry the standard 7-value AUTO-MERGE...NO-DATA vocabulary in
this set. Only track "temporal" (W5-26 to W5-30) carries a different list: R1/R2/R3/NO-DATA. This
differs from round 12, where "temporal" used R1/R2/NO-DATA (a two-source predecessor/successor
question) and "survivorship"/"requirements" used source-name or DECIDED/ASSUMED/INFERRED/UNKNOWN
vocabularies. Copied each case's own list verbatim from its own file; invented nothing.

## Cases where the schema's fields do not have a clean home for a stated fact

The fact-sheet schema (object_type_a/b, lifecycle, effective_dates, identifiers, ...) is built
around a two-record identity comparison. Several U5 cases state facts these fields cannot
represent without stretching an enum past its stated definition. Rather than force a stretch, the
literal facts are recorded here; the fact sheet carries only what its fields can honestly hold.

- W5-11 (hierarchy): 子安運輸 has three DIFFERENT, all-currently-valid parent relationships
  (capital: 庚崎ホールディングス; trade_flow: 辛島商事; reporting: 関東統括本部), each in its own
  ledger. The question asks whether to record all three as ONE relation. hierarchy_parents
  carries all three as {type, parent} entries; this is a single-entity, three-parent-type case,
  not a two-record comparison, so object_type_a/object_type_b are a loose fit (set to
  legal_entity / hierarchy_node) rather than a real second candidate record.

- W5-13 (hierarchy): a prior-year internal audit report explicitly states 北陸統括部 has NO
  capital or business relationship whatsoever with 寅松水産; the request asks to write 北陸統括部
  as 寅松水産's reporting parent anyway. There is no schema field for "a named third-party audit
  finding that flatly denies any relationship exists"; hierarchy_parents is left empty (nothing
  is stated as the CURRENT reporting parent) and the refuting fact is recorded only here.

- W5-17 (identifier): a note in the input claims a dissolved company's corporate number was
  reassigned to a new company, but the input's OWN text states corporate numbers are never
  reassigned after dissolution under the registration system, i.e. the note is stated to be an
  error. This is an "explained_conflict" (the discrepancy is explained, not merely asserted), not
  an "unexplained_conflict". identifiers carries two entries at the same value, one status:
  "valid" (the dissolved holder) and one status: "disputed_erroneous_note" (the claimed new
  holder, per the input's own correction) since the identifier status sub-field is explicitly
  not closed-vocabulary per fact-sheet-schema.json's own _enums._doc.

- W5-21 (survivorship): only relative dates are stated ("先月末", last month-end, for the
  override's expiry). No absolute date is given anywhere in the input. effective_dates.as_of
  and .prior_valid_to carry descriptive text rather than an invented ISO date.

- W5-23 (match-or-no-merge): the two corporate numbers are stated to differ "by the last digit
  only", but neither literal number string is given. identifiers[].value is left null on both
  entries rather than inventing digit strings; the difference is recorded via status text and
  authoritative_identifier: "conflicting".

- W5-26 through W5-30 (temporal): every one of these five cases is a THREE-source (R1/R2/R3)
  governance/precedence question ("which source governs the fact now"), not the two-record
  predecessor/successor comparison the schema's effective_dates fields (as_of,
  candidate_effective_date, conflict, prior_valid_to) are built for (per round 12's own record,
  those fields drove a temporal_before_successor / temporal_at_or_after_successor rule pair
  over exactly two records, R1 and R2, answered R1/R2/NO-DATA). There is no third slot in the
  schema for a source R3, no per-source "which policy governs" field, and no field for a named
  precedence RULE ("R3 always governs when R1 and R2 disagree"; "R3 overrides ERP only within its
  approved validity window"). effective_dates on these five sheets is filled with the literal
  dates/relative-date text the input states and conflict: true/false as apt; the governing
  policy text itself, and the identity of each of R1/R2/R3, are recorded only in the per-case
  notes below since no schema field holds them. This is stated plainly rather than bent toward
  what the two-record schema shape can already handle, per the extractor prompt's own instruction
  never to shade a reading toward a rule the engine currently has.

  - W5-26: R1=ERP customer address (governs by default, still current/maintained), R2=CRM
    customer address (not elaborated further), R3=sales rep's manual temporary-override sheet,
    valid only within its own approved period per company policy; this customer's R3 period
    expired yesterday. Literal answer-shaping fact: the override has lapsed and ERP was never
    changed.
  - W5-27: R1=old (closed) store code's shipment history, R2=new store code's shipment history;
    closure report states the store closed 2026-08-01 and shipments moved to the new code from
    that date; a 2026-08-15 shipment slip (after the close date) is filed under R1, the closed
    code. This is the extractor prompt's own named self-contradiction shape (a record dated after
    a stated lifecycle event, filed under the pre-event state) - evidence_reasons:
    ["unexplained_conflict"] and lifecycle: "closed" reflect it.
  - W5-28: R1=sales-management system's new-price effective date, R2=accounting system's
    new-price effective date; the two dates differ by one week. No precedence rule or explanation
    is stated anywhere in the input, unlike W5-26/29/30 which each name a governing policy. This
    is the plainest "nothing resolves it" case among the five.
  - W5-29: R1=HQ inventory ledger, R2=branch's own independently-tallied inventory, R3=this
    fiscal year's completed external audit's confirmed physical inventory; company policy states
    R3 ALWAYS governs when R1 and R2 disagree. This year R1 != R2, and both differ from R3's
    confirmed value as well; the policy names no exception for that case.
  - W5-30: R1=department's old-name-based past approval history, R2=new-name-based approval
    history going forward, R3=a temporary dual-named TEST record created during the rename
    migration itself, explicitly flagged in the migration log as test data (not a real production
    record). Department renamed officially last month. Question asks which source governs when
    referencing approvals from BEFORE the rename.

- W5-31 (survivorship): two 適格請求書発行事業者番号 records carry the literal SAME T-number
  and the same corporate number; the only stated difference is registration STATUS (one current,
  one revoked). contradicted_attributes carries "qualified_invoice_issuer_number registration
  status" since the two records state disagreeing values for that one attribute, per rule 4 (an
  attribute both sides state, where the stated values disagree).

- W5-36 (requirements): code H is described as "休止中" (dormant/suspended), not "closed" in
  the schema's sense. The lifecycle enum (active | closed | relocated | identifier_reused) has no
  "dormant" value; "closed" would overstate what the input says (a dormant code is not stated to
  have ceased to exist). Left lifecycle: "active" (nothing stated blocks a merge outright) and
  recorded the dormant/active distinction only here, since forcing it into "closed" would be
  exactly the kind of bent reading the extractor prompt forbids.

## Contradicted_attributes / rule-4 kanji-variant note

W5-34 explicitly matches the extractor prompt's own worked illustration under rule 4: two trade
names sharing a reading but written in different kanji ("戌亥硝子" vs "乾硝子"). Recorded as
contradicted_attributes: ["company_name"].

## Facts stated but with no dedicated schema field (recorded here only)

- W5-01, W5-06: representative name and/or phone/trade-name continuity across an old/new record
  pair is stated as evidence of identity, but corroborating_facts is explicitly scoped by the
  extractor prompt (rule 2) to facts that support a BLANK field, not general supporting evidence
  for a non-blank comparison. W5-06's blank corporate_number field legitimately takes
  corroborating_facts (rep name + trade name both match); W5-01 has no blank field at all, so its
  identical rep-name/phone facts are recorded only here, not forced into corroborating_facts.

No off-enum value was written into any closed-vocabulary field (stated_relation,
stated_difference, evidence_strength, evidence_reasons, object_type, lifecycle, requested_action,
authoritative_identifier, location_comparison, legal_person_type). Where a field's own value list
is explicitly NOT closed per fact-sheet-schema.json's _enums._doc (identifier domain and
status, tenant_boundary), short descriptive free text was used instead of forcing a closed-enum
value that would misstate the input.
