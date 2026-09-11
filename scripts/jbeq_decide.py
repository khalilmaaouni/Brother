#!/usr/bin/env python3
"""JBEQ-MDM decision module: a deterministic engine that decides once a
blind reader has filled out a FACT SHEET, so the engine (not a fresh prompt
read) applies the rules consistently.

WHY THIS EXISTS. The founder's ruling of 2026-09-05 on JBEQ-MDM blind round 4
("Engine decides, model extracts") is that a blind model should only extract
structured facts from a prompt, never apply the rules itself: three blind
rounds against the same rules-as-prose showed a fresh reader collides rules
that look alike (see benchmarks/jbeq/mdm/decision-rules-addendum.md and the
diagnosis this module was built from). This file is the engine half of that
split. It never reads a benchmark seed and never sees an expected answer; it
only takes a fact sheet and a fixed rule table and returns one answer.

THE FACT SHEET. A JSON object per case, built by a BLIND reader from the
prompt text alone (see benchmarks/jbeq/mdm/fact-sheet-schema.json for a
worked example). Required keys, always present (value may be null, "none",
false or an empty list when the fact does not apply to a case; the key
itself must exist so a fact the extractor never considered is NEVER read as
"nothing to see here" by silent default):

  track                str    the case's track label, copied from the prompt
  allowed_answers      list   the case's own allowed-answer list, copied
                               verbatim from the prompt's "許容される回答"
  identifiers          list   [{value, domain, legal_person_type, status}]
                               domain: corporate_number | invoice_number |
                                 internal_sequence | tax_id | unknown
                               legal_person_type: corporation | individual |
                                 unknown
                               status: valid | expired | blank
  stated_relation      str    none | corporate_number_shared | parent_child |
                               role_pair | trade_flow | same_site_only |
                               group_company_code_shared_entity
                               (the last value is an extension of boundary
                               rule 7: two or more company-code-specific
                               records the input states name one external
                               entity)
  stated_difference    str    none | different_legal_entity |
                               different_tenant | contractual_wall |
                               different_identifier_domain |
                               different_hierarchy_dimension
                               (the last value: the two hierarchy nodes
                               being compared live in different hierarchy
                               dimensions, for instance a geographic area
                               hierarchy versus a sales organisation
                               hierarchy, so a shared name is never a
                               refuting basis for identity)
  tenant_boundary       str   none | stated
  evidence_strength     str or null   strong | medium | weak | null
                               null means this case is not a merge candidate
                               at all (no score, no identifier match is being
                               proposed), so the merge ladder never applies.
  evidence_reasons      list  zero or more of: unexplained_conflict,
                               explained_conflict, unvalidated_crosswalk,
                               score_only, missing_identifier
  corroborating_facts   list  facts, in the extractor's own words, that
                               support a blank or missing field without
                               confirming it outright
  blank_fields          list  field names the input states are blank,
                               expired, or otherwise unfilled
  irreversible          bool  the candidate action (a merge, a delete) cannot
                               be undone if wrong
  history_exists        bool  transaction history exists on one or both
                               sides and a person should look before merging
  hierarchy_parents     list  [{type, parent}], type: capital | trade_flow |
                               reporting
  one_to_many_object    bool  one record serves several of the other kind
                               (a shared delivery hub, a consolidation
                               center); extension of boundary rule 5

One key stays RESERVED, documented here because an earlier brief asked for
it, but UNUSED by decide() today: source_precedence {authoritative_source,
overriding_source, override_validity: valid | expired}. No rule handed to
this module defines a survivorship (SV) or requirements (RQ) answer
vocabulary, so decide() honestly returns NO-DATA naming the track
unsupported for those two rather than guess at one (see UNSUPPORTED_TRACKS).

IDENTITY-KIND FIELDS, added round 5 (2026-09-06, design-p0-3-mdm-merge-
safety section B), because MM-01, HI-02, HI-10 and AD-03 in the round 4
blind read were unreachable on the 14 fields above: legal identity,
commercial identity, account identity, location identity and store identity
were all being scored on one ladder. requested_relation_type and
contradicted_attributes existed before round 5 as OPTIONAL fields read with
.get(); they are now REQUIRED, always present, like every other field:

  object_type_a            str   one side's kind of record: legal_entity |
  object_type_b            str    commercial_account | payer | sold_to |
                                   ship_to | store | site | hierarchy_node |
                                   address. Two different kinds are never
                                   the same operational object even with
                                   matching legal identifiers (gate
                                   "object_type" below).
  lifecycle                 str   active | closed | relocated |
                                   identifier_reused. A closed or
                                   reused-identifier record never silently
                                   folds into a live one; a relocated record
                                   is a compatible lifecycle of the SAME
                                   object, so it bars AUTO-MERGE only, never
                                   SUGGEST MERGE (gate "lifecycle", round 9,
                                   2026-09-06, id relocated_not_a_bar).
  requested_action           str  none | assignment. Whether the input asks
                                   for something to be WRITTEN, separate
                                   from what the input STATES. Documented
                                   for the schema; decide() itself still
                                   keys the requested-versus-stated check
                                   below on requested_relation_type alone,
                                   exactly as before round 5, so no
                                   already-proven case flips.
  requested_relation_type   str or null   capital | trade_flow | reporting |
                                   null. The hierarchy type an input asks to
                                   WRITE, versus the type(s)
                                   hierarchy_parents already states
                                   (HI-02, HI-10; see rule link_vs_reject).
  requested_parent          str or null, OPTIONAL (round 6, 2026-09-06).
                                   The entity the request asks to become the
                                   parent, when requested_relation_type
                                   matches a stated hierarchy TYPE but the
                                   input asks to REVERSE which side is the
                                   parent (HI-02; see rule link_vs_reject).
                                   Absent or null on every case that does
                                   not ask for a hierarchy reversal.
  authoritative_identifier  str   aligned | conflicting | absent. Whether
                                   the identifiers that should govern this
                                   decision actually agree, replacing the
                                   overloaded use of evidence_strength for
                                   that question (gate
                                   "authoritative_identifier").
  contradicted_attributes  list   attribute names the input's OWN fields
                                   disagree about, distinct from
                                   blank_fields (simply unfilled, not
                                   contradicted). Read by the weak-evidence
                                   split above (MM-06).
  distinct_operational_attributes bool   each side carries its own
                                   delivery, booking or pricing target
                                   (MM-01). True is a relationship signal,
                                   never a merge signal (gate "site_store").
  location_comparison       str or null   notation_variant_only |
                                   same_chiban_different_notation |
                                   different_administrative_area |
                                   different_unit_in_building |
                                   internally_inconsistent |
                                   same_area_different_lot | null (AD-03).
                                   different_administrative_area,
                                   internally_inconsistent,
                                   notation_variant_only and
                                   same_chiban_different_notation are all
                                   read by rule L below (the last two added
                                   round 6, 2026-09-06); same_area_different_lot
                                   (round 12, 2026-09-07, id
                                   same_area_different_lot_keeps_separate)
                                   is also read by rule L, and answers
                                   KEEP SEPARATE, never REJECT MATCH and
                                   never a merge: a differing lot number
                                   alone, in the same town and block, is an
                                   absence of support under addendum
                                   rule 6, not a refutation.
                                   different_unit_in_building is read
                                   separately, by rule_r, only when
                                   stated_relation == "none". null falls
                                   through to the ordinary relation and
                                   evidence rules.
  effective_dates            dict  {as_of, candidate_effective_date,
                                   conflict, prior_valid_to}. Promoted from
                                   reserved to required round 5:
                                   conflict=true blocks an otherwise-
                                   AUTO-MERGE case (gate "temporal"). The
                                   "temporal" TRACK is no longer in
                                   UNSUPPORTED_TRACKS as of round 5: it is
                                   decided by the ordinary rules below like
                                   any other track, using this field where
                                   a case reaches the merge ladder.
                                   as_of is the date the QUESTION asks
                                   about, never today; candidate_
                                   effective_date is the date the LATER
                                   (successor) record takes effect, never
                                   the earlier record's own start date.
                                   prior_valid_to, added for the temporal
                                   record rules below (review-temporal-
                                   track-2026-09-06.md), is the earlier
                                   record's own valid-to date, null when
                                   the input does not state one.

THE PRECEDENCE ORDER, fixed and documented once here (decide() below is a
straight-line implementation of this list, checked in this order, first
match wins):

  0. Every required key must be present (see REQUIRED_FIELDS). Missing any
     one is NO-DATA naming that field. This runs before every rule below.
  1. Track SV or RQ (survivorship, requirements): NO-DATA naming the track
     unsupported. These two tracks answer from their own vocabulary, which
     no rule handed to this module defines. The temporal (TM) track left
     this set round 5: it is decided by the ordinary rules below.
  1.5. Temporal record rules (temporal_at_or_after_successor_is_r2,
     temporal_before_successor_is_r1, temporal_gap_is_nodata; review-
     temporal-track-2026-09-06.md), only when "R1" is a member of the
     case's own allowed_answers, never gated on track (so U1 to U4's
     TM-flavored cases, which answer from the decision vocabulary below
     instead, reach item 2 untouched). as_of at or after
     candidate_effective_date: R2, the successor record governs. as_of
     before candidate_effective_date and lifecycle not closed: R1, the
     earlier record is still in force. as_of before
     candidate_effective_date, lifecycle closed, and no prior_valid_to
     stated: NO-DATA, naming the dormancy gap that cannot be ruled out. A
     month-precision date collision (same year and month, either side
     missing a day) leaves the order unknowable, so none of the three
     fires and the case falls through to item 2.
  2. Rule D (hierarchy types independent): 2 or more hierarchy_parents of
     DIFFERENT types, each with a parent named, is not one conflict; answer
     LINK AS RELATED. This runs before any relation or difference check
     because a multi-type hierarchy is never "the same object needing one
     merge decision" in the first place. CHANGED round 8 (2026-09-06) per
     the founder's ruling in the question UI ("LINK AS RELATED wins;
     correct HI-01 as a recorded benchmark defect (Recommended)"),
     recorded at docs/decisions/decision-p0-3-rule-d-hi01-2026-09-06.json:
     the rule just found three valid relations, and KEEP SEPARATE (no
     relation to record) contradicted its own reason text and the
     directive's section 18 vocabulary. HI-01's frozen seed label is
     corrected alongside this change (benchmarks/jbeq/mdm/seed-2026-09-05.json,
     field label_corrected on that case), net zero on the frozen count.
     Its id "D" stays exposed via JBEQ_DECIDE_DISABLE_RULES so this rule
     can still be proven load-bearing with the same mutation seam.
  3. Rule C (identifier-domain and tenant boundary): tenant_boundary
     "stated", or stated_difference one of different_tenant,
     contractual_wall, different_identifier_domain: REJECT MATCH. A numeric
     coincidence across domains or a stated tenant wall is a stated fact
     that the two are not the same or must never link, which boundary rule 2
     and rule 6 both require before REJECT MATCH is available; this is
     always checked ahead of the merge ladder, per the founder's ruling, so
     a strong-looking match never merges past a stated wall.
  4. Rule link_vs_reject, ahead of boundary rule 2 (added 2026-09-06 per the
     design review section C, closing EO-02 and EO-09): REJECT MATCH is for
     a PROPOSAL the input's own facts refute, never for a case where the
     input also states a relation. So: if requested_relation_type is set
     (today it never is; see above) and names a type hierarchy_parents does
     not carry, REJECT MATCH outright, the proposal itself is refuted. Else,
     if stated_difference == different_legal_entity and stated_relation !=
     "none", the two facts together mean "not the same legal entity, but a
     relation IS stated": LINK AS RELATED, not REJECT MATCH. Only when
     neither of those applies does boundary rule 2's original generic case
     run: stated_difference == different_legal_entity with no stated
     relation at all: REJECT MATCH.
     F1a (round 11, 2026-09-06, review-u4-2026-09-06.md W4-04, id
     link_vs_reject_write_refutes): a stated relation answers what the
     relation is, it does not license WRITING one code over the other.
     requested_action == record_write is a proposal to write, so it never
     confirms a relation here: the refuting stated_difference wins and
     boundary rule 2's REJECT MATCH runs instead of LINK AS RELATED.
  5. Rule 5 (site-only, one-to-many; hoisted above rule L round 10,
     2026-09-06, closes W-04): stated_relation == same_site_only and
     one_to_many_object: KEEP SEPARATE. A shared address alone is never the
     stated relation boundary rule 1 asks for, and collapsing a one-to-many
     object into one record misattributes every other side's records. This
     runs BEFORE rule L (next) so a notation-variant-only address match on
     a one-to-many, shared-address case cannot reach rule L's AUTO-MERGE
     gates and answer off gate-object-type before rule 5 is ever read; id
     "5" is unchanged. QA round (2026-09-06, qa-review-jbeq-2026-09-06.md
     M1): the hoisted position also checks id rule5_hoist, so disabling
     rule5_hoist alone re-lowers rule 5 below rule L without deleting the
     rule (a different mutation from disabling "5", which removes it
     everywhere).
  6. Rule L (location comparison, added round 5, closes AD-03; grew round
     6, 2026-09-06, closes AD-01/AD-02/AD-05/AD-07; grew round 10,
     2026-09-06, closes W-01):
     location_comparison == different_administrative_area: REJECT MATCH,
     the two addresses disagree at the administrative-area level despite
     any town or lot number match, UNLESS lifecycle == relocated (id
     relocated_address_not_a_refutation), in which case a relocation's own
     address difference never refutes identity and the ladder falls
     through to the merge rules below instead. location_comparison ==
     internally_inconsistent: ESCALATE, the input's own address fields
     disagree with each other. location_comparison == notation_variant_only:
     the same AUTO-MERGE gate the merge ladder's strong-evidence branch
     uses (object type, lifecycle, authoritative identifier, stated
     relation, temporal), then AUTO-MERGE if nothing blocks it.
     location_comparison == same_chiban_different_notation: SUGGEST MERGE.
     location_comparison == same_area_renamed (added round 7, 2026-09-06,
     closes the extraction half of U-03; changed by founder ruling
     2026-09-06, decision-p0-3-same-area-renamed-2026-09-06, id
     renamed_area_needs_a_person): one place under an old and a new
     administrative name, never two different places, but a rename is a
     fact about the map, not confirmation the two rows are one, so this
     answers SUGGEST MERGE, a person confirms. Disabling
     renamed_area_needs_a_person reverts to the pre-ruling behaviour: the
     same AUTO-MERGE-gate-checked path notation_variant_only uses.
     location_comparison == same_area_different_lot (added round 12,
     2026-09-07, per ~/.claude/evidence/review-rule6-2026-09-07.md ranking
     item 2, closes U3 W-03): two addresses that state the same town and
     block and differ only in lot number, with nothing else stated. Per
     addendum rule 6, a differing lot number alone is an absence of
     support, never a refutation, so this answers KEEP SEPARATE, never
     REJECT MATCH and never a merge (own id
     same_area_different_lot_keeps_separate; disabling it falls through to
     whichever rule would apply next, exactly as if the location_comparison
     value carried no signal at all).
     different_unit_in_building is read separately, by rule_r below.
  7. Rule 7 (group company-code records): stated_relation ==
     group_company_code_shared_entity: LINK AS RELATED. An operational
     reason to keep company-code records apart is never a reason to record
     no relation between them.
  8. The merge ladder, rule B refining rule 4 (only when evidence_strength is
     not null, i.e. this case actually proposes a merge or link on evidence):
       evidence_strength == weak, split three ways (2026-09-06, closing
       MM-09; MM-06 stays wrong until an extractor emits one of the first
       two signals, see the design review section A):
         contradicted_attributes non-empty, or evidence_reasons carries
           unexplained_conflict: KEEP SEPARATE. A stated contradiction is
           itself the answer, not a reason to look further.
         evidence_reasons carries ONLY score_only (nothing else observable:
           not irreversible, no history): NO-DATA. A bare match score with
           no aggravating factor is not enough to decide OR to force a
           human look.
         otherwise: ESCALATE, always. Irreversibility or history never
           upgrade weak evidence into a merge.
       evidence_strength == strong and evidence_reasons is empty and not
         irreversible and not history_exists: this is what rule B alone
         would call AUTO-MERGE. Round 5 adds an AUTO-MERGE SAFETY GATE here
         (_auto_merge_blocked, design section C): legal-identity evidence
         this strong still must not silently collapse two different kinds
         of object, a closed or relocated record, an unaligned
         authoritative identifier, a site or store distinction, or a
         temporal conflict. The gate returns the safer answer (LINK AS
         RELATED, KEEP SEPARATE or SUGGEST MERGE) if any of its items fire;
         only if none of them fire does AUTO-MERGE stand. Each gate item
         has its own JBEQ_DECIDE_DISABLE_RULES id: object_type, lifecycle,
         authoritative_identifier, site_store, temporal.
       evidence_strength == medium and not irreversible and not
         history_exists (round 10, 2026-09-06, closes W-19, id
         medium_needs_confirmation): ESCALATE. Medium evidence with no
         confirmation reason stated separately from that evidence itself
         must not fall through to SUGGEST MERGE; an unvalidated migration
         crosswalk is medium evidence and must never also count as its own
         confirmation reason.
       otherwise (medium with a reason or irreversible or history_exists,
         or strong with any reason, or irreversible, or history_exists):
         SUGGEST MERGE.
     evidence_strength is null (not a merge candidate) but evidence_reasons
     carries unexplained_conflict (2026-09-06, closing AD-06): ESCALATE.
     Before this fix evidence_reasons was only read inside the
     evidence_strength-is-not-null branch, so a stated conflict on a case
     that never proposed a merge was silently discarded.
  9. Rule A (ESCALATE vs NO-DATA): blank_fields is non-empty:
       corroborating_facts non-empty: ESCALATE.
       corroborating_facts empty: NO-DATA naming the blank field(s).
  10. Boundary rule 1, the default: stated_relation != "none": LINK AS
     RELATED. stated_relation == "none": KEEP SEPARATE. F2 (round 11,
     2026-09-06, review-u4-2026-09-06.md W4-23, id
     same_site_only_never_a_relation): stated_relation == same_site_only
     reads like "none" here too, not like a stated relation, so a shared
     address alone still answers KEEP SEPARATE rather than falling through
     to LINK AS RELATED; rule_r's own stated_relation == "none" guard reads
     same_site_only the same way, for the same reason.
  11. Safety net: if the rule table produced an answer the case's own
      allowed_answers does not carry, remap it (round 7, 2026-09-06, id
      allowed_answers_remap) by walking jbeq_mdm.CAUTION_RANK from the
      chosen answer toward the conservative end, then back toward the
      aggressive end, taking the first member of allowed_answers reached;
      rule_fired stays the original rule's id. NO-DATA, naming the
      mismatch, is reserved for the case where allowed_answers carries
      nothing this module recognizes at all.

Usage:
  python3 scripts/jbeq_decide.py decide <fact-sheets.json> [--out answers.json]
      [--decisions decisions.jsonl]
  python3 scripts/jbeq_decide.py validate <fact-sheets.json>

<fact-sheets.json> is a JSON object of {"CASE-ID": {fact sheet}, ...}.

A mutation control for tests: JBEQ_DECIDE_DISABLE_RULES takes a comma list
of rule ids (D, C, link_vs_reject, L, 2, 5, 7, site_store, rule_r, B, A,
plus the round 5 AUTO-MERGE gate ids object_type, lifecycle,
authoritative_identifier, temporal, stated_relation_gate, plus the round 6
ids relation_partition, site_store_weak_guard and
rule_r_blank_fields_guard, plus the round 7 ids rule_r_lifecycle_keep and
allowed_answers_remap, plus the round 8 id proposal_gate, plus the round 9
id relocated_not_a_bar, plus the round 10 ids rule_c_hierarchy_dimension,
medium_needs_confirmation and relocated_address_not_a_refutation, plus the
2026-09-06 founder-ruling id renamed_area_needs_a_person, plus the
round 11 ids (2026-09-06, review-u4-2026-09-06.md)
link_vs_reject_write_refutes, site_store_write_refutes and
same_site_only_never_a_relation, plus the round 12 id (2026-09-07,
review-rule6-2026-09-07.md) same_area_different_lot_keeps_separate) to
same_site_only_never_a_relation, plus the QA round id (2026-09-06,
qa-review-jbeq-2026-09-06.md M1) rule5_hoist, a second guard on rule 5's
hoisted position so the ordering can be mutated without also removing the
rule, plus the temporal record ids (2026-09-06, review-temporal-track-
2026-09-06.md) temporal_at_or_after_successor_is_r2,
temporal_before_successor_is_r1 and temporal_gap_is_nodata) to
disable, one at a time or several together, so
a test can prove ONE rule is load-bearing
rather than only that the whole table is. A disabled rule's condition is
simply never checked; the ladder falls through to whichever rule would
apply next, exactly as if that rule had been deleted. The bare value "1"
(no comma) keeps its old meaning of "disable everything", for backward
compatibility and because rule id "1" (boundary rule 1, the terminal
default) cannot itself be selectively disabled through this interface
without colliding with that sentinel; boundary rule 1 is therefore always
active. With everything disabled, decide() always returns NO-DATA with
rule_fired="rules-disabled-for-test". This exists so a test suite can prove
it is testing something real: with a rule disabled, any test that asserts
that rule's specific answer must fail.

EVERY ID ABOVE IS ALSO IN KNOWN_RULE_IDS (2026-09-06, qa-review-jbeq-
2026-09-06.md C2): an unrecognized token used to be accepted here and
silently disabled nothing, so a mistyped mutation id looked like a
passing test for the wrong reason. _disabled_rules() now raises
ValueError naming any token outside KNOWN_RULE_IDS, and the `decide` CLI
turns that into a non-zero exit with the token on stderr, before
anything runs.

THE MUTATION SEAM IS FAIL LOUD AND FAIL CLOSED (hub PR 386 security
finding, 2026-09-06): this environment variable is a fail-open test hook
by design, and a fail-open hook left silent is a production risk, so
JBEQ_DECIDE_DISABLE_RULES set to anything makes itself impossible to miss
and impossible to record as a real answer:

  LOUD. Every result decide() returns while it is set carries
  "mutation": {"disabled": [...]} and a "why" prefixed "MUTATION SEAM
  ACTIVE (rules disabled: ...): " (see decide() below, which wraps
  _decide_unwrapped for exactly this). cmd_decide also prints one stderr
  banner per invocation naming the disabled ids, and both decisions.jsonl
  (per row) and the answers.json file itself (under the "_mutation" key,
  which no real case id can collide with) carry the same marker, so a
  caller reading any one of the three artifacts can tell a mutated run
  from a real one without reading the others.

  CLOSED. cmd_decide (and scripts/jbeq_mdm.py's extract subcommand)
  refuse outright, before writing anything, when a seam is active and the
  destination resolves under benchmarks/jbeq/mdm/runs/: a mutated run must
  never become a committed record by accident. scripts/jbeq_mdm.py score
  refuses (exit 3) to score any answers file carrying the marker, either
  in the file itself or in a decisions.jsonl beside it, unless the caller
  passes --mutation-report, which scores anyway under a "MUTATION REPORT,
  not a record" header and writes no file either way.

ROUND 5 REPAIR (2026-09-06), per ~/.claude/evidence/review-round5-delta-
2026-09-06.md and FIX-DIRECTIVE-2026-09-06.md sections 15 to 22. The round
5 blind run scored worse than round 4 (47 of 70 versus 50) purely because
`validate` checked key PRESENCE only, never value membership: the round 5
extractor wrote free text into stated_relation and stated_difference (no
vocabulary was ever handed to it for those two fields), 20 of 45 sheets
carried a value matching no rule, and every one silently fell to the
boundary-rule-1 default. Five general fixes, all inside decide() and
validate:

  0.5 ENUM VALIDATION (the L12 fix). `_load_enums()` reads the closed
      vocabularies from benchmarks/jbeq/mdm/fact-sheet-schema.json's
      "_enums" key, the one source of truth. `cmd_validate` now refuses a
      sheet whose value for a documented enum field is outside that
      field's list, naming the field and the value, in addition to its
      existing missing-field check. decide() runs the same check right
      after the missing-field check and returns NO-DATA with rule_fired
      "unrecognized-value" naming the field and value, rather than let an
      unrecognized value silently match no rule and fall through: a typo
      is now distinguishable from an omission. `decide(sheet,
      allow_unrecognized=True)` (CLI: `decide --allow-unrecognized`) skips
      this short-circuit for a historical re-decision whose sheets predate
      the fix: the rule table still runs on the sheet's raw values (a
      still-off-enum field simply matches no rule, as before), and each
      such sheet's unrecognized fields are recorded on its decision row
      (`unrecognized_values`) instead of being silently dropped.
  site_store, promoted from a gate reachable only inside a strong-evidence
      AUTO-MERGE candidate to a top-level rule, checked just above rule_r,
      right after rule 7: distinct_operational_attributes true is a
      relationship signal regardless of evidence_strength (closes MM-10,
      HI-08, both of which have evidence_strength null and so never
      reached the old gate at all).
  rule_r, between rule 7 and the merge ladder, firing only when
      stated_relation == "none" (load-bearing: without this gate EO-09,
      which states parent_child, would wrongly flip from LINK to REJECT):
      REJECT MATCH when authoritative_identifier is "conflicting", or two
      identifiers share the same value in different domains or carry
      different legal_person_type, or object_type_a != object_type_b, or
      lifecycle is closed/identifier_reused (round 9, 2026-09-06: relocated
      dropped from this clause, id relocated_not_a_bar; a relocated record
      is a compatible lifecycle of the same object, so it no longer bars
      reaching the merge ladder here), or location_comparison is
      different_unit_in_building.
  link_vs_reject's HI-05 guard: dropped the `stated_hierarchy_types and`
      conjunct, so a requested_relation_type set against an EMPTY
      hierarchy_parents (nothing stated at all) is now correctly seen as a
      refuted proposal, not skipped by the short-circuit.
  rule A's observability test, widened beyond corroborating_facts alone to
      also cover identifiers, contradicted_attributes,
      effective_dates.as_of, effective_dates.candidate_effective_date and
      hierarchy_parents (closes ID-02; does not close AD-08, whose sheet
      states nothing observable at all).
  stated_relation_gate, a sixth (now: fifth, since site_store moved out)
      AUTO-MERGE safety-gate item in _auto_merge_blocked: stated_relation
      != "none" gives LINK AS RELATED before AUTO-MERGE stands, closing
      the unflagged false merge EO-05 (an AUTO-MERGE cleared on neutral
      defaults never read from the input). At the time this note was
      written, EO-03, MM-04 and MM-07 had stated_relation == "none" and
      were unaffected; the round 6 re-extraction of the same frozen prompts
      filled all three as corporate_number_shared instead (still a truthful
      reading of the same input), which is exactly the case the round 6
      repair immediately below addresses.

ROUND 6 REPAIR (2026-09-06), per ~/.claude/evidence/review-round6-2026-09-06.md
sections A and B group 1, itself scoped by ~/.claude/evidence/
FIX-DIRECTIVE-2026-09-06.md sections 15 to 22. One general fix, applied at
two call sites:

  IDENTITY_BEARING_RELATIONS / RELATIONSHIP_ONLY_RELATIONS (defined above
      REQUIRED_FIELDS): stated_relation's enum was being read as one
      undifferentiated set in both link_vs_reject and the gate-stated-
      relation AUTO-MERGE safety item, when a shared corporate number is
      itself identity evidence (never merely "a relation" to record
      instead of merging), and a shared office address is never a
      confirming relation at all (rule 5's own reasoning, extended here to
      link_vs_reject).
  gate-stated-relation, narrowed from firing on any stated_relation !=
      "none" to firing only on a RELATIONSHIP_ONLY_RELATIONS value: closes
      EO-03, MM-04 and MM-07 (corporate_number_shared with otherwise-clean
      strong evidence now reaches AUTO-MERGE instead of being downgraded to
      LINK AS RELATED for stating what is actually identity evidence, not a
      relation). EO-01, EO-05 and MM-01 are unaffected because all three
      carry distinct_operational_attributes=true and are already caught by
      the site_store rule before evidence_strength is ever read; EO-10 and
      ID-03 are unaffected because both leave rule B through the SUGGEST
      MERGE path (history_exists or a stated reason) before the gate runs
      at all.
  link_vs_reject's different_legal_entity downgrade to LINK AS RELATED,
      narrowed to exclude same_site_only specifically (every other
      relationship-only or identity-bearing value still confirms,
      unchanged): closes AD-10 (same_site_only paired with
      different_legal_entity now falls through to rule 2's REJECT MATCH
      instead of being read as a confirmed relation). EO-02, EO-09 and
      HI-06 are unaffected: none of the three carries same_site_only.
  Both changes share one mutation-seam id, relation_partition: disabling
      it reverts both call sites to their pre-round-6 behaviour (fire, or
      confirm, on ANY non-none stated_relation), which is exactly the
      defect this repair closes.

  rule B's null-strength branch (review section A ID-02), widened from
      "unexplained_conflict" specifically to any evidence_reasons value: a
      case with evidence_strength=null (not a merge candidate at all) but
      a STATED reason of any kind must not be silently discarded just
      because the specific reason is not unexplained_conflict. Closes
      ID-02 (missing_identifier); AD-06 (unexplained_conflict) is
      unaffected, unchanged from round 5.

  Two priority inversions (review section B group 2, AD-09 and EO-07):
  an early rule was outranking an honest refusal or asserting a relation
  on evidence too weak to assert anything.
      rule_r's object-type clause (own id rule_r_blank_fields_guard) does
          not fire when blank_fields is non-empty: an INFERRED difference
          (two distinct object_type values) must never outrank a STATED
          absence. Closes AD-09 (object_type_a/b come from a field left
          almost entirely blank, so rule A's NO-DATA refusal now runs
          instead); ID-04 (blank_fields empty) is unaffected.
      site_store (own id site_store_weak_guard) does not fire when
          evidence_strength == "weak": weak evidence never asserts a
          relation either. Closes EO-07 (the only one of the seven
          distinct_operational_attributes=true sheets carrying weak
          evidence; the other six keep LINK AS RELATED, unaffected).

  requested_parent (review section C HI-02), one new OPTIONAL schema key
      (string or null): requested_relation_type matching a stated
      hierarchy TYPE is not the same as matching its DIRECTION. HI-02
      asks to REVERSE parent and child inside the same capital hierarchy,
      which the existing type check cannot see. Inside link_vs_reject's
      existing block: when requested_relation_type IS among the stated
      hierarchy types (the type check above did not refute it) and
      requested_parent names something other than the stated parent for
      that type, REJECT MATCH. HI-02's pinned round 6 sheet predates this
      key and so stays wrong until a fresh extraction fills it; HI-04,
      HI-05 and HI-10 are unaffected, all three already reaching REJECT
      MATCH through the type-mismatch branch above this one.

  Rule L grown to read two enum values it never read (review section B
      group 3, flagged the WEAKEST of the round's changes, landed last and
      in its own commit so it can be reverted alone): notation_variant_only
      maps onto the merge ladder's AUTO-MERGE gate path (it must still pass
      every gate item, exactly like a strong-evidence AUTO-MERGE
      candidate); same_chiban_different_notation maps to SUGGEST MERGE.
      Closes AD-01, AD-02, AD-07 (notation_variant_only) and AD-05
      (same_chiban_different_notation) if the gates agree; see the commit
      that lands this for exactly which did.

ROUND 7 REPAIR (2026-09-06), per ~/.claude/evidence/review-u1-2026-09-06.md
(opus review of qualification run U1, hub PR 404) and FIX-DIRECTIVE-2026-09-06.md
sections 15 to 22 and 36. Four general fixes, plus one item deliberately left
unchanged:

  rule_r_lifecycle_keep: rule_r's own lifecycle clause used to return
      REJECT MATCH while _auto_merge_blocked's identical lifecycle gate
      (gate-lifecycle, above) returns KEEP SEPARATE for the SAME fact: the
      engine contradicted itself (U-36). rule_r's lifecycle clause now
      returns KEEP SEPARATE, its own mutation id so a test can prove it is
      load-bearing on its own. Frozen exposure: zero (ID-05, the only
      frozen sheet with a non-active lifecycle, reaches rule 2 first).
  rule_r_blank_fields_guard, widened: the AD-09 guard (round 6) already
      established that an INFERRED difference (an object_type mismatch)
      must never outrank a STATED absence (blank_fields). U-38 showed the
      same is true of rule_r's lifecycle clause against a STATED conflict:
      widened from "blank_fields non-empty" to "blank_fields, or
      evidence_reasons, or contradicted_attributes, non-empty", and now
      guards the lifecycle clause as well as the object_type clause. Same
      mutation id, kept, since disabling it must revert both clauses to
      their pre-guard behaviour together.
  allowed_answers_remap: finish()'s old safety net converted any
      disallowed answer straight to NO-DATA, which is itself sometimes not
      in the case's own allowed_answers (U-23) and was wrong on all three
      cases it fired on in U1 (U-05, U-23, U-38). It now walks
      jbeq_mdm.CAUTION_RANK from the chosen (disallowed) answer toward the
      conservative end, taking the first member of allowed_answers it
      finds; if none exists there, it walks back toward the aggressive
      end. NO-DATA is reserved for the case where nothing in
      allowed_answers is reachable at all. rule_fired stays the ORIGINAL
      rule's id (never "safety-net") whenever a remap succeeds, so the
      decision row still names which rule actually decided.
  same_area_renamed, a new location_comparison value (schema and
      EXTRACTOR-PROMPT.md; review section F, closing the extraction half
      of U-03): one place named under both an old and a new
      administrative name (a municipal merger or a renaming), never two
      genuinely different places. Rule L treats it exactly like
      notation_variant_only (same AUTO-MERGE-gate-checked path). No
      pinned fixture uses this value, so this is a pure addition with no
      frozen exposure.
  Rule D CHANGED round 8 (2026-09-06): the founder ruled A in the question
      UI ("LINK AS RELATED wins; correct HI-01 as a recorded benchmark
      defect (Recommended)"), recorded at
      docs/decisions/decision-p0-3-rule-d-hi01-2026-09-06.json. Rule D now
      returns LINK AS RELATED; HI-01's frozen seed label is corrected in
      the same commit (net zero on the frozen count, one case's label
      only). Its id, "D", stays exposed via JBEQ_DECIDE_DISABLE_RULES so
      this rule can still be proven load-bearing with the same
      mutation-test seam this file already uses for everything else.

ROUND 8 REPAIR, THE REJECT-VERSUS-KEEP BOUNDARY (2026-09-06), per
~/.claude/evidence/review-u1-2026-09-06.md section A and FIX-DIRECTIVE-
2026-09-06.md section 18. Section A's finding: REJECT MATCH is for a
proposed match or write the input's own facts refute; KEEP SEPARATE is two
records that simply stay apart, nothing having been proposed, even when
the same facts are true. The frozen 70's REJECT MATCH cases and the
unseen 40's KEEP SEPARATE cases had labeled the same field values
oppositely because no field on the fact sheet recorded whether a proposal
was ever on the table. requested_action's enum widened (round 8's own
blind extraction, hub PR 408) from {none, assignment} to {none, assignment,
match_on_stated_basis, record_write} makes the boundary decidable at last.

  gated_reject / _has_proposal, one new id proposal_gate: every REJECT
      MATCH return site in rule 2 (different_legal_entity, no stated
      relation), rule L (different_administrative_area) and rule_r
      (authoritative_identifier conflicting, two identifiers sharing a
      value or legal_person_type across domains, object_type mismatch,
      a non-active lifecycle when rule_r_lifecycle_keep is itself
      disabled, and different_unit_in_building) now calls gated_reject()
      instead of finish() directly. gated_reject returns REJECT MATCH only
      when _has_proposal(fact_sheet) is true (requested_action in
      assignment, match_on_stated_basis or record_write, or
      requested_relation_type set); otherwise it returns KEEP SEPARATE
      with the same rule_fired id, so a decision row still names which
      rule decided. Disabling proposal_gate restores REJECT MATCH
      unconditionally, matching every rule's pre-round-8 behaviour.
  Rule C and link_vs_reject are DELIBERATELY NOT gated. link_vs_reject's
      two REJECT MATCH branches only run when requested_relation_type is
      already set, which is itself a proposal under _has_proposal's own
      definition, so gating it would be a no-op. Rule C's REJECT MATCH
      (tenant_boundary=stated, or stated_difference naming a tenant wall
      or a cross-domain identifier) fires on MM-08 with
      requested_action=none: MM-08 is a frozen critical case
      (CROSS-TENANT DATA LEAK, expected REJECT MATCH) whose input names a
      contractual tenant wall with no explicit proposal phrasing at all;
      gating rule C would flip it to KEEP SEPARATE, a false negative on a
      critical case review-u1's section A never examined. Left alone.
  Frozen exposure on fixture b (regression-sheets-2026-09-06b.json):
      checked case by case against the seven frozen REJECT MATCH ids
      (AD-03, AD-04, AD-10, EO-04, ID-05, MM-02, MM-03). AD-10, MM-02 and
      MM-03 carry requested_action=match_on_stated_basis and stay REJECT
      MATCH unchanged. AD-03, AD-04 and EO-04 carry requested_action=none
      on fixture b despite their prompts proposing a match on a stated
      basis (the same shape review-u1 documents for AD-10 and MM-03): this
      is an EXTRACTION GAP in round 8's blind reading, not a rule defect,
      and proposal_gate correctly flips them to KEEP SEPARATE given what
      the sheet actually states; a future re-extraction that records the
      proposal is the fix, never a rule change. ID-05 already answered
      KEEP SEPARATE before this fix (via rule_r's lifecycle clause,
      unaffected by proposal_gate either way) and stays a known,
      pre-existing miss.
"""
import argparse
import json
import os
import sys

