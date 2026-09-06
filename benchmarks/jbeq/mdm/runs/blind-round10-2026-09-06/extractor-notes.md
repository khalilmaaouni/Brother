# JBEQ-MDM round 10 extractor notes (blind round, first pass under the widened requested_action definition, hub PR 435)

## Files opened during phase 1 (blind), in the order first opened

1. benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
2. benchmarks/jbeq/mdm/fact-sheet-schema.json
3. benchmarks/jbeq/mdm/round10-prompts/AD-01.md through AD-10.md
4. benchmarks/jbeq/mdm/round10-prompts/EO-01.md through EO-10.md
5. benchmarks/jbeq/mdm/round10-prompts/HI-01.md through HI-10.md
6. benchmarks/jbeq/mdm/round10-prompts/ID-01.md through ID-05.md
7. benchmarks/jbeq/mdm/round10-prompts/MM-01.md through MM-10.md
8. benchmarks/jbeq/mdm/round10-prompts/RQ-01.md through RQ-05.md
9. benchmarks/jbeq/mdm/round10-prompts/SV-01.md through SV-10.md
10. benchmarks/jbeq/mdm/round10-prompts/TM-01.md through TM-10.md

All 70 prompt files were read in full (the case-specific 入力/設問/許容される回答 sections were
extracted with a python slice cutting each file before its repeated ## 決定語彙 boilerplate, so
the substantive content of every file was read; the boilerplate answer-vocabulary and boundary-
rule text is identical within a track and carries no new per-case facts). No other file under
benchmarks/jbeq/mdm was opened before hashing: not seed-2026-09-05.json, not any unseen set, not
any RECORD file, not runs/, not decision-rules-addendum.md, not scripts/jbeq_decide.py or
scripts/jbeq_mdm.py source, not any test.

## The widened requested_action rule, applied literally

The merged prompt (tree 627ef8d9) already carries the "Round 9 widening" text that treats three
shapes as a proposal, plus the rule that a matching/dedup step which has ALREADY presented two
records as a candidate pair on a named basis, before any question is asked, inherits
match_on_stated_basis even when the question itself is open ("どう扱うべきか"). This was applied
literally case by case. Cases where the question itself is a bare "Xしてよいか"/"照合してよいか"
proposal to treat two records as one were all set to match_on_stated_basis, including ones whose
Japanese does not use "根拠に"/"理由に" (shape 2, not shape 1). Cases where the question is a
genuinely open "どう扱うべきか" / "どのレコードに紐づけるべきか" AND no matching/dedup step has
already proposed a candidate pair were left at "none".

Cases where I read the passage as a matching/dedup step already proposing a candidate pair before
an open question, so requested_action inherits match_on_stated_basis despite open question
wording: HI-06 (名寄せが両者を候補として提示している), ID-05 (名寄せは...候補として提示した),
MM-05 (今回の候補ペアの根拠は名寄せスコア0.83のみ), MM-08 (名寄せエンジンが提示した候補),
MM-09 (候補ペアのうち片方は...提示されているのは名寄せスコア0.91という数値だけ).

ID-05 is one of the four comparison cases the orchestrator asked for by name; its
match_on_stated_basis value is a direct product of this exact clause and is the one most likely
to differ from round 9's extraction if round 9 predates this exact "already-proposed inherits"
wording being merged into the prompt.

AD-03, AD-04 and EO-04 all use the bare-proposal shape 2 ("同一の物理拠点として統合してよいか",
"同一の拠点として統合してよいか", "同一の法人として照合してよいか") rather than shape 1's
"根拠に"/"理由に" wording; all three were extracted as match_on_stated_basis under the current
merged prompt.

## Hierarchy track (assignment vs none vs match_on_stated_basis)

HI-01, HI-02, HI-04, HI-05, HI-07, HI-10 explicitly ask whether a hierarchy parent should be
written (unify three parents into one field, reverse parent/child, change the capital parent to
match an operational reassignment, register a franchisee as a subsidiary, decide which hierarchy
a vague "group company" statement belongs under, set the capital parent to the trade-flow
wholesaler) and were set to requested_action="assignment".

requested_parent was set to a name only when the input already states an EXISTING parent of the
SAME hierarchy type that the request asks to replace or reverse (HI-02: existing capital parent
日之出ホールディングス株式会社, request asks to flip 株式会社ヒノデ薬品 into that role, so
requested_parent="株式会社ヒノデ薬品"). For HI-01, HI-04, HI-05, HI-07 and HI-10, the request asks
to write a hierarchy TYPE that the input does not already carry an entry for (capital, in every
one of these), which the schema's own rule says stays requested_parent=null even though
requested_action is "assignment". HI-04 and HI-10 both read as the same trap shape: an
operational fact (a sales rep's territory in HI-04, a trade-flow wholesaler relationship in
HI-10) is being asked to be written into the CAPITAL hierarchy, which the input never states
exists yet for that type, so requested_parent stays null in both.

HI-01 and HI-07 do not name one specific target hierarchy type in the request itself (HI-01 asks
to collapse three named types into "one", not naming which; HI-07's input never states whether
the "group company" fact is capital, trade_flow or reporting), so requested_relation_type is left
null on both and flagged here rather than guessed.

