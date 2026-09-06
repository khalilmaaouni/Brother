# JBEQ-MDM blind round 9 extractor notes (2026-09-06)

Extractor: fresh Claude subagent (sonnet, effort high), reading only
EXTRACTOR-PROMPT.md, fact-sheet-schema.json, and the seventy case prompt
files under benchmarks/jbeq/mdm/prompts/. No seed, no runs directory, no
regression sheet, no baseline, no decision-rules-addendum, no
generalization/identity-class/unseen files, no scripts/jbeq_decide.py, no
test file was opened before fact-sheets.json was written and hashed.

## Files opened, in order

1. benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
2. benchmarks/jbeq/mdm/fact-sheet-schema.json
3. benchmarks/jbeq/mdm/prompts/AD-01.md through AD-10.md
4. benchmarks/jbeq/mdm/prompts/EO-01.md through EO-10.md
5. benchmarks/jbeq/mdm/prompts/HI-01.md through HI-10.md
6. benchmarks/jbeq/mdm/prompts/ID-01.md through ID-05.md
7. benchmarks/jbeq/mdm/prompts/MM-01.md through MM-10.md
8. benchmarks/jbeq/mdm/prompts/RQ-01.md through RQ-05.md
9. benchmarks/jbeq/mdm/prompts/SV-01.md through SV-10.md
10. benchmarks/jbeq/mdm/prompts/TM-01.md through TM-10.md

(70 case files plus the two schema/prompt documents; nothing else opened.)

## Note on where this extraction actually ran