# Reused for the round 7 allowed_answers_remap fix (finish() below): the one
# CAUTION_RANK ordering already lives in jbeq_mdm.py, and both scripts sit
# in this same directory, so this import needs no path hack whether
# jbeq_decide.py is run directly or imported by a test. jbeq_mdm.py never
# imports jbeq_decide.py, so there is no cycle.
import jbeq_mdm

REQUIRED_FIELDS = [
    "track",
    "allowed_answers",
    "identifiers",
    "stated_relation",
    "stated_difference",
    "tenant_boundary",
    "evidence_strength",
    "evidence_reasons",
    "corroborating_facts",
    "blank_fields",
    "irreversible",
    "history_exists",
    "hierarchy_parents",
    "one_to_many_object",
    "object_type_a",
    "object_type_b",
    "lifecycle",
    "requested_action",
    "requested_relation_type",
    "authoritative_identifier",
    "contradicted_attributes",
    "distinct_operational_attributes",
    "location_comparison",
    "effective_dates",
]

# Round 6 repair (2026-09-06, per the round 6 review section B group 1 and
# section A's AD-10 finding): stated_relation's enum was being read as one
# undifferentiated set in two places, when it actually splits into values
# that themselves confirm IDENTITY (a shared corporate number is itself
# strong evidence a merge should stand on) and values that state only a
# RELATIONSHIP worth recording alongside two records that stay separate.
# Treating an identity-bearing value as if it were merely "a relation" cost
# EO-03, MM-04 and MM-07 their AUTO-MERGE (gate-stated-relation fired on
# corporate_number_shared, which should never have blocked it); treating
# same_site_only as if it confirmed anything cost AD-10 its REJECT MATCH
# (rule 5's own reasoning is that a shared address alone is never the
# stated relation, so it cannot confirm one at link_vs_reject either).
IDENTITY_BEARING_RELATIONS = {"corporate_number_shared"}
RELATIONSHIP_ONLY_RELATIONS = {
    "role_pair", "same_site_only", "trade_flow", "parent_child",
    "group_company_code_shared_entity",
}

