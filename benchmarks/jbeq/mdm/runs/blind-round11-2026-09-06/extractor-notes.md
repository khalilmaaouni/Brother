# JBEQ-MDM round 11 extractor notes (blind round on the frozen 70, 2026-09-06)

Files opened during phase 1 (read-only, blind, no other file under
benchmarks/jbeq/mdm opened before every fact sheet was written and hashed):

- benchmarks/jbeq/mdm/prompts/EXTRACTOR-PROMPT.md
- benchmarks/jbeq/mdm/fact-sheet-schema.json
- benchmarks/jbeq/mdm/round11-prompts/AD-01.md through AD-10.md
- benchmarks/jbeq/mdm/round11-prompts/EO-01.md through EO-10.md
- benchmarks/jbeq/mdm/round11-prompts/HI-01.md through HI-10.md
- benchmarks/jbeq/mdm/round11-prompts/ID-01.md through ID-05.md
- benchmarks/jbeq/mdm/round11-prompts/MM-01.md through MM-10.md
- benchmarks/jbeq/mdm/round11-prompts/RQ-01.md through RQ-05.md
- benchmarks/jbeq/mdm/round11-prompts/SV-01.md through SV-10.md
- benchmarks/jbeq/mdm/round11-prompts/TM-01.md through TM-10.md

That is 72 files total (2 reference files plus the 70 case prompts), and
nothing else under benchmarks/jbeq/mdm. No seed, no unseen set, no RECORD,
no runs/, no decision-rules-addendum.md, no jbeq_decide.py source, no test
was opened before this note and fact-sheets.json were written.

General note on requested_action: per the round 9 widening, a question of
the shape "this pair may be merged / matched / how should it be handled"
that compares two named candidate records is read as match_on_stated_basis
whenever the passage states a ground for treating them as the same (a
shared identifier, address, phone plus building, name, or a matching or
dedup step that already presented them as a candidate pair). I applied
this consistently across AD, EO, HI (non hierarchy write), ID and MM
cases. requested_action stays "none" for RQ (rule status classification,
no records being compared), SV (source survivorship, no records being
compared) and TM (temporal authority, asking which existing record already
governs, not proposing to write or merge anything now), and for single
record or fully blank cases with no stated second record or basis (AD-06,
AD-09, EO-08).

## Case by case judgment calls worth flagging

AD-03: town name and lot number match, but the city and ward differ
(Yokohama-shi Naka-ku vs Kawasaki-shi Kawasaki-ku): read as
location_comparison=different_administrative_area, a genuinely different
administrative area, not same_area_renamed (no renaming or merger is
stated) and not null (these are two different cities, not one place named
two ways within one municipality).

AD-05: old town name and postal code vs new city name and postal code
after a municipal merger, input explicitly states the lot number is the
same -> location_comparison=same_area_renamed per the round 7 definition.

AD-06: single record, registered address states Fukuoka, postal code on
the same record resolves to Sapporo. This is the same input, same record
contradiction rule 3 describes almost verbatim (a registered address in
one prefecture and a postal code belonging to a different one) ->
location_comparison=internally_inconsistent,
evidence_reasons=[unexplained_conflict],
contradicted_attributes=[registered_address, postal_code]. No second
record is named, so object_type_a/b both default to "address" and
requested_action stays "none".

AD-08: address A's prefecture is blank; address B states Tokyo; the input
flags that Fuchu-shi exists in both Tokyo and Hiroshima prefectures. I
extracted the blank field (prefecture) and one corroborating fact (town
name and lot number match address B), per rule A's worked example. The
Fuchu ambiguity sentence is a refuting or complicating fact, not a
corroborating one, so I did not fold it into corroborating_facts; it is
carried here as a note. It is the deciding engine's job to weigh it, not
mine.