HI-06, HI-08 and HI-09 ask to merge/unify two records/nodes rather than write a hierarchy parent
onto one entity, so they were read as match_on_stated_basis (entity-identity questions), not
"assignment", even though the track is "hierarchy".

## Borderline requested_action calls left at "none"

AD-09 and EO-08 ask "which existing record/site should this be linked to" (どの拠点に紐づけるべき
か / どのレコードに紐づけるべきか). This reads as an open identity-determination question (which
target is correct) rather than a stated request to write/reassign specific data, so both were left
at requested_action="none". A case could be made for record_write given "紐づける" literally means
"link/attach"; flagged here as a judgment call rather than silently picked.

ID-02 asks whether a lapsed invoice-registration number may be used to finalize a billing target
(この失効した番号を用いて請求先を確定してよいか). This is not a proposal to treat two records as
one, and it does not name a second record to move data to/from, so it was left at "none" rather
than forced into record_write; the lapsed status itself is captured via lifecycle="closed" and the
effective_dates fields.

ID-03 documents a migration crosswalk table entry (旧ERPの顧客ID and CRMのGUID correspondence) plus
an independently-stated matching corporate number, with an open question ("どう扱うべきか"). A
crosswalk table entry is a data-correspondence fact, not itself a stated proposal to merge/match
the records the way the four comparison cases' explicit "統合してよいか"/candidate-pair language is,
so this was left at requested_action="none"; flagged as a borderline call since a stricter reading
of "already presented as a candidate pair" could extend to a crosswalk table entry.

## Evidence and identifier fields worth flagging

ID-01 and MM-02 and ID-04 all match the authoritative_identifier="conflicting" third shape (same
numeric value in two different identifier domains, by coincidence) rather than the more common
straightforward-disagreement shape.

ID-03's evidence_strength is set to "strong" from the independently and directly stated matching
corporate number (not derived from the crosswalk), with evidence_reasons carrying
"unvalidated_crosswalk" as an additional flag for the separately-mentioned migration
correspondence table entry, per rule 1's instruction to flag any crosswalk mention even when a
stronger independent identifier is also present.

MM-07 and EO-10 both read a name difference between two records as fully explained by a stated
registry event (a registered trade-name change on file, and an ERP-migration full-width/half-width
notation difference respectively), so evidence_reasons carries "explained_conflict" rather than
"unexplained_conflict", while contradicted_attributes still lists the differing attribute per rule
4 (the field is neutral on whether the disagreement is explained).

AD-06 is the one case matching rule 3 literally (a registered address in one prefecture, a postal
code belonging to a different one, stated within the SAME record): evidence_reasons carries
"unexplained_conflict" and contradicted_attributes names both disagreeing fields.

MM-06's kanji-variant company name (株式会社アオイ電器 vs 株式会社アオイ電機, same reading) is the
rule 4 named example almost verbatim; contradicted_attributes=["legal_name"].

## Missing literal identifier values

EO-03 and MM-03 both state that corporate numbers match / differ without giving a literal numeric
value. Per the fact-sheet-schema's own convention (only the crosswalk worked example carries a
literal identifier value), no fabricated value was placed in the identifiers array for these two;
the stated fact of match/difference is instead carried through stated_relation /
authoritative_identifier.

## AD-08's blank-prefecture case

The input explicitly flags that 府中市 exists in both Tokyo and Hiroshima prefectures, i.e. the
town name match alone does not uniquely resolve the blank prefecture the way the worked
example's postal-code-plus-banchi corroboration does. corroborating_facts still lists the
town-name-and-banchi match (rule 2 forbids restating the blank itself as a corroborating fact, but
the matching town name and banchi are separately stated positive facts), with the ambiguity
flagged here rather than resolved, since resolving it is the engine's and not the extractor's job.

## AD-10, EO-06

AD-10 has two different, valid, stated corporate numbers at the same shared-office address; this
is read as stated_relation="same_site_only" plus stated_difference="different_legal_entity" plus
authoritative_identifier="conflicting" (a straightforward disagreement, both values valid). EO-06
compares a store record to a shared consolidation-point ship-to/site record for five stores'
worth of deliveries; object_type_a="store", object_type_b="site", one_to_many_object=true,
distinct_operational_attributes=true (the consolidation point handles delivery for five stores,
a distinct operational function from a single store's own delivery target).

## Unsupported tracks (RQ, SV, TM)

RQ (requirements) and SV (survivorship) answers are DECIDED/ASSUMED/INFERRED/UNKNOWN and source
names respectively, neither of which the fact-sheet schema's fields describe; every RQ and SV
sheet was left at full schema defaults (track and allowed_answers copied verbatim, everything
else neutral) since none of these fields plausibly bears on a requirements-provenance or a
survivorship-source-selection judgment. TM (temporal) sheets keep the schema defaults for the
merge-decision fields but populate effective_dates (as_of, candidate_effective_date, conflict)
from what each case actually states, since that field is genuinely meaningful for a temporal
record-authority question even though scripts/jbeq_decide.py's decide() does not output an
R1/R2/R3/NO-DATA-shaped answer. TM-09 is a genuine gap case (the query date 2026-03-15 falls
after R1's closure and before R2's reopening, a gap neither record covers); flagged here rather
than resolved.