# The two tracks whose answers come from a vocabulary this module was never
# handed. Named honestly rather than guessed at. See the module docstring.
# "temporal" left this set round 5: it is now decided by the ordinary rules,
# using the effective_dates field.
UNSUPPORTED_TRACKS = {"survivorship", "requirements"}

EXIT_OK = 0
EXIT_NOT_DECIDED = 1
EXIT_NODATA = 3


def _missing_fields(sheet):
    return [f for f in REQUIRED_FIELDS if f not in sheet]


# Scalar fields checked against a schema enum, mapped to the "_enums" key
# that carries their allowed values (object_type_a and object_type_b share
# one list). Fields not in this map (tenant_boundary, identifier domain,
# identifier status) are deliberately left unenforced; see the "_enums"
# block's own _doc in fact-sheet-schema.json for why.
_ENUM_FIELDS = {
    "stated_relation": "stated_relation",
    "stated_difference": "stated_difference",
    "evidence_strength": "evidence_strength",
    "object_type_a": "object_type",
    "object_type_b": "object_type",
    "lifecycle": "lifecycle",
    "requested_action": "requested_action",
    "requested_relation_type": "requested_relation_type",
    "authoritative_identifier": "authoritative_identifier",
    "location_comparison": "location_comparison",
}
# These may legitimately be null; every other field above is never null in
# a well-formed sheet, so a None there is a real violation, not a value the
# field is allowed to skip.
_NULLABLE_ENUM_FIELDS = {"evidence_strength", "requested_relation_type", "location_comparison"}