The task asked for a fresh worktree at /private/tmp/bh-lane-round9 created
from ~/brother-hub. That worktree registration was not visible to this
agent's own git checkout and the file-write guard refused writes there
(a worktree-isolation guard tied to this agent's assigned worktree), so
per the task's own fallback instruction this extraction instead branched
from hub/wbs/jbeq-reject-vs-keep (commit ed2a1886c) directly inside the
agent's own assigned worktree and pushes to the "hub" remote by name
(https://github.com/khalilmaaouni/brother-hub.git), which this worktree
already carries alongside "origin" (the public Brother repo, push
disabled there). The prompt and schema content was verified byte-identical
between the two checkouts before this note was written (same commit).

## requested_action: how the basis-naming boundary was read

The prompt's own rule: set match_on_stated_basis only when the question
itself PROPOSES a match/link AND NAMES the basis it rests on (the
"〜を根拠に照合してよいか" shape). Read literally: a question that merely
asks "may these be merged/matched" without the "を根拠に [X]" clause does
not name a basis, so it stays "none", even when it is obviously proposing
something. Cases carrying the explicit "を根拠に" clause: AD-10 (address
match), ID-01 (numeric match), ID-04 (ID collision), MM-01 (corporate
number), MM-02 (numeric match), MM-03 (phone+building name match), MM-06
(reading match). Seven cases total.

Two boundary calls worth flagging explicitly because they sit right next
to that line:

- EO-04 asks "この2件は同一の法人として照合してよいか" (may these be
  matched AS the same legal entity). This names the TARGET category of
  the proposed match (legal entity identity) but not an evidentiary BASIS
  the way "住所の一致を根拠に" or "電話番号とビル名の一致を根拠に" do.
  Recorded requested_action: none. If the intended reading is broader
  (any question of the form "may X be matched" counts, basis or not),
  this is the one case where that would flip the recorded value.
- HI-01 asks whether three already-stated hierarchy parents (capital,
  trade_flow, reporting) can be unified into the one parent field the
  master record carries. This is a consolidation-of-existing-facts
  question, not a request to WRITE a parent that differs from what is
  already stated for a given type, so it was left "none" rather than
  "assignment".
- HI-07 asks which hierarchy TYPE a bare "is a group company of X"
  statement should be registered under. The type itself is exactly what
  is undetermined, so this was extracted as "none" (an open
  classification question) with blank_fields=["hierarchy_type"], rather
  than forcing "assignment" with a guessed type.
- HI-03 asks whether a PRE-transfer period's hierarchy row should stay
  under the OLD parent (i.e. whether to leave history as it is), not
  whether to write a new/different parent. Recorded "none".

## requested_parent: only set when an existing same-type parent is stated

Per the prompt, requested_parent is null whenever the input does not
already carry a hierarchy_parents entry for the SAME type the request
targets, even when requested_action is "assignment". That fired for
HI-04 (sales-office reassignment describes no existing capital parent at
all), HI-05 (explicitly "no capital relationship, independent legal
entity" -- nothing to differ from), and HI-10 (only a trade_flow relation
is stated; the request asks to write a capital parent, a different type
with no existing entry). Only HI-02 has both an existing same-type
(capital) parent stated AND a request naming a different entity for that
same type, so only HI-02 carries a non-null requested_parent
("株式会社ヒノデ薬品").

## authoritative_identifier: "conflicting" read broadly, per the schema's
own worked example

fact-sheet-schema.json's own "WORKED EXAMPLE, STATED_DIFFERENCE NOT
'none'" section sets authoritative_identifier="conflicting" for a plain
case of two different, individually valid corporate numbers, one per
side, with nothing else tying the records together. That is a broader
reading than "two identifiers that SHOULD be the same and are not" in
isolation: it means any pair of candidate records that each state their
own valid governing identifier, and those identifiers differ, reads as
"conflicting", not "aligned" or "absent". Applied that reading to:
AD-10, EO-04, EO-09, ID-01 (same value, different domain), ID-04 (same
value, different domain), ID-05 and TM-07 (reused identifier, different
successor), MM-02 (same value, different domain, different
legal_person_type), MM-03 (two different valid corporate numbers behind
a coincidental phone/building match), SV-02, SV-07 (a conceptual
competing-ID conflict with no printed values), SV-08 (dates disagreeing
about which is current). Where a value on only ONE side is stated
(EO-07), that was left "aligned" (the default) since the definition
reserves "absent" for the case where NEITHER side states one, and a
single blank side is a blank_fields fact, not a conflict.

## contradicted_attributes: applied to notation and lot-number pairs
per the prompt's own worked shape

The prompt names "a name written in different characters (a kanji
variant with the same reading), and a lot number that differs between
the two records" as its own worked examples of contradicted_attributes.
Applied that literally to EO-10 (zenkaku/hankaku name notation),
MM-06 (differing kanji name plus differing banchi), SV-01 (registry vs
CRM name notation).

## Fields the schema does not cover well for some tracks

- object_type has no "vendor"/"仕入先" value; EO-05 (a record that is
  simultaneously customer and vendor) was extracted with
  object_type_a=object_type_b="commercial_account" for lack of a closer
  enum value, noted here rather than invented.
- lifecycle has no value for a plain corporate NAME CHANGE (only active,
  closed, relocated, identifier_reused). MM-07 and TM-08's "superseded"
  state were left at "active"/"closed" respectively as the closest fit;
  the actual fact (registry name change effective 2025-04-01 / successor
  record effective 2026-07-01) is carried in effective_dates instead.
- HI-03's input never names the old/new parent legal entities, only
  "旧親法人"/"新親法人" as generic labels. hierarchy_parents for HI-03
  carries "新親法人" verbatim as the input's own label, not a proper name.
- Several match-or-no-merge and survivorship cases state that two
  identifiers/values match or differ without printing the actual digits
  (EO-05, EO-10, MM-03, MM-04, SV-07). Those identifiers entries carry a
  placeholder string noting "value not printed" rather than a fabricated
  number.
- SV-09's input states outright that the CRM side's blank means
  "not yet entered, not a deletion instruction". That sentence explains
  what the blank itself means rather than corroborating a different,
  unconfirmed fact, so per rule 2 it was NOT placed in
  corroborating_facts; it is only recorded here.
- TM-09 asks about a date (2026-03-15) that falls in the GAP between one
  record's valid_to (2025-12-31) and the reopened successor's valid_from
  (2026-04-01). Neither candidate's window actually covers the as_of
  date; effective_dates.candidate_effective_date was left null to reflect
  that, since the schema has no explicit "gap" flag.
- MM-04's creator-comment ("登録済みか未確認のため再登録") does not fit
  corroborating_facts (which is reserved for facts supporting a BLANK
  field) or any evidence_reasons enum value, so it is recorded only here:
  the record was created 3 days after the other, with a comment noting
  uncertainty about whether the entity was already registered.

## RQ / SV / TM tracks

These tracks are decided by track-specific logic (requirement-status
classification, source-priority survivorship, temporal authority), not
by the entity-resolution identity-kind fields the round 5-8 repairs were
about. The identity-kind fields (object_type_a/b, lifecycle,
hierarchy_parents, authoritative_identifier, location_comparison) were
left at their schema defaults for these tracks except where the input
states something concrete that maps cleanly (e.g. TM's effective_dates,
or a stated identifier conflict in SV-02/SV-07). location_comparison was
left null on every non-address-track case per the prompt's own
instruction ("Set this ONLY on an address-track case ... null on every
other track"), even where the case text happens to mention an address
(TM-01, TM-03).