AD-10: the two records share the exact same address and floor (a shared
office), so stated_relation=same_site_only, not "none" (the input does
state a relation between the two records: same site). one_to_many_object
is true because the passage explicitly states several legal entities
register their head office at that one floor. stated_difference is
different_legal_entity and authoritative_identifier is conflicting because
the two corporate numbers are both stated as valid and both different,
matching the extractor prompt's own worked example.

EO-01: head office vs branch, same corporate number, branch carries its
own credit limit and its own billing destination ->
distinct_operational_attributes=true (billing destination distinct is
explicitly stated). object_type_a/b set to commercial_account since the
records are customer records with distinguishable operational roles, not
plain legal_entity comparisons.

EO-02: payer's name is stated as the parent holding company, distinct from
the sold-to entity, with payment terms set separately ->
stated_relation=parent_child, distinct_operational_attributes=true
(payment terms). object_type_a=sold_to, object_type_b=payer per the
stated roles.

EO-03, EO-05, EO-10, MM-03, MM-04: the input states a corporate number
matches or differs between the two records but never gives the actual
digits. I left identifiers=[] rather than inventing a placeholder value
string, and carried the fact only through stated_relation,
stated_difference or authoritative_identifier, since a fabricated numeric
value would be exactly the kind of invented fact the extractor prompt
forbids.

EO-05: object roles are customer record and supplier record; the
fact-sheet-schema's object_type enum has no dedicated supplier value, so I
used the generic commercial_account on both sides and flag the true roles
(customer, supplier) here rather than force a mismatched enum value.

EO-06: the shipping master is stated to receive goods for five
neighbouring stores at the same site -> stated_relation=same_site_only,
one_to_many_object=true, object_type_b=site (a joint logistics hub, not a
single store).

EO-07: blank corporate number on record A, corroborated (not confirmed) by
a matching company name and address on record B; both records carry order
history in the past year; the assigned sales representative differs
between the two stated records -> contradicted_attributes=[sales_
representative] per rule 4's "an attribute both sides state that
disagrees."

HI-01: three hierarchy parents (capital, trade_flow, reporting), all
different, all valid, single parent master field, question asks whether to
collapse to one. This is the same shape as the schema's own
example_rule_D_hierarchy_keep_separate worked example, which sets
requested_action to "none" because no specific target type is named in the
request (it only asks in the abstract whether the three can be unified,
not which one becomes the parent) -> I followed that precedent:
requested_action=none, requested_relation_type=null, requested_parent=null.