_ENUMS_CACHE = None


def _schema_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmarks", "jbeq", "mdm", "fact-sheet-schema.json",
    )


def _load_enums():
    """Read the field-value enums from fact-sheet-schema.json's "_enums"
    key, the one source of truth (see the module docstring). Cached per
    process. Fails open (returns {}, so no enum is enforced) if the schema
    file is missing or malformed, rather than raise out of decide(): a
    broken schema file is a tooling problem this module ships fixed
    alongside, never a reason for a malformed FACT SHEET to crash the
    caller.
    """
    global _ENUMS_CACHE
    if _ENUMS_CACHE is not None:
        return _ENUMS_CACHE
    try:
        with open(_schema_path(), encoding="utf-8") as fh:
            schema = json.load(fh)
        enums = schema.get("_enums")
        if not isinstance(enums, dict):
            enums = {}
    except (OSError, ValueError):
        enums = {}
    _ENUMS_CACHE = enums
    return enums


def _enum_violations(sheet, enums):
    """Return a list of (field, value) pairs for every field present in
    `sheet` whose value is outside its documented enum. A MISSING field is
    a different failure (_missing_fields), reported separately so a typo
    is distinguishable from an omission (L12).
    """
    violations = []
    for field, enum_key in _ENUM_FIELDS.items():
        if field not in sheet:
            continue
        value = sheet[field]
        if value is None and field in _NULLABLE_ENUM_FIELDS:
            continue
        allowed = enums.get(enum_key)
        if allowed and value not in allowed:
            violations.append((field, value))

    allowed_reasons = enums.get("evidence_reasons")
    if allowed_reasons and "evidence_reasons" in sheet:
        for reason in sheet["evidence_reasons"] or []:
            if reason not in allowed_reasons:
                violations.append(("evidence_reasons", reason))

    allowed_person_type = enums.get("legal_person_type")
    if allowed_person_type and "identifiers" in sheet:
        for ident in sheet["identifiers"] or []:
            if not isinstance(ident, dict):
                continue
            person_type = ident.get("legal_person_type")
            if person_type is not None and person_type not in allowed_person_type:
                violations.append(("identifiers.legal_person_type", person_type))

    return violations


# Every id JBEQ_DECIDE_DISABLE_RULES is ever compared against, built by
# hand from `grep -noE '"[^"]*" (not )?in disabled' scripts/jbeq_decide.py`
# (2026-09-06, qa-review-jbeq-2026-09-06.md C2): before this set existed, a
# typo in the env var silently disabled nothing, so a mutation test with a
# mistyped id could never fail for the right reason. scripts/test_jbeq_
# decide.py greps this same file and asserts every literal it finds is a
# member here, so this set cannot drift from the code without a failing
# test naming the gap. "*" is the internal wildcard (see _disabled_rules
# below); every other member is a rule id named in the module docstring
# above.
KNOWN_RULE_IDS = frozenset({
    "*", "1", "2", "5", "7", "A", "B", "C", "D", "L",
    "allowed_answers_remap",
    "authoritative_identifier",
    "lifecycle",
    "link_vs_reject",
    "link_vs_reject_write_refutes",
    "medium_needs_confirmation",
    "object_type",
    "proposal_gate",
    "relation_partition",
    "relocated_address_not_a_refutation",
    "relocated_not_a_bar",
    "renamed_area_needs_a_person",
    "rule5_hoist",
    "rule_c_hierarchy_dimension",
    "rule_r",
    "rule_r_blank_fields_guard",
    "rule_r_lifecycle_keep",
    "same_site_only_never_a_relation",
    "site_store",
    "site_store_weak_guard",
    "site_store_write_refutes",
    "stated_relation_gate",
    "temporal",
    "temporal_at_or_after_successor_is_r2",
    "temporal_before_closure_is_r1",
    "temporal_before_successor_is_r1",
    "temporal_gap_is_nodata",
    "same_area_different_lot_keeps_separate",
})


def _disabled_rules():
    """Parse JBEQ_DECIDE_DISABLE_RULES into a set of rule ids to skip. See
    the module docstring for the full contract: bare "1" means "disable
    everything" (returned as the sentinel "*"), anything else is read as a
    comma list of specific rule ids.

    Raises ValueError naming the token(s) if the env var names anything
    outside KNOWN_RULE_IDS (2026-09-06, qa-review-jbeq-2026-09-06.md C2):
    an unrecognized token used to be accepted and silently disabled
    nothing, so a mistyped id read as a passing mutation test.
    """
    raw = os.environ.get("JBEQ_DECIDE_DISABLE_RULES", "")
    if not raw:
        return set()
    if raw == "1":
        return {"*"}
    tokens = {tok.strip() for tok in raw.split(",") if tok.strip()}
    unknown = tokens - KNOWN_RULE_IDS
    if unknown:
        raise ValueError(
            "JBEQ_DECIDE_DISABLE_RULES named unknown rule id(s): %s "
            "(known ids: %s)"
            % (", ".join(sorted(unknown)), ", ".join(sorted(KNOWN_RULE_IDS)))
        )
    return tokens


def _auto_merge_blocked(sheet, disabled):
    """Called only when the merge ladder (rule B) is about to return
    AUTO-MERGE. Legal-identity evidence this strong still must not silently
    collapse two different kinds of object, a closed or relocated record,
    an unaligned authoritative identifier, a stated relation, or a temporal
    conflict into one (design-p0-3-mdm-merge-safety section C; the site or
    store distinction moved out of this gate round 5 repair 2026-09-06,
    promoted to the top-level "site_store" rule in decide() so it also
    protects a case that never reaches this ladder at all). Returns
    (answer, rule_fired, why) for the first gate item that blocks the
    merge, or None if nothing blocks it. Each item has its own
    JBEQ_DECIDE_DISABLE_RULES id so a mutation test can disable exactly one
    and show the case it protects flips.
    """
    if "object_type" not in disabled:
        object_type_a, object_type_b = sheet.get("object_type_a"), sheet.get("object_type_b")
        if object_type_a and object_type_b and object_type_a != object_type_b:
            return (
                "LINK AS RELATED", "gate-object-type",
                "object_type_a=%r != object_type_b=%r: strong legal-identity "
                "evidence never collapses two different kinds of object "
                "into one; the relation is recorded instead"
                % (object_type_a, object_type_b),
            )

    if "lifecycle" not in disabled:
        lifecycle = sheet.get("lifecycle")
        # Round 9 repair (2026-09-06, review-u2-2026-09-06.md section B):
        # a relocated record is a compatible lifecycle of the SAME object
        # (one attribute of a continuing object changed), never a
        # different object the way closed and identifier_reused are, so it
        # bars AUTO-MERGE only, not every merge answer. id
        # relocated_not_a_bar isolates just this split: disabling it
        # reverts relocated to the old bar-everything behaviour so a
        # mutation test can prove the split is load-bearing.
        relocated_bars_both = (
            lifecycle == "relocated" and "relocated_not_a_bar" in disabled
        )
        if lifecycle in ("closed", "identifier_reused") or relocated_bars_both:
            return (
                "KEEP SEPARATE", "gate-lifecycle",
                "lifecycle=%r: a closed, relocated or reused-identifier "
                "record is never silently folded into a live one" % lifecycle,
            )
        if lifecycle == "relocated":
            return (
                "SUGGEST MERGE", "gate-lifecycle",
                "lifecycle=relocated: a compatible lifecycle of the same "
                "object bars AUTO-MERGE only, so a person confirms rather "
                "than an automatic merge",
            )

    if "authoritative_identifier" not in disabled:
        authoritative_identifier = sheet.get("authoritative_identifier")
        if authoritative_identifier and authoritative_identifier != "aligned":
            return (
                "SUGGEST MERGE", "gate-authoritative-identifier",
                "authoritative_identifier=%r: the identifiers that should "
                "govern this decision are not aligned, so a person confirms "
                "rather than an automatic merge" % authoritative_identifier,
            )

    if "stated_relation_gate" not in disabled:
        stated_relation = sheet.get("stated_relation")
        # Round 6 repair: this gate protects a case where the input states
        # only a RELATIONSHIP (never identity) alongside otherwise-strong
        # evidence. An identity-bearing value (corporate_number_shared) is
        # itself part of the identity evidence, not a relation to record
        # instead of it, so it must not trip this gate (EO-03, MM-04,
        # MM-07); see IDENTITY_BEARING_RELATIONS /
        # RELATIONSHIP_ONLY_RELATIONS above. Disabling "relation_partition"
        # reverts to the pre-round-6 behaviour of firing on ANY non-none
        # value, which is exactly the defect this repair closes.
        fires = stated_relation and stated_relation != "none" and (
            "relation_partition" in disabled
            or stated_relation in RELATIONSHIP_ONLY_RELATIONS
        )
        if fires:
            return (
                "LINK AS RELATED", "gate-stated-relation",
                "stated_relation=%r: the input itself states a relation, "
                "so strong legal-identity evidence records the relation "
                "instead of silently merging past it" % stated_relation,
            )

    if "temporal" not in disabled:
        effective_dates = sheet.get("effective_dates") or {}
        if effective_dates.get("conflict"):
            return (
                "KEEP SEPARATE", "gate-temporal",
                "effective_dates conflict=true: a temporal disagreement "
                "blocks an automatic merge until a person resolves it",
            )

    return None


def _remap_to_allowed(answer, allowed):
    """Round 7 repair (2026-09-06, review section C, id
    allowed_answers_remap). Called only by finish() above, only when
    `answer` is not a member of the case's own `allowed` list.

    Walks jbeq_mdm.CAUTION_RANK's own order (AUTO-MERGE ... NO-DATA, the
    aggressive end first) starting at `answer`'s own position, forward
    toward the conservative end (inclusive of ties at the same rank, e.g.
    ESCALATE next to NO-DATA), and returns the first member it finds in
    `allowed`. If nothing forward matches, walks back from just before
    `answer`'s position toward the aggressive end instead. Returns None
    only if `allowed` carries nothing this ordering recognizes at all, in
    which case the caller falls back to an honest NO-DATA.
    """
    order = list(jbeq_mdm.CAUTION_RANK)
    if answer not in order:
        order = order + [answer]
    start = order.index(answer)
    for i in range(start, len(order)):
        if order[i] in allowed:
            return order[i]
    for i in range(start - 1, -1, -1):
        if order[i] in allowed:
            return order[i]
    return None


def _has_proposal(sheet):
    """True when the input proposes a match or a write for this case:
    requested_action is one of assignment, match_on_stated_basis or
    record_write (never "none"), or requested_relation_type names a
    hierarchy type to write. Used by gated_reject() inside
    _decide_unwrapped: REJECT MATCH is for a proposal the facts refute,
    never for a case where nothing was proposed in the first place
    (review-u1-2026-09-06.md section A)."""
    if sheet.get("requested_action") in (
        "assignment", "match_on_stated_basis", "record_write",
    ):
        return True
    return sheet.get("requested_relation_type") is not None


def _relation_is_none(stated_relation, disabled):
    """F2 (2026-09-06, review-u4-2026-09-06.md W4-23, mutation id
    same_site_only_never_a_relation): same_site_only is never the stated
    relation boundary rule 1 asks for, exactly as rule 5 and link_vs_reject
    already read it: a shared address alone is never a relation. Treated
    like stated_relation=="none" wherever a rule keys directly on that
    equality, so rule_r's guard and boundary rule 1 (the two places that
    still read stated_relation=="none" as a bare string match) agree with
    rule 5 and link_vs_reject rather than falling through to LINK AS
    RELATED on a shared address alone. Disabling
    same_site_only_never_a_relation reverts both call sites to the literal
    string comparison."""
    if stated_relation == "none":
        return True
    return (
        stated_relation == "same_site_only"
        and "same_site_only_never_a_relation" not in disabled
    )


def _date_parts(value):
    """Parse an ISO-ish "YYYY", "YYYY-MM" or "YYYY-MM-DD" string into
    (year, month-or-None, day-or-None). None on anything unparseable."""
    if not isinstance(value, str):
        return None
    bits = value.strip().split("-")
    try:
        year = int(bits[0])
        month = int(bits[1]) if len(bits) > 1 else None
        day = int(bits[2]) if len(bits) > 2 else None
    except (ValueError, IndexError):
        return None
    return (year, month, day)


def _date_cmp(a, b):
    """-1/0/1 for a versus b, or None when the order is unknowable: either
    side lacks month precision, or both fall in the same year and month but
    at least one side lacks a day (a month-precision collision the temporal
    rules below must not fire on)."""
    pa, pb = _date_parts(a), _date_parts(b)
    if not pa or not pb or pa[1] is None or pb[1] is None:
        return None
    if (pa[0], pa[1]) != (pb[0], pb[1]):
        return -1 if (pa[0], pa[1]) < (pb[0], pb[1]) else 1
    if pa[2] is None or pb[2] is None:
        return None
    return (pa[2] > pb[2]) - (pa[2] < pb[2])


def _temporal_rule(fact_sheet, disabled):
    """Three date-ordering rules for the temporal track's own R1/R2/NO-DATA
    vocabulary (review-temporal-track-2026-09-06.md), gated on "R1" being a
    member of the case's own allowed_answers, never on track: U1 to U4's
    temporal cases answer from the decision vocabulary instead (KEEP
    SEPARATE and the like) and must reach the ladder below untouched.
    Returns (answer, rule_fired, why) for the first rule that fires, or
    None to fall through to the ladder unchanged. Each rule has its own
    JBEQ_DECIDE_DISABLE_RULES id so a mutation test can disable exactly
    one.

    temporal_at_or_after_successor_is_r2: as_of is at or after
    candidate_effective_date (the date the later, successor record takes
    effect), so the successor record is the one in force: R2.
    temporal_before_successor_is_r1: as_of is before candidate_effective_date
    and lifecycle is not stated closed, so the earlier record is still the
    one in force: R1.
    temporal_gap_is_nodata: as_of is before candidate_effective_date, the
    earlier record is stated closed, and the sheet carries no
    prior_valid_to (the earlier record's own valid_to): a dormancy gap
    between the closure and the successor's start cannot be ruled out, so
    the honest answer is NO-DATA naming that gap rather than guessing which
    record, if any, was in force.
    temporal_before_closure_is_r1 (round 12 repair, TM-01): as_of is before
    candidate_effective_date, the earlier record is stated closed, and the
    sheet carries a prior_valid_to (the closure date) that is itself after
    as_of. lifecycle="closed" describes the record's state today, not its
    state at as_of: the record had not yet closed as of as_of, so it was
    still the one in force: R1. When prior_valid_to is at or before as_of
    the record really was already closed by as_of, and this rule does not
    fire, leaving the case to rule_r below (TM-09 shape).

    A pair of dates that collide at month precision (either side missing a
    day, both in the same year and month) has an unknowable order, so no
    rule fires and the case falls through to the ladder below.
    """
    allowed = fact_sheet.get("allowed_answers") or []
    if "R1" not in allowed:
        return None
    effective_dates = fact_sheet.get("effective_dates") or {}
    as_of = effective_dates.get("as_of")
    candidate = effective_dates.get("candidate_effective_date")
    if not as_of or not candidate:
        return None
    order = _date_cmp(as_of, candidate)
    if order is None:
        return None
    if order >= 0:
        if "temporal_at_or_after_successor_is_r2" in disabled:
            return None
        return (
            "R2", "temporal_at_or_after_successor_is_r2",
            "as_of %r is at or after candidate_effective_date %r: the "
            "later (successor) record is the one in force"
            % (as_of, candidate),
        )
    lifecycle = fact_sheet.get("lifecycle")
    if lifecycle != "closed":
        if "temporal_before_successor_is_r1" in disabled:
            return None
        return (
            "R1", "temporal_before_successor_is_r1",
            "as_of %r is before candidate_effective_date %r and "
            "lifecycle=%r is not closed: the earlier record is still the "
            "one in force" % (as_of, candidate, lifecycle),
        )
    prior_valid_to = effective_dates.get("prior_valid_to")
    if prior_valid_to is None:
        if "temporal_gap_is_nodata" in disabled:
            return None
        return (
            "NO-DATA", "temporal_gap_is_nodata",
            "as_of %r is before candidate_effective_date %r and "
            "lifecycle=closed, but the sheet carries no prior_valid_to: a "
            "dormancy gap between the closure and the successor's start "
            "cannot be ruled out" % (as_of, candidate),
        )
    closure_order = _date_cmp(as_of, prior_valid_to)
    if closure_order is not None and closure_order < 0:
        if "temporal_before_closure_is_r1" in disabled:
            return None
        return (
            "R1", "temporal_before_closure_is_r1",
            "as_of %r is before candidate_effective_date %r and also "
            "before the record's own prior_valid_to %r (its closure "
            "date): lifecycle=closed states today's status, not the "
            "status at as_of, and the record had not yet closed as of "
            "as_of, so the earlier record was still the one in force"
            % (as_of, candidate, prior_valid_to),
        )
    return None