HI-02: request to reverse which side is the capital parent (subsidiary's
revenue now exceeds the parent's) -> requested_action=assignment,
requested_relation_type=capital, requested_parent=the currently stated
subsidiary, since the request asks to flip which side is parent.

HI-04: request to set the legal or capital hierarchy parent to a sales
office (a rep territory office, not stated as a legal entity) ->
extracted literally: requested_action=assignment,
requested_relation_type=capital, requested_parent=the named sales office.
The mismatch between a capital hierarchy request and a non legal entity
target is a fact for the engine to weigh, not something I resolved by
picking a different type or declining to extract the request.

HI-05: franchise agreement explicitly denies any capital relationship or
shared board members -> stated_difference=different_legal_entity. The
registration request explicitly asks to register the franchisee as a
subsidiary in the legal hierarchy -> requested_action=assignment,
requested_relation_type=capital, requested_parent="brand headquarters" (the
only name this case's own text gives; I did not import a more specific
company name used in a different case's text, since each case is read
blind and independently).

HI-07: the input only says the entity "is a group company of" the group,
without stating whether that is a capital, trade flow, or reporting
relation. group_company_code_shared_entity specifically means a shared
GROUP COMPANY CODE was stated, which this input does not carry (it only
carries prose about group membership), so I did not force that value;
stated_relation stays "none". The question does ask which hierarchy the
parent-child should be registered under, which is a real request to write
a hierarchy parent, but the type it should use is exactly what is
undetermined -> requested_action=assignment with requested_relation_type
left null, since naming one of the three types here would be inventing
the fact the case is built to leave open.

HI-08: two business units in one group hold separate, company code keyed
customer records for the same counterpart, with differing credit limits
and pricing terms per code -> stated_relation=
group_company_code_shared_entity (the shared group company code shape this
enum value is for), distinct_operational_attributes=true.

HI-09: a geographic area hierarchy node and a sales organisation hierarchy
node share a name but their governed prefectures only partially overlap
-> stated_difference=different_hierarchy_dimension, the round 10 worked
case almost verbatim.

HI-10: goods flow through a stated trade flow counterpart and the request
asks to set the stores' capital parent to that same trade flow counterpart
-> stated_relation=trade_flow, requested_action=assignment,
requested_relation_type=capital, requested_parent=the named logistics
company. The mismatch between the stated relation type (trade_flow) and
the requested written type (capital) is extracted as is, not resolved.

ID-01, ID-04: same numeric value stated in two different identifier
domains purely by coincidence (internal customer number vs corporate
number in ID-01; store ID vs site ID in ID-04) ->
stated_difference=different_identifier_domain,
authoritative_identifier=conflicting, per the docstring's third
"conflicting" shape.

ID-02: qualified invoice issuer number stated as expired as of a named
date, current date given, no replacement number obtained yet ->
identifiers=[status: expired], blank_fields=[new_invoice_registration_
number]. No second record is named for comparison, so
requested_action=none.

ID-03: an unvalidated migration crosswalk table links an old ERP ID and a
CRM GUID, AND the two records separately state the same, valid corporate
number. Per rule 1 the crosswalk itself is only medium strength evidence,
but the corporate number is independently stated as matching and valid,
which the extractor prompt's own rule 1 treats as "strong" evidence in its
own right. I extracted evidence_strength=strong (carried by the corporate
number match) while still recording evidence_reasons=[unvalidated_
crosswalk] as a true fact about the crosswalk's own limited standing,
since rule 2 forbids treating an absence as corroboration but does not
forbid recording two independently true evidence facts side by side.

ID-05: a customer code was reused for a different legal entity after the
original holder's contract ended, and a matching engine presented the pre
and post reuse records as a candidate pair on that code match ->
lifecycle=identifier_reused, authoritative_identifier=conflicting,
requested_action=match_on_stated_basis (the passage already presents the
pair as candidates on a named basis, before any question is asked).

MM-01: three stores share one corporate number, each with its own
delivery destination, shelf allocation and sales booking destination ->
object_type_a/b=store per the extractor prompt's own explicit guidance
for this exact shape, distinct_operational_attributes=true (this is the
signal the prompt says round 4 missed for this case).

MM-05, MM-09: match score only candidates with a missing identifier
(MM-05: both corporate numbers blank, irreversible physical delete with no
undo; MM-09: one side's attributes are blocked by permission control) ->
evidence_strength=weak, evidence_reasons=[score_only, missing_identifier],
matching example_rule_B_weak_escalate.

MM-06: two company names are homophones with different kanji (a name
variant with the same reading) and the two addresses share the city but
differ in lot number, both explicitly named in rule 4 as
contradicted_attributes examples -> contradicted_attributes=[company_name,
lot_number]. Both corporate numbers are stated as not yet obtained ->
blank_fields=[corporate_number].

MM-07: a corporate number matches on both sides, but the registered
company name differs, and the registry explicitly records a dated name
change event that explains it -> evidence_reasons=[explained_conflict]
rather than unexplained_conflict, since the passage itself states the
reason for the disagreement, and contradicted_attributes=[company_name]
under rule 4, which does not require the disagreement to be unexplained
to count as a contradicted attribute.

MM-08: a matching engine's candidate crosses a stated contractual tenant
wall -> tenant_boundary=stated, stated_difference=contractual_wall, the
same shape as example_rule_C_reject_match.

MM-10: two payer records for one legal entity, differing payment terms
stated on both sides -> distinct_operational_attributes=true,
contradicted_attributes=[payment_terms].

RQ track (RQ-01..RQ-05): these ask whether an extracted rule is DECIDED,
ASSUMED, INFERRED or UNKNOWN against a stated source document. None of the
MDM identity fields (object_type, hierarchy_parents, location_comparison,
etc.) apply to this track, so every RQ sheet carries the schema defaults
with track="requirements" and allowed_answers copied from the prompt's own
DECIDED/ASSUMED/INFERRED/UNKNOWN list. The source facts for each case,
extracted here rather than forced into an inapplicable field:
  RQ-01: 2026-08-20 meeting minutes record an explicit approval, with no
    objections, of a 0.95 auto-merge threshold; the extracted rule states
    that same threshold verbatim.
  RQ-02: a migration ticket states existing payment terms carry over as
    is and that payment terms are managed in ERP alongside the contract;
    the extracted rule generalizes this to "ERP is the authoritative
    source," a step beyond what the ticket itself declares as a decision.
  RQ-03: a requirements document states only that records sharing a
    corporate number are merged, and is silent on store records; the
    extracted rule extends the stated rule to store records, a case the
    document does not address.
  RQ-04: a BRD explicitly defers the address normalization rule to a
    separate, not yet made decision, and no decision record exists; the
    extracted rule (normalize to full width) is not stated anywhere in
    the input.
  RQ-05: a dated 2026-09-01 decision record states an explicit approval,
    naming an approver role, confirming ERP as the payment terms source;
    the extracted rule matches that record's own words.

SV track (SV-01..SV-10): these ask which source's value should survive
for one attribute. None of the MDM identity fields apply (no candidate
record pair, no address, no hierarchy), so every SV sheet carries the
schema defaults with track="survivorship" and allowed_answers copied from
the prompt's own source name list. Where the input states blank values or
governing dates I carried those into blank_fields or effective_dates
(SV-05, SV-06, SV-08, SV-09, SV-10); I did not force contradicted_
attributes for a same-attribute-different-source disagreement, since rule
4 defines that field for two CANDIDATE RECORDS disagreeing, not two
SOURCES of one record's attribute disagreeing. SV-07's two competing
identifiers (an MDM assigned golden ID and a CRM assigned internal
management number) are named by domain only; no digit values are stated,
so identifiers stays empty and only authoritative_identifier=conflicting
carries the fact. SV-08 is the one SV case with a genuine effective_dates
conflict: the ERP update's own source_timestamp (2026-02-10) disagrees
with which date should govern the name today against the corporate
registry's later effective date (2026-04-01), so conflict=true there.

TM track (TM-01..TM-10): these ask which existing record (R1/R2/R3)
already governs as of a stated date; none propose a merge, so
requested_action stays "none" throughout. Facts map onto effective_dates
(as_of = the query date, candidate_effective_date = the later record's
valid_from or a reuse or merge event date) and onto lifecycle for closed
or superseded predecessor records. Every TM case's own dates order
cleanly on inspection, so conflict=false throughout the TM sheets. TM-04's
merge then unmerge history and TM-09's genuine after close, before reopen
coverage gap are recorded here rather than in a dedicated field, since the
schema has no separate slot for either: TM-04's merge event date is
carried as candidate_effective_date (2026-05-01) with the 2026-06-10
unmerge date noted only here; TM-09's query date (2026-03-15) falls
strictly between R1's valid_to (2025-12-31) and R2's valid_from
(2026-04-01), a real gap the sheet's lifecycle=closed and effective_
dates.as_of alone do not fully carry.

No value outside the schema's _enums lists was used in any enum governed
field; where the input's own words did not map cleanly onto one of those
values (HI-07's undated group company language, HI-04/HI-05/HI-10's
mismatched hierarchy type requests), the literal stated fact was
extracted and the mismatch is left for the engine and for validate() to
surface, never resolved by picking a friendlier enum value.