def _decide_unwrapped(fact_sheet, allow_unrecognized, disabled):
    """The rule table itself, run with `disabled` already resolved. See
    decide() below for the public entry point, which wraps every result
    this function returns with the mutation-seam marker when `disabled` is
    non-empty (hub PR 386 security finding, 2026-09-06).
    """
    if "*" in disabled:
        return {
            "answer": "NO-DATA",
            "rule_fired": "rules-disabled-for-test",
            "why": "JBEQ_DECIDE_DISABLE_RULES matched every rule: the rule "
                   "table was skipped",
        }

    missing = _missing_fields(fact_sheet)
    if missing:
        return {
            "answer": "NO-DATA",
            "rule_fired": "validation",
            "why": "missing required field(s): %s" % ", ".join(missing),
        }

    violations = _enum_violations(fact_sheet, _load_enums())
    if violations and not allow_unrecognized:
        field, value = violations[0]
        return {
            "answer": "NO-DATA",
            "rule_fired": "unrecognized-value",
            "why": ("field %r has value %r, which is outside its "
                     "documented enum: a typo is distinguishable from an "
                     "omission" % (field, value)),
        }

    track = fact_sheet["track"]
    allowed = fact_sheet.get("allowed_answers") or []

    def finish(answer, rule_fired, why):
        if allowed and answer not in allowed:
            # Round 7 repair (2026-09-06, review section C): the old safety
            # net converted straight to NO-DATA, which is itself sometimes
            # not in allowed_answers (U-23), and was wrong on all three
            # cases it fired on in U1. id allowed_answers_remap: walk
            # jbeq_mdm.CAUTION_RANK from the chosen answer toward the
            # conservative end and take the first member of `allowed`
            # reached; if none exists there, walk back toward the
            # aggressive end. Disabling this id reverts to the old
            # immediate-NO-DATA behaviour.
            remapped = None
            if "allowed_answers_remap" not in disabled:
                remapped = _remap_to_allowed(answer, allowed)
            if remapped is not None:
                result = {
                    "answer": remapped,
                    "rule_fired": rule_fired,
                    "why": ("rule %s chose %r, which is outside this case's "
                            "own allowed_answers %s; remapped along "
                            "CAUTION_RANK to the nearest allowed answer "
                            "instead of NO-DATA: %s"
                            % (rule_fired, answer, allowed, why)),
                }
            else:
                result = {
                    "answer": "NO-DATA",
                    "rule_fired": "safety-net",
                    "why": ("rule %s chose %r but the case's own allowed_answers "
                            "does not carry it: %s" % (rule_fired, answer, allowed)),
                }
        else:
            result = {"answer": answer, "rule_fired": rule_fired, "why": why}
        if violations:
            result = dict(result)
            result["unrecognized_values"] = [
                "%s=%r" % (f, v) for f, v in violations
            ]
        return result

    def gated_reject(rule_fired, why):
        """Rules 2, L and rule_r all reach a point where the input's own
        facts refute something; whether that "something" is a REJECT MATCH
        or a KEEP SEPARATE turns on whether a proposal was ever on the
        table (id proposal_gate; review-u1-2026-09-06.md section A). A
        proposed match or write (requested_action in assignment,
        match_on_stated_basis, record_write, or requested_relation_type
        set) that the facts refute is REJECT MATCH; with no proposal at
        all, the same refutation is just two records that stay apart:
        KEEP SEPARATE. Disabling proposal_gate restores REJECT MATCH
        unconditionally, exactly as before this fix.
        """
        if "proposal_gate" in disabled or _has_proposal(fact_sheet):
            return finish("REJECT MATCH", rule_fired, why)
        return finish(
            "KEEP SEPARATE", rule_fired,
            "%s; but requested_action=%r and requested_relation_type=%r "
            "record no proposal at all, so there is nothing to reject: "
            "the records simply stay apart"
            % (why, fact_sheet.get("requested_action"),
               fact_sheet.get("requested_relation_type")),
        )

    if track in UNSUPPORTED_TRACKS:
        return finish(
            "NO-DATA", "track-unsupported",
            "track %r answers from its own vocabulary; no rule handed to "
            "this module defines it" % track,
        )

    temporal_hit = _temporal_rule(fact_sheet, disabled)
    if temporal_hit is not None:
        return finish(*temporal_hit)

    hierarchy_parents = fact_sheet["hierarchy_parents"] or []
    distinct_types = {p["type"] for p in hierarchy_parents if p.get("parent")}
    if len(distinct_types) >= 2 and "D" not in disabled:
        return finish(
            "LINK AS RELATED", "D",
            "hierarchy_parents states %d distinct types (%s), each valid; "
            "hierarchy types are independent, not one conflict "
            "(founder ruling 2026-09-06: a relationship is recorded as a "
            "link)"
            % (len(distinct_types), ", ".join(sorted(distinct_types))),
        )

    stated_difference = fact_sheet["stated_difference"]
    tenant_boundary = fact_sheet["tenant_boundary"]
    # rule_c_hierarchy_dimension (round 10, 2026-09-06, HI-09): a shared
    # name between two hierarchy nodes is never a refuting fact on its own,
    # but the input can state the two nodes live in different hierarchy
    # dimensions (a location hierarchy versus an organisation hierarchy),
    # which no other stated_difference value carries. Read exactly like
    # different_identifier_domain, gated by its own id so a mutation test
    # can disable just this value while different_identifier_domain,
    # different_tenant and contractual_wall keep firing rule C.
    hierarchy_dimension_difference = (
        stated_difference == "different_hierarchy_dimension"
        and "rule_c_hierarchy_dimension" not in disabled
    )
    if (tenant_boundary == "stated" or stated_difference in (
        "different_tenant", "contractual_wall", "different_identifier_domain",
    ) or hierarchy_dimension_difference) and "C" not in disabled:
        return finish(
            "REJECT MATCH", "C",
            "tenant_boundary=%r, stated_difference=%r states the two are "
            "not the same or must never link" % (tenant_boundary, stated_difference),
        )

    stated_relation = fact_sheet["stated_relation"]

    # Rule link_vs_reject, ahead of boundary rule 2 (2026-09-06). See the
    # module docstring, precedence step 4. requested_relation_type is
    # OPTIONAL and absent from every fact sheet today, so this half is inert
    # until an extractor starts filling it in.
    requested_relation_type = fact_sheet.get("requested_relation_type")
    if requested_relation_type is not None and "link_vs_reject" not in disabled:
        stated_hierarchy_types = {
            p["type"] for p in hierarchy_parents if p.get("parent")
        }
        if requested_relation_type not in stated_hierarchy_types:
            # HI-05 fix (2026-09-06): the guard used to require
            # stated_hierarchy_types be non-empty before checking
            # membership, so a requested_relation_type asked against an
            # EMPTY hierarchy_parents (nothing stated at all) short-
            # circuited past this rule instead of being seen as a refuted
            # proposal. Asking to write a parent that nothing states IS a
            # refuted proposal, empty set included.
            return finish(
                "REJECT MATCH", "link_vs_reject",
                "requested_relation_type=%r conflicts with the stated "
                "hierarchy type(s) %s: the proposal itself is refuted"
                % (requested_relation_type, sorted(stated_hierarchy_types)),
            )
        else:
            # requested_parent (round 6 repair, 2026-09-06, review section C
            # HI-02): an OPTIONAL key, string or null, the entity the
            # request asks to become the parent. requested_relation_type
            # matching a stated hierarchy TYPE is not the same as matching
            # its DIRECTION: HI-02 asks to REVERSE parent and child inside
            # the same capital hierarchy, which the type check above cannot
            # see at all. When the input states requested_parent and it
            # names something other than the stated parent for this type,
            # the proposal is refuted the same way a type mismatch is.
            requested_parent = fact_sheet.get("requested_parent")
            if requested_parent is not None:
                stated_parents_for_type = {
                    p["parent"] for p in hierarchy_parents
                    if p.get("type") == requested_relation_type and p.get("parent")
                }
                if (stated_parents_for_type
                        and requested_parent not in stated_parents_for_type):
                    return finish(
                        "REJECT MATCH", "link_vs_reject",
                        "requested_relation_type=%r matches a stated "
                        "hierarchy type, but requested_parent=%r is not the "
                        "stated parent for that type (%s): the proposal "
                        "reverses parent and child"
                        % (requested_relation_type, requested_parent,
                           sorted(stated_parents_for_type)),
                    )

    if stated_difference == "different_legal_entity":
        # Round 6 repair: same_site_only never confirms a relation here
        # (AD-10), exactly as rule 5 already treats it as never confirming
        # anything on its own ("a shared address alone is never the stated
        # relation"). Every OTHER relationship-only or identity-bearing
        # value still confirms, unchanged (EO-02, EO-09, HI-06). Disabling
        # "relation_partition" reverts to the pre-round-6 behaviour of
        # confirming on ANY non-none value, including same_site_only.
        #
        # F1a (2026-09-06, review-u4-2026-09-06.md W4-04, mutation id
        # link_vs_reject_write_refutes): a stated relation answers "what is
        # the relation", it does not license WRITING one code over the
        # other. When requested_action=record_write, the input is not
        # merely stating a relation alongside the refutation, it is asking
        # to record a write the refutation itself refutes, so
        # confirms_relation never fires and the refuting fact
        # (stated_difference=different_legal_entity) wins, same as if no
        # relation had been stated at all. Disabling
        # link_vs_reject_write_refutes reverts to letting a stated relation
        # confirm even under a record_write proposal.
        confirms_relation = stated_relation != "none" and (
            "relation_partition" in disabled
            or stated_relation != "same_site_only"
        ) and (
            fact_sheet["requested_action"] != "record_write"
            or "link_vs_reject_write_refutes" in disabled
        )
        if confirms_relation and "link_vs_reject" not in disabled:
            return finish(
                "LINK AS RELATED", "link_vs_reject",
                "stated_difference=different_legal_entity refutes identity, "
                "but stated_relation=%r states a relation the input itself "
                "confirms: REJECT MATCH is for a refuted proposal, not for "
                "a stated relation" % stated_relation,
            )
        if "2" not in disabled:
            return gated_reject(
                "2",
                "stated_difference=different_legal_entity refutes the "
                "proposed identity outright",
            )

    # Round 10 repair (2026-09-06, review-u3-2026-09-06.md W-04): rule 5
    # (a shared address alone never confirms anything when one side is a
    # one-to-many object) sat below the location ladder, so a
    # notation_variant_only address on a one-to-many, shared-address case
    # reached the AUTO-MERGE safety gates first and answered LINK AS RELATED
    # off gate-object-type before rule 5 was ever read. Hoisted above the
    # ladder so it answers first, exactly as it already does for every
    # other location_comparison value.
    #
    # M1 (2026-09-06, qa-review-jbeq-2026-09-06.md): id "5" guards the rule
    # itself (disabling it removes rule 5 everywhere), and id rule5_hoist
    # guards only THIS hoisted position, separately: disabling rule5_hoist
    # alone re-lowers rule 5 below the location ladder without deleting the
    # rule, which is a different mutation from disabling "5" and needs its
    # own id to be provable in isolation.
    if (stated_relation == "same_site_only" and fact_sheet["one_to_many_object"]
            and "5" not in disabled and "rule5_hoist" not in disabled):
        return finish(
            "KEEP SEPARATE", "5",
            "only a shared address is stated and one side is a one-to-many "
            "object; a shared address alone is never the stated relation",
        )

    location_comparison = fact_sheet["location_comparison"]
    # Round 10 repair (2026-09-06, review-u3-2026-09-06.md W-01): a
    # relocation's address difference is the relocation itself, never a
    # refutation of identity, so lifecycle=relocated exempts this case from
    # rule L's different_administrative_area gate. Backstop for an
    # extraction gap; the primary fix is EXTRACTOR-PROMPT.md giving
    # location_comparison a value for two real addresses inside one
    # administrative area. id relocated_address_not_a_refutation isolates
    # just this exemption: disabling it reverts to the old
    # refuses-everything behaviour so a mutation test can prove the
    # exemption is load-bearing.
    if (location_comparison == "different_administrative_area"
            and not (fact_sheet["lifecycle"] == "relocated"
                     and "relocated_address_not_a_refutation" not in disabled)
            and "L" not in disabled):
        return gated_reject(
            "L",
            "location_comparison=different_administrative_area: the two "
            "addresses disagree at the administrative-area level despite "
            "any town or lot number match",
        )
    if location_comparison == "internally_inconsistent" and "L" not in disabled:
        return finish(
            "ESCALATE", "L",
            "location_comparison=internally_inconsistent: the input's own "
            "address fields disagree with each other",
        )
    # Round 6 repair (2026-09-06, review section B group 3, flagged the
    # weakest of the round's changes): notation_variant_only and
    # same_chiban_different_notation are two of the five documented
    # location_comparison values (fact-sheet-schema.json's "_enums") that no
    # rule read at all, so every AD-01/AD-02/AD-05/AD-07-shaped case fell
    # through to the boundary-rule-1 default (KEEP SEPARATE). Landed last
    # and in its own commit so it can be reverted alone if the mapping
    # below turns out to be the wrong shape.
    if location_comparison == "notation_variant_only" and "L" not in disabled:
        # A notation-only address difference is close enough to an
        # AUTO-MERGE candidate that it must still clear every AUTO-MERGE
        # safety gate (object type, lifecycle, authoritative identifier,
        # stated relation, temporal), exactly like the merge ladder's own
        # strong-evidence branch.
        blocked = _auto_merge_blocked(fact_sheet, disabled)
        if blocked:
            answer, rule_fired, why = blocked
            return finish(answer, rule_fired, why)
        return finish(
            "AUTO-MERGE", "L",
            "location_comparison=notation_variant_only: the addresses name "
            "the same place under two notations, and no merge-safety gate "
            "blocked it",
        )
    # Round 7 repair (2026-09-06, review section F, closing the extraction
    # half of U-03) had treated same_area_renamed exactly like
    # notation_variant_only above, sharing the AUTO-MERGE-gate-checked path.
    # Founder ruling 2026-09-06 (decision-p0-3-same-area-renamed-2026-09-06):
    # that was wrong on all three corpus cases carrying the value (AD-05,
    # W4-21, W4-24): a renamed area (a municipal merger or a street/town
    # renaming) is a fact about the map, never confirmation the two
    # registry rows are one, so it earns a person's confirmation, not an
    # outright merge. id renamed_area_needs_a_person isolates this branch:
    # disabling it reverts to the pre-ruling AUTO-MERGE-gate-checked path
    # (below), so a mutation test can prove the branch is load-bearing.
    if location_comparison == "same_area_renamed" and "L" not in disabled:
        if "renamed_area_needs_a_person" not in disabled:
            return finish(
                "SUGGEST MERGE", "L",
                "location_comparison=same_area_renamed: the addresses name "
                "the same place under an old and a new administrative "
                "name, but a rename is a fact about the map, not "
                "confirmation the two registry rows are one (founder "
                "ruling 2026-09-06, decision-p0-3-same-area-renamed-"
                "2026-09-06: a renamed area earns a person's confirmation, "
                "never an outright merge)",
            )
        blocked = _auto_merge_blocked(fact_sheet, disabled)
        if blocked:
            answer, rule_fired, why = blocked
            return finish(answer, rule_fired, why)
        return finish(
            "AUTO-MERGE", "L",
            "location_comparison=same_area_renamed: the addresses name "
            "the same place under an old and a new administrative name, "
            "and no merge-safety gate blocked it "
            "(renamed_area_needs_a_person disabled)",
        )
    if location_comparison == "same_chiban_different_notation" and "L" not in disabled:
        return finish(
            "SUGGEST MERGE", "L",
            "location_comparison=same_chiban_different_notation: the same "
            "lot number written two ways is close enough that a person "
            "should confirm before it merges",
        )
    # Round 12 repair (2026-09-07, review-rule6-2026-09-07.md ranking item
    # 2, closes U3 W-03): the corpus had no location_comparison value for
    # two addresses that state the same town and block and differ only in
    # lot number, so the extractor wrote different_administrative_area and
    # rule L's REJECT MATCH fired. Addendum rule 6 treats a differing lot
    # number alone, with nothing else stated, as an absence of support,
    # never a refutation: answer stays KEEP SEPARATE. Own id
    # same_area_different_lot_keeps_separate isolates this branch: disabled,
    # the case falls through to whichever rule would apply next, exactly as
    # if location_comparison carried no signal at all.
    if (location_comparison == "same_area_different_lot" and "L" not in disabled
            and "same_area_different_lot_keeps_separate" not in disabled):
        return finish(
            "KEEP SEPARATE", "L",
            "location_comparison=same_area_different_lot: the two addresses "
            "state the same town and block and differ only in lot number, "
            "which addendum rule 6 treats as an absence of support, never "
            "a refutation",
        )

    if stated_relation == "group_company_code_shared_entity" and "7" not in disabled:
        return finish(
            "LINK AS RELATED", "7",
            "records are kept apart by company code operationally, but both "
            "name the same external entity, which is itself the relation",
        )

    # site_store (round 5 repair, 2026-09-06): promoted from a gate reached
    # only inside a strong-evidence AUTO-MERGE candidate to a top-level
    # rule, because MM-10 and HI-08 both carry distinct_operational_
    # attributes=true with evidence_strength=null and so never reached the
    # old gate at all. This is a relationship signal regardless of
    # evidence_strength, EXCEPT weak evidence (round 6 repair, 2026-09-06,
    # review section B group 2 EO-07): weak evidence never asserts a
    # relation either, so a case whose only signal is a stated operational
    # difference under weak evidence must still reach rule B's weak branch
    # (ESCALATE), not this rule's LINK AS RELATED. Its own mutation id,
    # site_store_weak_guard, isolates that one exception from the
    # site_store rule as a whole.
    weak_blocks_site_store = (
        fact_sheet["evidence_strength"] == "weak"
        and "site_store_weak_guard" not in disabled
    )
    if (fact_sheet["distinct_operational_attributes"]
            and not weak_blocks_site_store
            and "site_store" not in disabled):
        # F1b (2026-09-06, review-u4-2026-09-06.md W4-03, mutation id
        # site_store_write_refutes): distinct_operational_attributes=true
        # is a relationship signal to RECORD, not a licence to WRITE one
        # code over the other. When requested_action=record_write, the
        # per-object separation this rule reads IS the fact that refutes
        # that specific write, so it takes gated_reject (REJECT MATCH,
        # since record_write is itself a proposal) instead of finish's
        # LINK AS RELATED. Every other requested_action value (none,
        # assignment, match_on_stated_basis) is unaffected: those never
        # ask to overwrite a stated per-object split. Disabling
        # site_store_write_refutes reverts to LINK AS RELATED even under a
        # record_write proposal.
        if (fact_sheet["requested_action"] == "record_write"
                and "site_store_write_refutes" not in disabled):
            return gated_reject(
                "site_store",
                "distinct_operational_attributes=true: each side carries "
                "its own delivery, booking or pricing target, which "
                "refutes the requested_action=record_write proposal to "
                "record one over the other",
            )
        return finish(
            "LINK AS RELATED", "site_store",
            "distinct_operational_attributes=true: each side carries its "
            "own delivery, booking or pricing target, so the records "
            "relate but never merge into one",
        )

    # rule_r (round 5 repair, 2026-09-06, design review section A): fires
    # only when stated_relation == "none" (load-bearing: EO-09 states
    # parent_child and must keep LINK, not flip to REJECT). Covers the
    # eight REJECT-versus-KEEP cases the round 5 regression lost: a
    # conflicting authoritative identifier, two identifiers that share a
    # value across domains or carry different legal_person_type, two
    # different object types, a lifecycle event that blocks a merge, or an
    # address that matches only down to the wrong unit in a building.
    if _relation_is_none(stated_relation, disabled) and "rule_r" not in disabled:
        authoritative_identifier = fact_sheet["authoritative_identifier"]
        object_type_a = fact_sheet["object_type_a"]
        object_type_b = fact_sheet["object_type_b"]
        identifiers = fact_sheet["identifiers"] or []

        def _identifiers_conflict():
            for i, id_a in enumerate(identifiers):
                for id_b in identifiers[i + 1:]:
                    if (id_a.get("value") is not None
                            and id_a.get("value") == id_b.get("value")
                            and id_a.get("domain") != id_b.get("domain")):
                        return True
                    person_a = id_a.get("legal_person_type")
                    person_b = id_b.get("legal_person_type")
                    if person_a and person_b and person_a != person_b:
                        return True
            return False

        if authoritative_identifier == "conflicting":
            return gated_reject(
                "rule_r",
                "stated_relation=none and authoritative_identifier="
                "conflicting: the identifiers that should govern this "
                "decision disagree outright",
            )
        if _identifiers_conflict():
            return gated_reject(
                "rule_r",
                "stated_relation=none and two identifiers share the same "
                "value in different domains, or carry different "
                "legal_person_type: identifiers=%s" % identifiers,
            )
        # Round 6 repair (2026-09-06, review section B group 2 AD-09): an
        # INFERRED difference (two distinct object_type values read off
        # what the extractor could tell) must never outrank a STATED fact.
        # Round 7 (2026-09-06, review section D, U-38) widened this from
        # blank_fields alone to blank_fields, evidence_reasons or
        # contradicted_attributes non-empty: AD-09's own object types come
        # from a field left almost entirely blank, and U-38's own lifecycle
        # event is outranked by a STATED contradicted_attributes/
        # evidence_reasons conflict the same way. Same mutation id,
        # rule_r_blank_fields_guard, isolates this exception; now guards
        # both the object_type clause and the lifecycle clause below, so
        # disabling it reverts both to firing unconditionally together.
        stated_fact_block = (
            bool(fact_sheet["blank_fields"])
            or bool(fact_sheet["evidence_reasons"])
            or bool(fact_sheet.get("contradicted_attributes"))
        ) and "rule_r_blank_fields_guard" not in disabled
        if object_type_a != object_type_b and not stated_fact_block:
            return gated_reject(
                "rule_r",
                "stated_relation=none and object_type_a=%r != "
                "object_type_b=%r: two different kinds of record are "
                "never the same operational object"
                % (object_type_a, object_type_b),
            )
        # Round 9 repair (2026-09-06, review-u2-2026-09-06.md section B):
        # relocated is a compatible lifecycle of the SAME object, not a
        # bar-everything case like closed and identifier_reused, so it no
        # longer stops here at all; it falls through to the merge ladder
        # below, where _auto_merge_blocked's own gate-lifecycle item bars
        # AUTO-MERGE only and SUGGEST MERGE stays reachable. id
        # relocated_not_a_bar isolates this split: disabling it restores
        # relocated to the pre-round-9 bar-both behaviour, matching
        # _auto_merge_blocked's own id of the same name.
        relocated_bars_here = (
            fact_sheet["lifecycle"] == "relocated"
            and "relocated_not_a_bar" in disabled
        )
        if (
            fact_sheet["lifecycle"] in ("closed", "identifier_reused")
            or relocated_bars_here
        ) and not stated_fact_block:
            # Round 7 repair (2026-09-06, review section D, U-36): this
            # clause used to answer REJECT MATCH here while
            # _auto_merge_blocked's own lifecycle gate (gate-lifecycle,
            # above) answers KEEP SEPARATE for the IDENTICAL fact: the
            # engine contradicted itself. id rule_r_lifecycle_keep,
            # separate from the guard above, isolates just the answer this
            # clause gives once it does fire.
            if "rule_r_lifecycle_keep" not in disabled:
                return finish(
                    "KEEP SEPARATE", "rule_r",
                    "stated_relation=none and lifecycle=%r: a closed, "
                    "relocated or reused-identifier record is never "
                    "folded into a live one (matches gate-lifecycle's "
                    "answer for the same fact, never REJECT MATCH)"
                    % fact_sheet["lifecycle"],
                )
            return gated_reject(
                "rule_r",
                "stated_relation=none and lifecycle=%r: a closed, "
                "relocated or reused-identifier record is never the same "
                "live object" % fact_sheet["lifecycle"],
            )
        if fact_sheet["location_comparison"] == "different_unit_in_building":
            return gated_reject(
                "rule_r",
                "stated_relation=none and location_comparison="
                "different_unit_in_building: the addresses match only "
                "down to the wrong unit in the same building",
            )

    evidence_strength = fact_sheet["evidence_strength"]
    reasons = fact_sheet["evidence_reasons"] or []
    irreversible = bool(fact_sheet["irreversible"])
    history_exists = bool(fact_sheet["history_exists"])

    if evidence_strength is not None and "B" not in disabled:
        contradicted = fact_sheet.get("contradicted_attributes") or []
        if evidence_strength == "weak":
            if contradicted or "unexplained_conflict" in reasons:
                return finish(
                    "KEEP SEPARATE", "B",
                    "evidence_strength=weak with a stated contradiction "
                    "(contradicted_attributes=%s, evidence_reasons=%s): the "
                    "conflict itself is the answer, not a reason to look "
                    "further" % (contradicted, reasons),
                )
            if "score_only" in reasons and not irreversible and not history_exists:
                return finish(
                    "NO-DATA", "B",
                    "evidence_strength=weak and evidence_reasons carries "
                    "only score_only, with no irreversibility or history to "
                    "force a human look: nothing observable to decide or "
                    "escalate",
                )
            return finish(
                "ESCALATE", "B",
                "evidence_strength=weak; irreversibility or history never "
                "upgrade weak evidence into a merge, and nothing here "
                "refutes or clears it either",
            )
        no_reason = (not reasons and not irreversible and not history_exists)
        # Round 10 repair (2026-09-06, review-u3-2026-09-06.md W-19,
        # decision-rules-addendum.md rule 9): medium evidence with no
        # confirmation reason stated apart from the evidence itself
        # (irreversible=False and history_exists=False) must ESCALATE, not
        # fall through to SUGGEST MERGE. An unvalidated migration crosswalk
        # (evidence_strength=medium by its own rule) must never be counted
        # twice, once as the medium evidence and again as its own
        # confirmation reason. id medium_needs_confirmation isolates this
        # branch: disabling it reverts to the old fall-through-to-merge
        # behaviour so a mutation test can prove the branch is load-bearing.
        if (evidence_strength == "medium" and not irreversible
                and not history_exists
                and "medium_needs_confirmation" not in disabled):
            return finish(
                "ESCALATE", "B",
                "evidence_strength=medium, reasons=%s, but no confirmation "
                "reason stated separately from that evidence itself "
                "(irreversible=False, history_exists=False): boundary rule 9 "
                "forbids counting the evidence twice as its own confirmation"
                % (reasons,),
            )
        if evidence_strength == "strong" and no_reason:
            blocked = _auto_merge_blocked(fact_sheet, disabled)
            if blocked:
                answer, rule_fired, why = blocked
                return finish(answer, rule_fired, why)
            return finish(
                "AUTO-MERGE", "B",
                "evidence_strength=strong with no conflicting or "
                "confirmation reason, and no merge-safety gate blocked it",
            )
        return finish(
            "SUGGEST MERGE", "B",
            "evidence_strength=%s, reasons=%s, irreversible=%s, "
            "history_exists=%s: enough to merge but a person should confirm"
            % (evidence_strength, reasons, irreversible, history_exists),
        )
    elif evidence_strength is None and reasons and "B" not in disabled:
        # Round 6 repair (2026-09-06, review section A ID-02): this branch
        # used to fire only for "unexplained_conflict", so a DIFFERENT
        # stated reason (missing_identifier, ID-02's own) was silently
        # discarded on a case that never proposed a merge at all. Any
        # stated reason on a null-strength sheet is itself the fact that
        # forces a human look; the specific reason no longer matters here.
        return finish(
            "ESCALATE", "B",
            "evidence_reasons carries %s even though evidence_strength is "
            "null (not a merge candidate): a stated reason must not be "
            "silently discarded" % reasons,
        )

    blank_fields = fact_sheet["blank_fields"] or []
    if blank_fields and "A" not in disabled:
        corroborating = fact_sheet["corroborating_facts"] or []
        contradicted = fact_sheet["contradicted_attributes"] or []
        effective_dates = fact_sheet["effective_dates"] or {}
        # Widened round 5 repair (2026-09-06, design review section B):
        # a blank field is not automatically NO-DATA just because
        # corroborating_facts is empty; ANY other observable fact on the
        # sheet (an identifier, a contradicted attribute, a stated date, a
        # hierarchy parent) is enough to force a human look rather than an
        # honest refusal. Closes ID-02 (identifiers non-empty); does not
        # close AD-08, whose sheet states nothing observable at all.
        observable = bool(
            corroborating
            or fact_sheet["identifiers"]
            or contradicted
            or effective_dates.get("as_of")
            or effective_dates.get("candidate_effective_date")
            or hierarchy_parents
        )
        if observable:
            return finish(
                "ESCALATE", "A",
                "blank field(s) %s but an observable fact supports without "
                "confirming (corroborating_facts=%s, identifiers=%s, "
                "contradicted_attributes=%s, effective_dates=%s, "
                "hierarchy_parents=%s)"
                # PRE-EXISTING BUG fixed 2026-09-06 (found while proving the
                # mutation seam fix): `identifiers` here used to be the bare
                # local name, which is only ever bound inside the rule_r
                # branch far above. Any sheet that reaches this ESCALATE
                # with rule_r not run (stated_relation != "none", or
                # JBEQ_DECIDE_DISABLE_RULES disables rule_r) raised
                # UnboundLocalError instead of returning an answer. Reading
                # fact_sheet["identifiers"] directly matches what
                # `observable` above already checks.
                % (blank_fields, corroborating, fact_sheet["identifiers"],
                   contradicted, effective_dates, hierarchy_parents),
            )
        return finish(
            "NO-DATA", "A",
            "blank field(s) %s and nothing observable corroborates them"
            % blank_fields,
        )

    if not _relation_is_none(stated_relation, disabled):
        return finish(
            "LINK AS RELATED", "1",
            "stated_relation=%r: the input itself states a relation" % stated_relation,
        )
    return finish(
        "KEEP SEPARATE", "1",
        "stated_relation=%r: the input states no relation (same_site_only "
        "reads like none here too: a shared address alone is never the "
        "stated relation)" % stated_relation,
    )


def decide(fact_sheet, allow_unrecognized=False):
    """Apply the fixed rule table to one fact sheet. Returns
    {"answer": str, "rule_fired": str, "why": str}. Never raises on a
    malformed sheet: a missing field is reported as NO-DATA, never a guess.

    allow_unrecognized: for a historical re-decision of sheets extracted
    before the round 5 repair (2026-09-06). When False (the default), a
    value outside its documented enum is refused with NO-DATA naming the
    field and value, exactly like a missing field. When True, the refusal
    is skipped and the rule table runs on the sheet's raw values instead
    (an off-enum value still matches no rule keyed on it, same as before
    this fix); every field found off-enum is recorded on the result as
    "unrecognized_values" instead of being silently dropped.

    MUTATION SEAM, fail loud (hub PR 386 security finding, 2026-09-06):
    JBEQ_DECIDE_DISABLE_RULES is a test-only hook that runs this engine
    with one or more safety rules turned off (see _disabled_rules() and
    the module docstring). When it is set to anything, EVERY result this
    function returns, on every code path in _decide_unwrapped, carries
    "mutation": {"disabled": [...]} and its "why" is prefixed "MUTATION
    SEAM ACTIVE (rules disabled: ...): ", so nothing downstream (a
    decisions.jsonl row, an answers.json file, a caller reading the dict
    directly) can mistake a mutated run for a real one. See cmd_decide's
    stderr banner and its refusal to write into benchmarks/jbeq/mdm/runs/
    while a seam is active, and scripts/jbeq_mdm.py score's refusal of a
    marked answers file, for the rest of the fail-loud, fail-closed
    contract this exists to make true.
    """
    disabled = _disabled_rules()
    result = _decide_unwrapped(fact_sheet, allow_unrecognized, disabled)
    if disabled:
        disabled_ids = sorted(disabled)
        result = dict(result)
        result["mutation"] = {"disabled": disabled_ids}
        result["why"] = "MUTATION SEAM ACTIVE (rules disabled: %s): %s" % (
            ", ".join(disabled_ids), result["why"],
        )
    return result


def _runs_dir():
    """benchmarks/jbeq/mdm/runs/, the committed-record tree a mutation
    seam must never be able to write into (see cmd_decide)."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmarks", "jbeq", "mdm", "runs",
    )


def _under_runs_dir(path):
    """True if `path` resolves to _runs_dir() itself or anything inside
    it."""
    if not path:
        return False
    resolved = os.path.abspath(path)
    runs = os.path.abspath(_runs_dir())
    return resolved == runs or resolved.startswith(runs + os.sep)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)


def cmd_validate(args):
    sheets, err = _load_json(args.fact_sheets)
    if err:
        sys.stderr.write("NO-DATA: %s\n" % err)
        return EXIT_NODATA
    if not isinstance(sheets, dict):
        sys.stderr.write("NO-DATA: fact sheet file must be an object of "
                         "{case id: fact sheet}, got %s\n" % type(sheets).__name__)
        return EXIT_NODATA
    enums = _load_enums()
    bad = {}
    for case_id, sheet in sheets.items():
        if not isinstance(sheet, dict):
            bad[case_id] = ["fact sheet is not an object"]
            continue
        issues = ["missing %s" % f for f in _missing_fields(sheet)]
        issues += [
            "unrecognized value %s=%r" % (field, value)
            for field, value in _enum_violations(sheet, enums)
        ]
        if issues:
            bad[case_id] = issues
    if bad:
        for case_id in sorted(bad):
            print("REFUSED %s: %s" % (case_id, ", ".join(bad[case_id])))
        print("validate: %d of %d fact sheet(s) refused" % (len(bad), len(sheets)))
        return EXIT_NOT_DECIDED
    print("validate: %d fact sheet(s) OK" % len(sheets))
    return EXIT_OK


def cmd_decide(args):
    try:
        disabled = _disabled_rules()
    except ValueError as exc:
        # C2 (2026-09-06, qa-review-jbeq-2026-09-06.md): an unknown
        # mutation id must refuse loudly at the CLI, never disable
        # nothing and run as if the seam were off.
        sys.stderr.write("NO-DATA: %s\n" % exc)
        return EXIT_NODATA
    if disabled:
        # Loud (hub PR 386): one stderr banner per run naming the disabled
        # ids, so a mutation seam can never run silently even if nobody
        # reads decisions.jsonl or answers.json.
        sys.stderr.write(
            "MUTATION SEAM ACTIVE (rules disabled: %s): this run's "
            "decisions are not a real decision run\n" % ", ".join(sorted(disabled))
        )
        # Closed at the record: a mutated run must never become a
        # committed record by accident.
        if _under_runs_dir(args.out) or _under_runs_dir(args.decisions):
            sys.stderr.write(
                "REFUSED: a mutation seam is active; refusing to write "
                "into benchmarks/jbeq/mdm/runs/, where a mutated run "
                "could become a committed record by accident\n"
            )
            return EXIT_NODATA

    sheets, err = _load_json(args.fact_sheets)
    if err:
        sys.stderr.write("NO-DATA: %s\n" % err)
        return EXIT_NODATA
    if not isinstance(sheets, dict):
        sys.stderr.write("NO-DATA: fact sheet file must be an object of "
                         "{case id: fact sheet}, got %s\n" % type(sheets).__name__)
        return EXIT_NODATA

    answers = {}
    decisions = []
    unrecognized_count = 0
    for case_id in sheets:
        sheet = sheets[case_id]
        if not isinstance(sheet, dict):
            result = {"answer": "NO-DATA", "rule_fired": "validation",
                      "why": "fact sheet is not an object"}
        else:
            result = decide(sheet, allow_unrecognized=args.allow_unrecognized)
        answers[case_id] = result["answer"]
        row = {"case_id": case_id, "answer": result["answer"],
               "rule_fired": result["rule_fired"], "why": result["why"]}
        if "unrecognized_values" in result:
            row["unrecognized_values"] = result["unrecognized_values"]
            unrecognized_count += 1
        if "mutation" in result:
            row["mutation"] = result["mutation"]
        decisions.append(row)

    if disabled:
        # The engine-answers writer: record the marker in the answers JSON
        # itself, under a key no real case id can collide with, so
        # scripts/jbeq_mdm.py score can see it without the decisions file
        # beside it.
        answers["_mutation"] = {"disabled": sorted(disabled)}

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(answers, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("NO-DATA: cannot write %s: %s\n" % (args.out, exc))
        return EXIT_NODATA

    try:
        with open(args.decisions, "w", encoding="utf-8") as fh:
            for row in decisions:
                fh.write(json.dumps(row, ensure_ascii=False))
                fh.write("\n")
    except OSError as exc:
        sys.stderr.write("NO-DATA: cannot write %s: %s\n" % (args.decisions, exc))
        return EXIT_NODATA

    print("wrote %d answer(s) to %s and %s" % (len(answers), args.out, args.decisions))
    if unrecognized_count:
        print("%d of %d sheet(s) carried an unrecognized value, allowed "
              "through by --allow-unrecognized (see decisions.jsonl "
              "unrecognized_values)" % (unrecognized_count, len(answers)))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd")

    d = sub.add_parser("decide", help="apply the rule table to a fact sheet file")
    d.add_argument("fact_sheets")
    d.add_argument("--out", default="answers.json")
    d.add_argument("--decisions", default="decisions.jsonl")
    d.add_argument(
        "--allow-unrecognized", action="store_true",
        help="for a historical re-decision of sheets extracted before the "
             "round 5 enum repair: skip the unrecognized-value refusal and "
             "record each sheet's off-enum fields on its decision row "
             "instead of refusing it",
    )
    d.set_defaults(func=cmd_decide)

    v = sub.add_parser("validate", help="refuse a fact sheet file missing required fields")
    v.add_argument("fact_sheets")
    v.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_NODATA
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
