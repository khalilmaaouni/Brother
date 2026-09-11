#!/usr/bin/env python3
"""Tests for scripts/jbeq_decide.py, the JBEQ-MDM decision engine.

WHAT THIS DRIVES BACKWARDS. Every one of the 9 invented cases in
benchmarks/jbeq/mdm/generalization-cases-2026-09-05.json gets a fact sheet
authored BY HAND from that case's own prompt text (never from its expected
answer or rationale), run through decide(), and checked against the case's
own expected answer. This never touches the frozen JBEQ-MDM seed.

Plus negative tests for the rules the diagnosis this module was built from
named by hand: a weak-evidence irreversible merge never yields a merge term,
a stated tenant boundary always yields REJECT MATCH, a blank field with a
corroborating fact yields ESCALATE and without one yields NO-DATA, three
hierarchy parents of different types yield LINK AS RELATED, and a fact sheet
missing a required field yields NO-DATA naming it.

MUTATION CONTROL. This suite is also run once with JBEQ_DECIDE_DISABLE_RULES
set to "1" (bare, no comma), which is the standing hook jbeq_decide.decide()
checks (see its docstring) to skip the rule table entirely. That run is not
part of this file's own pass/fail contract: it is invoked separately
(`JBEQ_DECIDE_DISABLE_RULES=1 python3 scripts/test_jbeq_decide.py -v`) to
prove the rule table matters, and at least one test here is expected to fail
under it.

PER-RULE MUTATION. TestPerRuleMutation below drives the finer-grained switch
added 2026-09-06 (FIX-DIRECTIVE section 20): JBEQ_DECIDE_DISABLE_RULES also
takes a comma list of individual rule ids, so a test can disable exactly ONE
rule and show that the case it protects flips to a different answer, rather
than only that the whole table matters. Each test sets and restores the
environment variable itself, run inline in this same process.

MUTATION SEAM MARKER (hub PR 386 security finding, 2026-09-06):
JBEQ_DECIDE_DISABLE_RULES was a fail-open test hook with nothing to tell a
mutated decide() result from a real one apart. _decide_with_disabled below
now asserts, on every call, that the mutated result carries "mutation":
{"disabled": [...]} and a "why" prefixed "MUTATION SEAM ACTIVE (rules
disabled: ...): ", so every test in TestPerRuleMutation proves the marker
as well as the flipped answer it already checked. See
scripts/test_jbeq_mutation_seams.py for the marker's absence when no rule
is disabled, the CLI banner, the scorer's refusal, and the runs/-directory
write refusal.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jbeq_decide  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERALIZATION_CASES = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "generalization-cases-2026-09-05.json"
)

ALL_ANSWERS = ["AUTO-MERGE", "SUGGEST MERGE", "LINK AS RELATED", "KEEP SEPARATE",
               "REJECT MATCH", "ESCALATE", "NO-DATA"]

# E100: one base fact sheet every test starts from and overrides. This keeps
# every REQUIRED_FIELDS key present without repeating all fourteen of them in
# every test, and it is verified (test_base_sheet_has_every_required_field)
# to actually carry them all so the shortcut cannot silently rot.
BASE_SHEET = {
    "track": "entity-object",
    "allowed_answers": list(ALL_ANSWERS),
    "identifiers": [],
    "stated_relation": "none",
    "stated_difference": "none",
    "tenant_boundary": "none",
    "evidence_strength": None,
    "evidence_reasons": [],
    "corroborating_facts": [],
    "blank_fields": [],
    "irreversible": False,
    "history_exists": False,
    "hierarchy_parents": [],
    "one_to_many_object": False,
    # Identity-kind fields, promoted to required round 5 (2026-09-06). All
    # default to a value that never trips a new rule or gate, so every test
    # written before round 5 keeps its own answer unless it overrides one.
    "object_type_a": "legal_entity",
    "object_type_b": "legal_entity",
    "lifecycle": "active",
    "requested_action": "none",
    "requested_relation_type": None,
    "authoritative_identifier": "aligned",
    "contradicted_attributes": [],
    "distinct_operational_attributes": False,
    "location_comparison": None,
    "effective_dates": {"as_of": None, "candidate_effective_date": None, "conflict": False},
}


def sheet(**overrides):
    s = dict(BASE_SHEET)
    s.update(overrides)
    return s


def load_generalization_cases():
    with open(GENERALIZATION_CASES, encoding="utf-8") as fh:
        return json.load(fh)


class TestBaseSheet(unittest.TestCase):
    def test_base_sheet_has_every_required_field(self):
        missing = jbeq_decide._missing_fields(BASE_SHEET)
        self.assertEqual(missing, [], "BASE_SHEET must carry every required field")


class TestGeneralizationCases(unittest.TestCase):
    """One fact sheet per invented case in generalization-cases-2026-09-05.json,
    hand-authored from that case's own 'input' text. No sibling A-to-D
    generalization file was found pushed to origin/wbs/jbeq-rules-a-to-d at
    the time this suite was written (checked via `git show` against that
    branch, which still matched origin/main), so this file covers the 9
    cases that exist and the A-to-D rules are exercised by the negative
    tests below instead, built from the diagnosis text's own rule
    statements.
    """

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in load_generalization_cases()["cases"]}

    def _check(self, case_id, fact_sheet):
        case = self.cases[case_id]
        result = jbeq_decide.decide(fact_sheet)
        self.assertEqual(
            result["answer"], case["expected"],
            "%s: expected %s, got %s (rule %s: %s)"
            % (case_id, case["expected"], result["answer"],
               result["rule_fired"], result["why"]),
        )

    # G5: same address only, one side a one-to-many object -> KEEP SEPARATE.
    def test_G5_01_bookstore_shared_delivery_hub(self):
        self._check("G5-01", sheet(
            track="entity-object",
            allowed_answers=self.cases["G5-01"]["allowed"],
            stated_relation="same_site_only",
            one_to_many_object=True,
        ))

    def test_G5_02_factory_shared_tech_center(self):
        self._check("G5-02", sheet(
            track="entity-object",
            allowed_answers=self.cases["G5-02"]["allowed"],
            stated_relation="same_site_only",
            one_to_many_object=True,
        ))

    def test_G5_03_pharmacy_shared_call_center(self):
        self._check("G5-03", sheet(
            track="entity-object",
            allowed_answers=self.cases["G5-03"]["allowed"],
            stated_relation="same_site_only",
            one_to_many_object=True,
        ))

    # G6: shared reading only, no stated refuting fact -> KEEP SEPARATE.
    def test_G6_01_hayashi_reading_match_only(self):
        self._check("G6-01", sheet(
            track="match-or-no-merge",
            allowed_answers=self.cases["G6-01"]["allowed"],
            stated_relation="none",
            stated_difference="none",
        ))

    def test_G6_02_tanaka_department_differs(self):
        self._check("G6-02", sheet(
            track="match-or-no-merge",
            allowed_answers=self.cases["G6-02"]["allowed"],
            stated_relation="none",
            stated_difference="none",
        ))

    def test_G6_03_minami_one_digit_address(self):
        self._check("G6-03", sheet(
            track="match-or-no-merge",
            allowed_answers=self.cases["G6-03"]["allowed"],
            stated_relation="none",
            stated_difference="none",
        ))

    # G7: same external entity, separate company-code records -> LINK AS RELATED.
    def test_G7_01_hibari_two_company_codes(self):
        self._check("G7-01", sheet(
            track="hierarchy",
            allowed_answers=self.cases["G7-01"]["allowed"],
            stated_relation="group_company_code_shared_entity",
        ))

    def test_G7_02_watari_three_local_entities(self):
        self._check("G7-02", sheet(
            track="hierarchy",
            allowed_answers=self.cases["G7-02"]["allowed"],
            stated_relation="group_company_code_shared_entity",
        ))

    def test_G7_03_sekino_two_subsidiaries(self):
        self._check("G7-03", sheet(
            track="hierarchy",
            allowed_answers=self.cases["G7-03"]["allowed"],
            stated_relation="group_company_code_shared_entity",
        ))


class TestNegatives(unittest.TestCase):
    MERGE_TERMS = {"AUTO-MERGE", "SUGGEST MERGE"}

    def test_weak_evidence_irreversible_never_merges(self):
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            evidence_strength="weak",
            evidence_reasons=["score_only", "missing_identifier"],
            irreversible=True,
        ))
        self.assertNotIn(result["answer"], self.MERGE_TERMS)
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "B")

    def test_stated_tenant_boundary_always_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            tenant_boundary="stated",
            evidence_strength="strong",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "C")

    def test_stated_hierarchy_dimension_difference_rejects(self):
        # Round 10 (2026-09-06, HI-09): two hierarchy nodes that share a
        # name but the input states live in different hierarchy dimensions
        # (a location hierarchy versus an organisation hierarchy) refute
        # identity even though no other stated_difference value fits.
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            object_type_a="hierarchy_node",
            object_type_b="hierarchy_node",
            stated_difference="different_hierarchy_dimension",
            evidence_strength="strong",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "C")

    def test_blank_field_with_corroboration_escalates(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            blank_fields=["prefecture"],
            corroborating_facts=["banchi_and_postal_code_match"],
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "A")

    def test_blank_field_without_corroboration_is_nodata(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            blank_fields=["prefecture"],
            corroborating_facts=[],
        ))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "A")

    def test_three_hierarchy_types_link_as_related(self):
        # Round 8 (2026-09-06): rule D used to answer KEEP SEPARATE here;
        # the founder's ruling (docs/decisions/
        # decision-p0-3-rule-d-hi01-2026-09-06.json) flipped it to LINK AS
        # RELATED, since the rule just found three valid relations and
        # KEEP SEPARATE means no relation to record.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            hierarchy_parents=[
                {"type": "capital", "parent": "A"},
                {"type": "trade_flow", "parent": "B"},
                {"type": "reporting", "parent": "C"},
            ],
        ))
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "D")

    def test_missing_required_field_is_nodata_naming_it(self):
        s = sheet()
        del s["tenant_boundary"]
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "validation")
        self.assertIn("tenant_boundary", result["why"])

    def test_unsupported_track_is_honest_nodata(self):
        result = jbeq_decide.decide(sheet(track="survivorship"))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "track-unsupported")

    def test_answer_outside_allowed_set_remaps_along_caution_rank(self):
        # Round 7 repair (2026-09-06, review section C, id
        # allowed_answers_remap): rule 7 fires LINK AS RELATED, which this
        # case's own allowed_answers does not carry; walking CAUTION_RANK
        # toward the conservative end lands on ESCALATE (the next allowed
        # member), never the old immediate NO-DATA, and rule_fired stays
        # "7", the rule that actually decided.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            allowed_answers=["ESCALATE", "NO-DATA"],
            stated_relation="group_company_code_shared_entity",
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "7")

    def test_answer_outside_allowed_set_is_nodata_only_when_nothing_reachable(self):
        # The old safety-net NO-DATA behaviour still applies when
        # allowed_answers carries nothing this ordering recognizes at all.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            allowed_answers=["R1", "R2"],
            stated_relation="group_company_code_shared_entity",
        ))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "safety-net")

    def test_finish_never_returns_an_answer_outside_allowed(self):
        # FIX-DIRECTIVE review section C, U-23's own shape: rule A's honest
        # refusal would choose NO-DATA, which this case's own
        # allowed_answers does not carry at all. The old safety net
        # returned NO-DATA here anyway (itself outside allowed), exactly
        # the invariant this engine exists to hold.
        s = sheet(
            track="match-or-no-merge",
            stated_relation="none",
            allowed_answers=["ESCALATE", "LINK AS RELATED"],
            blank_fields=["priority_source"],
        )
        result = jbeq_decide.decide(s)
        self.assertIn(result["answer"], s["allowed_answers"])
        self.assertNotEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "A")

    # 2026-09-06, FIX-DIRECTIVE section 18/21 and the design review section
    # C: REJECT MATCH is for a proposal the input's own facts refute, never
    # for a case that also states a relation. EO-02 and EO-09's shape:
    # stated_difference=different_legal_entity (which used to be an
    # unconditional REJECT MATCH) alongside a stated_relation that is not
    # "none".
    def test_stated_relation_beats_different_legal_entity_for_link(self):
        result = jbeq_decide.decide(sheet(
            track="entity-object",
            stated_relation="role_pair",
            stated_difference="different_legal_entity",
        ))
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "link_vs_reject")

    def test_different_legal_entity_alone_still_rejects(self):
        # No stated relation at all: boundary rule 2's original generic case
        # is unchanged (EO-04, AD-04, AD-10, ID-05, MM-03, HI-05 in the round
        # 4 fact sheets all have this shape and must keep REJECT MATCH).
        # requested_action=match_on_stated_basis records the proposal rule
        # 2's REJECT MATCH now requires (round 8, proposal_gate); see
        # TestPerRuleMutation for the no-proposal KEEP SEPARATE case.
        result = jbeq_decide.decide(sheet(
            track="entity-object",
            stated_relation="none",
            stated_difference="different_legal_entity",
            requested_action="match_on_stated_basis",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "2")

    # 2026-09-06, FIX-DIRECTIVE section 15/AD-06: evidence_reasons must not
    # be discarded just because evidence_strength is null (not a merge
    # candidate at all).
    def test_unexplained_conflict_without_a_score_still_escalates(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            evidence_strength=None,
            evidence_reasons=["unexplained_conflict"],
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "B")

    # Round 6 repair (2026-09-06, review section A ID-02): the branch above
    # used to key on "unexplained_conflict" specifically, so a DIFFERENT
    # stated reason (missing_identifier, ID-02's own) on a null-strength
    # sheet was silently discarded instead of forcing ESCALATE. Any stated
    # reason must not be discarded, not just this one value.
    def test_any_reason_without_a_score_still_escalates(self):
        result = jbeq_decide.decide(sheet(
            track="identifier",
            evidence_strength=None,
            evidence_reasons=["missing_identifier"],
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "B")

    # 2026-09-06, FIX-DIRECTIVE section 15/MM-09: weak evidence reduced to
    # nothing but a bare match score, with no irreversibility or history to
    # force a human look, is NO-DATA rather than an automatic ESCALATE.
    def test_weak_evidence_score_only_with_nothing_else_is_nodata(self):
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            evidence_strength="weak",
            evidence_reasons=["score_only", "missing_identifier"],
            irreversible=False,
            history_exists=False,
        ))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "B")

    def test_weak_evidence_contradicted_attributes_keeps_separate(self):
        # contradicted_attributes is the OPTIONAL, not-yet-schema-carried
        # field from the module docstring; exercised here so the branch is
        # proven even though no fact sheet in the wild sets it today.
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            evidence_strength="weak",
            evidence_reasons=["missing_identifier"],
            contradicted_attributes=["registered_address"],
        ))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "B")

    def test_requested_relation_type_conflicts_with_stated_hierarchy_type(self):
        # Renamed 2026-09-06 (round 5 repair, review section F): four real
        # round 5 sheets (HI-02, HI-04, HI-05, HI-10) now fill
        # requested_relation_type, so "inert today" was stale even before
        # this fix; this test itself never claimed inertness (the field
        # was always exercisable by hand), only the name and comment did.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            stated_relation="trade_flow",
            hierarchy_parents=[{"type": "trade_flow", "parent": "X"}],
            requested_relation_type="capital",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "link_vs_reject")

    def test_requested_relation_type_against_empty_hierarchy_rejects(self):
        # HI-05 fix (round 5 repair, 2026-09-06): before, the guard required
        # stated_hierarchy_types be non-empty before checking membership, so
        # a requested_relation_type asked against NOTHING stated at all
        # (hierarchy_parents=[]) short-circuited past this rule instead of
        # being seen as a refuted proposal.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            stated_relation="role_pair",
            hierarchy_parents=[],
            requested_relation_type="capital",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "link_vs_reject")

    # requested_parent (round 6 repair, 2026-09-06, review section C HI-02):
    # requested_relation_type matching a stated TYPE is not the same as
    # matching its DIRECTION. HI-02 asks to reverse parent and child inside
    # the same capital hierarchy; the type check alone cannot see that.
    def test_requested_parent_reverses_stated_hierarchy_direction(self):
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            stated_relation="parent_child",
            hierarchy_parents=[{"type": "capital", "parent": "HD Holdings"}],
            requested_relation_type="capital",
            requested_parent="Subsidiary Co",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "link_vs_reject")

    def test_requested_parent_matching_the_stated_parent_does_not_reject(self):
        # The type matches AND the direction matches: no reversal, so this
        # clause must not fire (the type-mismatch branch above it already
        # covers a genuine conflict).
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            stated_relation="parent_child",
            hierarchy_parents=[{"type": "capital", "parent": "HD Holdings"}],
            requested_relation_type="capital",
            requested_parent="HD Holdings",
        ))
        self.assertNotEqual(result["rule_fired"], "link_vs_reject")

    def test_requested_parent_absent_is_a_noop(self):
        # Every sheet in the wild before round 6 lacks this OPTIONAL key
        # entirely; .get() must return None and change nothing.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            stated_relation="parent_child",
            hierarchy_parents=[{"type": "capital", "parent": "HD Holdings"}],
            requested_relation_type="capital",
        ))
        self.assertNotEqual(result["rule_fired"], "link_vs_reject")

    # Round 5 (2026-09-06), design-p0-3-mdm-merge-safety section C: rule L
    # reads the new location_comparison field, closing AD-03.
    def test_location_different_administrative_area_rejects(self):
        # requested_action records the proposal rule L's REJECT MATCH now
        # requires (round 8, proposal_gate).
        result = jbeq_decide.decide(sheet(
            track="address",
            location_comparison="different_administrative_area",
            requested_action="match_on_stated_basis",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "L")

    def test_location_internally_inconsistent_escalates(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            location_comparison="internally_inconsistent",
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "L")

    # Round 5 AUTO-MERGE safety gate (design section C): a case that rule B
    # alone would call AUTO-MERGE (strong evidence, no reasons, not
    # irreversible, no history) is blocked by each gate item in turn.
    def _strong_no_reason_sheet(self, **overrides):
        return sheet(
            track="entity-object",
            evidence_strength="strong",
            evidence_reasons=[],
            irreversible=False,
            history_exists=False,
            **overrides,
        )

    def test_auto_merge_stands_with_no_gate_blocking(self):
        result = jbeq_decide.decide(self._strong_no_reason_sheet())
        self.assertEqual(result["answer"], "AUTO-MERGE")
        self.assertEqual(result["rule_fired"], "B")

    def test_object_type_mismatch_without_stated_relation_rejects_via_rule_r(self):
        # Round 5 repair (2026-09-06): rule_r now fires for object_type
        # mismatch WHENEVER stated_relation == "none" (the default here),
        # ahead of the merge ladder entirely, and REJECT MATCH is the
        # stronger, safer verdict when nothing states a relation at all.
        # This sheet used to reach the merge ladder's own "gate-object-
        # type" item (see the next test for that item, still reachable
        # when a relation IS stated).
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            object_type_a="legal_entity", object_type_b="store",
            requested_action="match_on_stated_basis",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_gate_object_type_mismatch_links_instead_of_merging(self):
        # A stated relation ("role_pair") keeps rule_r's stated_relation=="
        # none" gate closed, so this exercises the merge ladder's own
        # object-type gate instead.
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            stated_relation="role_pair",
            object_type_a="legal_entity", object_type_b="store",
        ))
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "gate-object-type")

    def test_lifecycle_closed_without_stated_relation_keeps_separate_via_rule_r(self):
        # Round 7 repair (2026-09-06, review section D, U-36): rule_r's own
        # lifecycle clause used to answer REJECT MATCH here while the
        # merge-ladder's gate-lifecycle answers KEEP SEPARATE for the
        # identical fact (see test_gate_lifecycle_closed_keeps_separate
        # below); rule_r now agrees with it instead of contradicting it.
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            lifecycle="closed",
        ))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_gate_lifecycle_closed_keeps_separate(self):
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            stated_relation="role_pair",
            lifecycle="closed",
        ))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "gate-lifecycle")

    def test_lifecycle_relocated_without_stated_relation_reaches_merge_ladder(self):
        # Round 9 repair (2026-09-06, review-u2-2026-09-06.md section B, id
        # relocated_not_a_bar): unlike closed above, a relocated record no
        # longer stops at rule_r at all; it falls through to the merge
        # ladder's own gate-lifecycle item, which answers SUGGEST MERGE
        # rather than KEEP SEPARATE (see test_gate_lifecycle_relocated_
        # suggests_merge below for the isolated form of the same gate).
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            lifecycle="relocated",
        ))
        self.assertEqual(result["answer"], "SUGGEST MERGE")
        self.assertEqual(result["rule_fired"], "gate-lifecycle")

    def test_gate_lifecycle_relocated_suggests_merge(self):
        # A relocated record is a compatible lifecycle of the SAME object,
        # not a different object the way closed and identifier_reused are,
        # so it bars AUTO-MERGE only: a person still confirms with SUGGEST
        # MERGE instead of KEEP SEPARATE.
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            stated_relation="role_pair",
            lifecycle="relocated",
        ))
        self.assertEqual(result["answer"], "SUGGEST MERGE")
        self.assertEqual(result["rule_fired"], "gate-lifecycle")

    def test_gate_authoritative_identifier_absent_suggests_merge(self):
        # "absent" (not "conflicting") on purpose: rule_r's own
        # authoritative_identifier trigger only matches "conflicting" (see
        # test_rule_r_authoritative_identifier_conflicting_rejects below),
        # so "absent" reaches the merge ladder's own gate unshadowed.
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            authoritative_identifier="absent",
        ))
        self.assertEqual(result["answer"], "SUGGEST MERGE")
        self.assertEqual(result["rule_fired"], "gate-authoritative-identifier")

    def test_stated_relation_beats_strong_evidence_for_auto_merge(self):
        # EO-05 fix (round 5 repair, 2026-09-06, design review section E):
        # an unflagged false AUTO-MERGE, cleared on neutral defaults never
        # read from the input. A stated relation must record the relation
        # instead of silently merging past it, even with strong,
        # unconflicted evidence.
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            stated_relation="role_pair",
        ))
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "gate-stated-relation")

    def test_gate_site_store_distinct_operational_attributes_links(self):
        # Promoted round 5 repair (2026-09-06) from a gate reachable only
        # inside this strong-evidence AUTO-MERGE candidate to a top-level
        # rule (closing MM-10, HI-08, both evidence_strength=null and so
        # never reaching the old gate at all); rule_fired is now the
        # top-level id "site_store", not "gate-site-store".
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            distinct_operational_attributes=True,
        ))
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "site_store")

    def test_gate_temporal_conflict_keeps_separate(self):
        result = jbeq_decide.decide(self._strong_no_reason_sheet(
            effective_dates={"as_of": "2026-01-01",
                             "candidate_effective_date": "2026-03-01",
                             "conflict": True},
        ))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "gate-temporal")

    def test_temporal_track_no_longer_blanket_nodata(self):
        # Round 5: "temporal" left UNSUPPORTED_TRACKS. A plain temporal-track
        # case with nothing else stated falls through to the ordinary
        # default rule, not to the old track-unsupported NO-DATA.
        result = jbeq_decide.decide(sheet(track="temporal"))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "1")

    def test_survivorship_and_requirements_still_unsupported(self):
        for track in ("survivorship", "requirements"):
            with self.subTest(track=track):
                result = jbeq_decide.decide(sheet(track=track))
                self.assertEqual(result["answer"], "NO-DATA")
                self.assertEqual(result["rule_fired"], "track-unsupported")

    # Round 5 repair (2026-09-06), rule_r (design review section A): fires
    # only when stated_relation == "none", closing the eight REJECT-versus-
    # KEEP cases the round 5 regression lost (EO-04, MM-02, MM-03, AD-04,
    # AD-10, ID-01, ID-04, ID-05).
    # requested_action=match_on_stated_basis records the proposal rule_r's
    # REJECT MATCH branches now require (round 8, proposal_gate); see
    # TestPerRuleMutation for the no-proposal KEEP SEPARATE case.
    def test_rule_r_authoritative_identifier_conflicting_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="entity-object",
            stated_relation="none",
            authoritative_identifier="conflicting",
            requested_action="match_on_stated_basis",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_rule_r_identifiers_same_value_different_domain_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="identifier",
            stated_relation="none",
            requested_action="match_on_stated_basis",
            identifiers=[
                {"value": "7010001234567", "domain": "internal_customer_number",
                 "legal_person_type": None, "status": "valid"},
                {"value": "7010001234567", "domain": "corporate_number",
                 "legal_person_type": "corporation", "status": "valid"},
            ],
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_rule_r_identifiers_different_legal_person_type_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="match-or-no-merge",
            stated_relation="none",
            requested_action="match_on_stated_basis",
            identifiers=[
                {"value": "A", "domain": "tax_id",
                 "legal_person_type": "individual", "status": "valid"},
                {"value": "B", "domain": "corporate_number",
                 "legal_person_type": "corporation", "status": "valid"},
            ],
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_rule_r_object_type_mismatch_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="entity-object",
            stated_relation="none",
            object_type_a="legal_entity",
            object_type_b="store",
            requested_action="match_on_stated_basis",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_rule_r_lifecycle_reused_keeps_separate(self):
        # Round 7 repair (2026-09-06, review section D, id
        # rule_r_lifecycle_keep): see
        # test_lifecycle_closed_without_stated_relation_keeps_separate_via_
        # rule_r above for the self-contradiction this closes.
        result = jbeq_decide.decide(sheet(
            track="identifier",
            stated_relation="none",
            lifecycle="identifier_reused",
        ))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_rule_r_location_different_unit_in_building_rejects(self):
        result = jbeq_decide.decide(sheet(
            track="address",
            stated_relation="none",
            location_comparison="different_unit_in_building",
            requested_action="match_on_stated_basis",
        ))
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_rule_r_gate_requires_stated_relation_none(self):
        # Load-bearing per the design review: EO-09 states parent_child
        # with a conflicting authoritative_identifier and must keep LINK,
        # never flip to REJECT, because a relation IS stated.
        result = jbeq_decide.decide(sheet(
            track="hierarchy",
            stated_relation="parent_child",
            authoritative_identifier="conflicting",
        ))
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "1")

    def test_rule_r_does_not_fire_on_a_neutral_no_relation_sheet(self):
        # IC-04-shaped: nothing stated, no trigger; the KEEP SEPARATE
        # default must still stand, not rule_r.
        result = jbeq_decide.decide(sheet(track="entity-object", stated_relation="none"))
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "1")

    # Round 5 repair (2026-09-06), rule A widened (design review section B):
    # observable now covers identifiers, contradicted_attributes,
    # effective_dates and hierarchy_parents, not only corroborating_facts.
    def test_blank_field_with_only_identifiers_observable_escalates(self):
        result = jbeq_decide.decide(sheet(
            track="identifier",
            blank_fields=["replacement_identifier"],
            corroborating_facts=[],
            identifiers=[{"value": "T3010005555555",
                          "domain": "qualified_invoice_issuer_number",
                          "legal_person_type": None, "status": "expired"}],
        ))
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "A")

    def test_blank_field_with_nothing_observable_at_all_stays_nodata(self):
        # AD-08-shaped: the widening does not manufacture an observable
        # fact where the sheet states none.
        result = jbeq_decide.decide(sheet(
            track="address",
            blank_fields=["prefecture"],
            corroborating_facts=[],
        ))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "A")

    # Round 5 repair (2026-09-06), the L12 enum-validation fix.
    def test_decide_refuses_unrecognized_stated_relation(self):
        result = jbeq_decide.decide(sheet(stated_relation="bogus_relation"))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "unrecognized-value")
        self.assertIn("stated_relation", result["why"])
        self.assertIn("bogus_relation", result["why"])

    def test_decide_allow_unrecognized_runs_the_rule_table_anyway(self):
        # A historical re-decision: the off-enum stated_relation matches no
        # rule keyed on the literal value "none" (rule_r's own gate stays
        # closed, same as before this fix), so the sheet falls through to
        # boundary rule 1's default (any non-"none" string states A
        # relation); the sheet's own off-enum field is recorded rather
        # than dropped.
        result = jbeq_decide.decide(
            sheet(stated_relation="bogus_relation"),
            allow_unrecognized=True,
        )
        self.assertEqual(result["answer"], "LINK AS RELATED")
        self.assertEqual(result["rule_fired"], "1")
        self.assertIn("unrecognized_values", result)
        self.assertIn("stated_relation='bogus_relation'", str(result["unrecognized_values"]))


class TestPerRuleMutation(unittest.TestCase):
    """FIX-DIRECTIVE section 20: disabling ONE safety rule must break the
    case that rule protects, or the rule is not actually proven. Before
    2026-09-06, JBEQ_DECIDE_DISABLE_RULES was all-or-nothing, which the
    design review (design-p0-3-mdm-merge-safety section E) named as a gap:
    disabling every rule at once cannot show any ONE rule is load-bearing.
    Each test here disables exactly one rule id (see the module docstring
    for the full list) and checks the answer flips.
    """

    def _decide_with_disabled(self, rule_id, fact_sheet):
        old = os.environ.get("JBEQ_DECIDE_DISABLE_RULES")
        os.environ["JBEQ_DECIDE_DISABLE_RULES"] = rule_id
        try:
            result = jbeq_decide.decide(fact_sheet)
        finally:
            if old is None:
                os.environ.pop("JBEQ_DECIDE_DISABLE_RULES", None)
            else:
                os.environ["JBEQ_DECIDE_DISABLE_RULES"] = old
        # Hub PR 386 security finding (2026-09-06): every result decide()
        # returns while a mutation seam is active must carry the marker,
        # not only the flipped answer. Checked once here so every test in
        # this class proves it, not only the answer it names.
        expected_ids = sorted({tok.strip() for tok in rule_id.split(",") if tok.strip()})
        self.assertEqual(result.get("mutation"), {"disabled": expected_ids})
        self.assertTrue(
            result["why"].startswith(
                "MUTATION SEAM ACTIVE (rules disabled: %s): " % ", ".join(expected_ids)
            ),
            result["why"],
        )
        return result

    def test_disabling_rule_C_flips_stated_tenant_boundary(self):
        s = sheet(
            track="match-or-no-merge",
            tenant_boundary="stated",
            evidence_strength="strong",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "C")
        mutated = self._decide_with_disabled("C", s)
        self.assertNotEqual(mutated["answer"], "REJECT MATCH")

    def test_disabling_rule_c_hierarchy_dimension_flips_hierarchy_dimension_difference(self):
        # Round 10 (2026-09-06, HI-09): rule_c_hierarchy_dimension gates
        # only the new stated_difference value, so disabling it must flip
        # this specific case while leaving the tenant-boundary case above
        # (and a plain different_identifier_domain case) firing rule C.
        s = sheet(
            track="match-or-no-merge",
            object_type_a="hierarchy_node",
            object_type_b="hierarchy_node",
            stated_difference="different_hierarchy_dimension",
            evidence_strength="strong",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "C")
        mutated = self._decide_with_disabled("rule_c_hierarchy_dimension", s)
        self.assertNotEqual(mutated["answer"], "REJECT MATCH")

        other = sheet(
            track="match-or-no-merge",
            stated_difference="different_identifier_domain",
            evidence_strength="strong",
        )
        still_fires = self._decide_with_disabled("rule_c_hierarchy_dimension", other)
        self.assertEqual(still_fires["answer"], "REJECT MATCH")
        self.assertEqual(still_fires["rule_fired"], "C")

    def test_disabling_rule_5_flips_site_only_one_to_many(self):
        s = sheet(
            track="entity-object",
            stated_relation="same_site_only",
            one_to_many_object=True,
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "5")
        # Round 11 (2026-09-06, id same_site_only_never_a_relation): boundary
        # rule 1 now reads same_site_only like "none" too (F2), so disabling
        # rule 5 ALONE still answers KEEP SEPARATE off rule "1" instead of
        # falling through to the old LINK AS RELATED default; that is the
        # fix working as intended (defense in depth), not a regression.
        # Both ids must be off together to see the pre-F2 behaviour.
        mutated_rule_5_only = self._decide_with_disabled("5", s)
        self.assertEqual(mutated_rule_5_only["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated_rule_5_only["rule_fired"], "1")
        mutated = self._decide_with_disabled(
            "5,same_site_only_never_a_relation", s
        )
        self.assertNotEqual(mutated["answer"], "KEEP SEPARATE")

    def test_disabling_rule_7_flips_group_company_code_under_weak_evidence(self):
        # A bare group_company_code_shared_entity sheet cannot prove rule 7
        # is load-bearing on its own: boundary rule 1's default also answers
        # LINK AS RELATED for any non-none stated_relation, so removing rule
        # 7 alone would change nothing. Pairing it with weak evidence makes
        # rule 7's placement ahead of the merge ladder the only reason the
        # answer isn't ESCALATE.
        s = sheet(
            track="hierarchy",
            stated_relation="group_company_code_shared_entity",
            evidence_strength="weak",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "LINK AS RELATED")
        self.assertEqual(baseline["rule_fired"], "7")
        mutated = self._decide_with_disabled("7", s)
        self.assertNotEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["answer"], "ESCALATE")

    def test_disabling_link_vs_reject_flips_stated_relation_under_different_legal_entity(self):
        # requested_action records the proposal rule 2's REJECT MATCH now
        # requires once link_vs_reject stops intercepting it.
        s = sheet(
            track="entity-object",
            stated_relation="role_pair",
            stated_difference="different_legal_entity",
            requested_action="match_on_stated_basis",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "LINK AS RELATED")
        self.assertEqual(baseline["rule_fired"], "link_vs_reject")
        mutated = self._decide_with_disabled("link_vs_reject", s)
        self.assertEqual(mutated["answer"], "REJECT MATCH")
        self.assertEqual(mutated["rule_fired"], "2")

    def test_disabling_rule_D_flips_three_hierarchy_types(self):
        # Proves the founder-ruling flip (round 8, 2026-09-06) is still
        # rule D's own doing: with "D" disabled, the same three-type
        # hierarchy sheet falls through to boundary rule 1's default
        # (stated_relation == "none": KEEP SEPARATE), never LINK AS
        # RELATED.
        s = sheet(
            track="hierarchy",
            hierarchy_parents=[
                {"type": "capital", "parent": "A"},
                {"type": "trade_flow", "parent": "B"},
                {"type": "reporting", "parent": "C"},
            ],
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "LINK AS RELATED")
        self.assertEqual(baseline["rule_fired"], "D")
        mutated = self._decide_with_disabled("D", s)
        self.assertNotEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated["rule_fired"], "1")

    # Round 5 AUTO-MERGE safety gate mutation tests (FIX-DIRECTIVE section
    # 20 / design section E): disabling exactly one gate id must flip the
    # class it protects back to AUTO-MERGE.
    def _strong_no_reason_sheet(self, **overrides):
        s = dict(BASE_SHEET)
        s.update(track="entity-object", evidence_strength="strong",
                 evidence_reasons=[], irreversible=False, history_exists=False)
        s.update(overrides)
        return s

    def test_disabling_gate_object_type_alone_is_still_blocked_by_rule_r(self):
        # Round 5 repair (2026-09-06): with stated_relation=="none" (the
        # default), rule_r ALSO rejects an object-type mismatch, ahead of
        # the merge ladder entirely. Disabling the merge ladder's own
        # "object_type" gate alone therefore does not reach AUTO-MERGE:
        # rule_r still blocks it first. Both must be disabled together to
        # prove neither is redundant (see the next test).
        s = self._strong_no_reason_sheet(
            object_type_a="legal_entity", object_type_b="store",
            requested_action="match_on_stated_basis",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "rule_r")
        mutated = self._decide_with_disabled("object_type", s)
        self.assertEqual(mutated["answer"], "REJECT MATCH")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    def test_disabling_gate_object_type_and_rule_r_together_flips_to_auto_merge(self):
        s = self._strong_no_reason_sheet(object_type_a="legal_entity", object_type_b="store")
        mutated = self._decide_with_disabled("object_type,rule_r", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")
        # disabling rule_r alone still leaves the merge-ladder gate standing
        rule_r_only = self._decide_with_disabled("rule_r", s)
        self.assertEqual(rule_r_only["answer"], "LINK AS RELATED")
        self.assertEqual(rule_r_only["rule_fired"], "gate-object-type")

    def test_disabling_gate_lifecycle_alone_is_still_blocked_by_rule_r(self):
        # Round 7 (2026-09-06): both baseline and mutated now answer KEEP
        # SEPARATE, not REJECT MATCH, since rule_r's own lifecycle clause
        # was fixed to agree with gate-lifecycle (rule_r_lifecycle_keep);
        # rule_r still fires first either way, which is what this test
        # proves.
        s = self._strong_no_reason_sheet(lifecycle="closed")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "rule_r")
        mutated = self._decide_with_disabled("lifecycle", s)
        self.assertEqual(mutated["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    def test_disabling_gate_lifecycle_and_rule_r_together_flips_to_auto_merge(self):
        s = self._strong_no_reason_sheet(lifecycle="closed")
        mutated = self._decide_with_disabled("lifecycle,rule_r", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")
        rule_r_only = self._decide_with_disabled("rule_r", s)
        self.assertEqual(rule_r_only["answer"], "KEEP SEPARATE")
        self.assertEqual(rule_r_only["rule_fired"], "gate-lifecycle")

    # Round 9 repair (2026-09-06, review-u2-2026-09-06.md section B): a
    # relocated record now bars AUTO-MERGE only, answering SUGGEST MERGE
    # both when rule_r would otherwise fall through to the merge ladder
    # (stated_relation=="none") and when the merge ladder's own
    # gate-lifecycle item is reached directly (a stated relation keeps
    # rule_r closed). id relocated_not_a_bar isolates the split at both
    # call sites: disabling it reverts relocated to the old bar-both
    # behaviour, proving the split is load-bearing rather than free.
    def test_disabling_relocated_not_a_bar_flips_relocation_via_rule_r(self):
        s = self._strong_no_reason_sheet(lifecycle="relocated")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "SUGGEST MERGE")
        self.assertEqual(baseline["rule_fired"], "gate-lifecycle")
        mutated = self._decide_with_disabled("relocated_not_a_bar", s)
        self.assertEqual(mutated["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    def test_disabling_relocated_not_a_bar_flips_relocation_via_gate_lifecycle(self):
        s = self._strong_no_reason_sheet(
            stated_relation="role_pair", lifecycle="relocated",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "SUGGEST MERGE")
        self.assertEqual(baseline["rule_fired"], "gate-lifecycle")
        mutated = self._decide_with_disabled("relocated_not_a_bar", s)
        self.assertEqual(mutated["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated["rule_fired"], "gate-lifecycle")

    def test_disabling_gate_authoritative_identifier_flips_to_auto_merge(self):
        # "absent", not "conflicting": rule_r's own authoritative_identifier
        # trigger only matches "conflicting" (see TestNegatives), so
        # "absent" isolates the merge-ladder gate alone.
        s = self._strong_no_reason_sheet(authoritative_identifier="absent")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "SUGGEST MERGE")
        self.assertEqual(baseline["rule_fired"], "gate-authoritative-identifier")
        mutated = self._decide_with_disabled("authoritative_identifier", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")

    def test_disabling_gate_stated_relation_flips_to_auto_merge(self):
        # EO-05 fix (round 5 repair, 2026-09-06): the false AUTO-MERGE the
        # design review found uncaught until this gate existed.
        s = self._strong_no_reason_sheet(stated_relation="role_pair")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "LINK AS RELATED")
        self.assertEqual(baseline["rule_fired"], "gate-stated-relation")
        mutated = self._decide_with_disabled("stated_relation_gate", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")

    def test_disabling_gate_site_store_flips_to_auto_merge(self):
        s = self._strong_no_reason_sheet(distinct_operational_attributes=True)
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "LINK AS RELATED")
        self.assertEqual(baseline["rule_fired"], "site_store")
        mutated = self._decide_with_disabled("site_store", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")

    def test_disabling_gate_temporal_flips_to_auto_merge(self):
        s = self._strong_no_reason_sheet(effective_dates={
            "as_of": "2026-01-01", "candidate_effective_date": "2026-03-01",
            "conflict": True,
        })
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "gate-temporal")
        mutated = self._decide_with_disabled("temporal", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")

    def test_disabling_rule_L_flips_different_administrative_area(self):
        s = sheet(track="address", location_comparison="different_administrative_area",
                   requested_action="match_on_stated_basis")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "L")
        mutated = self._decide_with_disabled("L", s)
        self.assertNotEqual(mutated["answer"], "REJECT MATCH")

    # Round 6 repair (2026-09-06, review section B group 3, flagged the
    # weakest of the round's changes): two location_comparison values no
    # rule read at all.
    def test_disabling_rule_L_flips_notation_variant_only(self):
        # evidence_strength stays null (AD-01's own shape: no score at all)
        # so the answer comes from rule L alone, not from the ordinary
        # merge ladder falling through to the same AUTO-MERGE by luck.
        s = sheet(track="address", location_comparison="notation_variant_only")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "AUTO-MERGE")
        self.assertEqual(baseline["rule_fired"], "L")
        mutated = self._decide_with_disabled("L", s)
        self.assertNotEqual(mutated["answer"], "AUTO-MERGE")

    def test_notation_variant_only_still_clears_the_auto_merge_gate(self):
        # The gate must still block it exactly like a strong-evidence
        # AUTO-MERGE candidate: a lifecycle event blocks the merge here too.
        s = sheet(track="address", location_comparison="notation_variant_only",
                   lifecycle="closed")
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "gate-lifecycle")

    def test_disabling_rule_L_flips_same_chiban_different_notation(self):
        s = sheet(track="address", location_comparison="same_chiban_different_notation")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "SUGGEST MERGE")
        self.assertEqual(baseline["rule_fired"], "L")
        mutated = self._decide_with_disabled("L", s)
        self.assertNotEqual(mutated["answer"], "SUGGEST MERGE")

    def test_disabling_rule_r_flips_authoritative_identifier_conflicting(self):
        s = sheet(track="entity-object", stated_relation="none",
                   authoritative_identifier="conflicting",
                   requested_action="match_on_stated_basis")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "rule_r")
        mutated = self._decide_with_disabled("rule_r", s)
        self.assertNotEqual(mutated["answer"], "REJECT MATCH")

    # Round 6 repair (2026-09-06, review section A AD-10 and section B group
    # 1): one disable id, relation_partition, protects TWO call sites. Both
    # halves are proven in one test so the id's own scope (both, not either)
    # is what the mutation demonstrates.
    def test_disabling_relation_partition_flips_ad10_and_group1_cases(self):
        # AD-10 shape: different_legal_entity + same_site_only must reach
        # rule 2's REJECT MATCH, never link_vs_reject's LINK AS RELATED.
        ad10_like = sheet(
            track="address",
            stated_difference="different_legal_entity",
            stated_relation="same_site_only",
            authoritative_identifier="conflicting",
            requested_action="match_on_stated_basis",
        )
        baseline = jbeq_decide.decide(ad10_like)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "2")
        mutated = self._decide_with_disabled("relation_partition", ad10_like)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "link_vs_reject")

        # Group 1 shape: corporate_number_shared plus otherwise-clean strong
        # evidence must reach AUTO-MERGE, never gate-stated-relation's
        # downgrade to LINK AS RELATED.
        group1_like = self._strong_no_reason_sheet(
            stated_relation="corporate_number_shared",
        )
        baseline2 = jbeq_decide.decide(group1_like)
        self.assertEqual(baseline2["answer"], "AUTO-MERGE")
        mutated2 = self._decide_with_disabled("relation_partition", group1_like)
        self.assertEqual(mutated2["answer"], "LINK AS RELATED")
        self.assertEqual(mutated2["rule_fired"], "gate-stated-relation")

    # Round 6 repair (2026-09-06, review section B group 2 AD-09): an
    # inferred object-type difference must never outrank a stated absence.
    def test_disabling_rule_r_blank_fields_guard_flips_ad09_shape(self):
        s = sheet(
            track="address",
            object_type_a="address",
            object_type_b="site",
            blank_fields=["prefecture"],
            requested_action="match_on_stated_basis",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "NO-DATA")
        self.assertEqual(baseline["rule_fired"], "A")
        mutated = self._decide_with_disabled("rule_r_blank_fields_guard", s)
        self.assertEqual(mutated["answer"], "REJECT MATCH")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    # Round 6 repair (2026-09-06, review section B group 2 EO-07): weak
    # evidence never asserts a relation, even under
    # distinct_operational_attributes=true.
    def test_disabling_site_store_weak_guard_flips_eo07_shape(self):
        s = sheet(
            track="entity-object",
            distinct_operational_attributes=True,
            evidence_strength="weak",
            evidence_reasons=["missing_identifier"],
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "ESCALATE")
        self.assertEqual(baseline["rule_fired"], "B")
        mutated = self._decide_with_disabled("site_store_weak_guard", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "site_store")

    # Round 7 repair (2026-09-06, review section D, U-36): rule_r's own
    # lifecycle clause used to answer REJECT MATCH while gate-lifecycle
    # answers KEEP SEPARATE for the identical fact, an engine self-
    # contradiction. id rule_r_lifecycle_keep isolates just this clause's
    # own answer, independent of whether rule_r fires at all. Uses
    # identifier_reused, not relocated: round 9 dropped relocated from
    # this clause entirely (id relocated_not_a_bar, see
    # TestPerRuleMutation below), so identifier_reused is the fact that
    # still reaches it by default.
    def test_disabling_rule_r_lifecycle_keep_flips_to_reject_match(self):
        s = sheet(track="temporal", stated_relation="none",
                   lifecycle="identifier_reused",
                   requested_action="match_on_stated_basis")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "rule_r")
        mutated = self._decide_with_disabled("rule_r_lifecycle_keep", s)
        self.assertEqual(mutated["answer"], "REJECT MATCH")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    # Round 7 repair (2026-09-06, review section D, U-38): the AD-09
    # blank_fields guard is widened to also cover evidence_reasons and
    # contradicted_attributes, and now guards rule_r's object_type clause
    # too (extending the reasoning AD-09 already established for it).
    def test_disabling_rule_r_blank_fields_guard_widened_flips_object_type_shape(self):
        s = sheet(
            track="temporal",
            stated_relation="none",
            object_type_a="legal_entity",
            object_type_b="store",
            contradicted_attributes=["relocation_date"],
            evidence_reasons=["unexplained_conflict"],
            requested_action="match_on_stated_basis",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "ESCALATE")
        self.assertEqual(baseline["rule_fired"], "B")
        mutated = self._decide_with_disabled("rule_r_blank_fields_guard", s)
        self.assertEqual(mutated["answer"], "REJECT MATCH")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    # Same widened guard, proven against the lifecycle clause specifically:
    # this is the exact mechanism U-38 needed (a STATED conflict must not
    # be outranked by rule_r's lifecycle clause either). Uses
    # identifier_reused, not relocated: round 9 dropped relocated from
    # this clause entirely (id relocated_not_a_bar), so identifier_reused
    # is the fact that still reaches it by default.
    def test_disabling_rule_r_blank_fields_guard_widened_flips_lifecycle_shape(self):
        s = sheet(
            track="temporal",
            stated_relation="none",
            lifecycle="identifier_reused",
            contradicted_attributes=["relocation_date"],
            evidence_reasons=["unexplained_conflict"],
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "ESCALATE")
        self.assertEqual(baseline["rule_fired"], "B")
        mutated = self._decide_with_disabled("rule_r_blank_fields_guard", s)
        # rule_r_lifecycle_keep is still active (only the guard above it
        # was disabled), so the lifecycle clause fires with its OWN fixed
        # answer, KEEP SEPARATE, not the pre-round-7 REJECT MATCH.
        self.assertEqual(mutated["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated["rule_fired"], "rule_r")

    # Round 8 repair (2026-09-06, review-u1-2026-09-06.md section A, id
    # proposal_gate): REJECT MATCH is for a proposed match or write the
    # facts refute; with no proposal on record at all, the same refutation
    # is KEEP SEPARATE instead. Disabling proposal_gate restores REJECT
    # MATCH unconditionally, exactly as every rule behaved before this fix.
    # One sheet per gated rule (2, L, rule_r) proves the id protects all
    # three, not just one.
    def test_disabling_proposal_gate_flips_no_proposal_cases_to_reject_match(self):
        no_proposal_cases = [
            ("2", sheet(track="entity-object", stated_relation="none",
                        stated_difference="different_legal_entity")),
            ("L", sheet(track="address",
                        location_comparison="different_administrative_area")),
            ("rule_r", sheet(track="entity-object", stated_relation="none",
                              authoritative_identifier="conflicting")),
        ]
        for rule_fired, s in no_proposal_cases:
            with self.subTest(rule_fired=rule_fired):
                self.assertEqual(s["requested_action"], "none")
                self.assertIsNone(s["requested_relation_type"])
                baseline = jbeq_decide.decide(s)
                self.assertEqual(baseline["answer"], "KEEP SEPARATE")
                self.assertEqual(baseline["rule_fired"], rule_fired)
                mutated = self._decide_with_disabled("proposal_gate", s)
                self.assertEqual(mutated["answer"], "REJECT MATCH")
                self.assertEqual(mutated["rule_fired"], rule_fired)

    # With a proposal on record, proposal_gate must not change anything:
    # the refuted proposal is REJECT MATCH whether or not the gate is
    # active, so disabling it here has no observable effect.
    def test_proposal_gate_is_a_noop_when_a_proposal_is_recorded(self):
        s = sheet(track="entity-object", stated_relation="none",
                   stated_difference="different_legal_entity",
                   requested_action="match_on_stated_basis")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "2")
        mutated = self._decide_with_disabled("proposal_gate", s)
        self.assertEqual(mutated["answer"], "REJECT MATCH")
        self.assertEqual(mutated["rule_fired"], "2")

    # Round 7 repair (2026-09-06, review section C, id
    # allowed_answers_remap): disabling it reverts finish() to the old
    # immediate-NO-DATA safety net, exactly the behaviour U-23 showed was
    # itself capable of returning an answer outside the case's own
    # allowed_answers.
    def test_disabling_allowed_answers_remap_flips_to_old_safety_net(self):
        s = sheet(
            track="hierarchy",
            allowed_answers=["ESCALATE", "NO-DATA"],
            stated_relation="group_company_code_shared_entity",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "ESCALATE")
        self.assertEqual(baseline["rule_fired"], "7")
        mutated = self._decide_with_disabled("allowed_answers_remap", s)
        self.assertEqual(mutated["answer"], "NO-DATA")
        self.assertEqual(mutated["rule_fired"], "safety-net")

    # Founder ruling 2026-09-06 (decision-p0-3-same-area-renamed-2026-09-06):
    # same_area_renamed (one place under an old and a new administrative
    # name) is a fact about the map, not confirmation the two registry
    # rows are one, so rule L answers SUGGEST MERGE, a person confirms,
    # replacing the round 7 AUTO-MERGE treatment.
    def test_same_area_renamed_gives_suggest_merge(self):
        s = sheet(track="address", location_comparison="same_area_renamed")
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "SUGGEST MERGE")
        self.assertEqual(result["rule_fired"], "L")
        self.assertIn("decision-p0-3-same-area-renamed-2026-09-06", result["why"])

    def test_disabling_rule_L_flips_same_area_renamed(self):
        s = sheet(track="address", location_comparison="same_area_renamed")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "SUGGEST MERGE")
        self.assertEqual(baseline["rule_fired"], "L")
        mutated = self._decide_with_disabled("L", s)
        self.assertNotEqual(mutated["answer"], "SUGGEST MERGE")

    # id renamed_area_needs_a_person isolates the founder-ruling branch:
    # disabling it alone (rule L stays active) reverts same_area_renamed
    # to the pre-ruling AUTO-MERGE-gate-checked path, the same path
    # notation_variant_only still uses, and the mutation marker must be
    # present on the result (_decide_with_disabled asserts it).
    def test_disabling_renamed_area_needs_a_person_flips_to_auto_merge(self):
        s = sheet(track="address", location_comparison="same_area_renamed")
        mutated = self._decide_with_disabled("renamed_area_needs_a_person", s)
        self.assertEqual(mutated["answer"], "AUTO-MERGE")
        self.assertEqual(mutated["rule_fired"], "L")

    def test_disabling_renamed_area_needs_a_person_still_clears_the_auto_merge_gate(self):
        # With the founder-ruling branch disabled, same_area_renamed falls
        # back to the pre-ruling path (see
        # test_notation_variant_only_still_clears_the_auto_merge_gate
        # above for the identical shape on notation_variant_only): a
        # lifecycle event blocks the merge through the merge-ladder's own
        # gate-lifecycle item, never reaching rule_r at all.
        s = sheet(track="address", location_comparison="same_area_renamed",
                   lifecycle="closed")
        result = self._decide_with_disabled("renamed_area_needs_a_person", s)
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "gate-lifecycle")

    # Round 12 repair (2026-09-07, review-rule6-2026-09-07.md ranking item
    # 2, closes U3 W-03): two addresses stating the same town and block and
    # differing only in lot number are an absence of support under
    # addendum rule 6, never a refutation, so rule L answers KEEP SEPARATE,
    # never REJECT MATCH and never a merge.
    def test_same_area_different_lot_gives_keep_separate(self):
        s = sheet(track="address", location_comparison="same_area_different_lot")
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "L")
        self.assertIn("same_area_different_lot", result["why"])

    # id same_area_different_lot_keeps_separate isolates this branch:
    # disabled, the case falls through the location ladder entirely (it
    # never matches different_administrative_area, the pre-round-12
    # mis-extraction this fixes) all the way to boundary rule 1's own
    # stated_relation reading, which flips the answer whenever
    # stated_relation is not "none".
    def test_disabling_same_area_different_lot_keeps_separate_flips_to_link_as_related(self):
        s = sheet(track="address", location_comparison="same_area_different_lot",
                   stated_relation="corporate_number_shared")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "L")
        mutated = self._decide_with_disabled("same_area_different_lot_keeps_separate", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "1")

    # Round 10 repair (2026-09-06, review-u3-2026-09-06.md W-04): rule 5
    # (id "5", unchanged) is hoisted above the location ladder so it
    # answers before a notation-variant-only address match can reach rule
    # L's AUTO-MERGE gates. Before the hoist this exact shape (a shared,
    # one-to-many object whose address is a notation variant, with a
    # different object_type on each side) answered LINK AS RELATED off
    # gate-object-type without rule 5 ever being read.
    def test_rule_5_answers_before_location_ladder_on_notation_variant(self):
        s = sheet(
            track="entity-object",
            stated_relation="same_site_only",
            one_to_many_object=True,
            location_comparison="notation_variant_only",
            object_type_a="site",
            object_type_b="store",
        )
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "5")

    def test_disabling_rule_5_flips_notation_variant_shared_hub_to_link_as_related(self):
        s = sheet(
            track="entity-object",
            stated_relation="same_site_only",
            one_to_many_object=True,
            location_comparison="notation_variant_only",
            object_type_a="site",
            object_type_b="store",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "5")
        mutated = self._decide_with_disabled("5", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "gate-object-type")

    def test_disabling_rule5_hoist_alone_flips_notation_variant_to_link_as_related(self):
        # M1 (2026-09-06, qa-review-jbeq-2026-09-06.md): before this id
        # existed, the only way to prove rule 5's hoisted position mattered
        # was to disable "5" itself, which also proves the rule's mere
        # existence, not its ORDER. rule5_hoist isolates just the ordering:
        # disabling it alone must produce the exact same flip as disabling
        # "5" on this fixture (test_disabling_rule_5_flips_notation_
        # variant_shared_hub_to_link_as_related, above, unchanged and still
        # passing: id "5" still fires everywhere it always did), without
        # naming "5" at all.
        s = sheet(
            track="entity-object",
            stated_relation="same_site_only",
            one_to_many_object=True,
            location_comparison="notation_variant_only",
            object_type_a="site",
            object_type_b="store",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "5")
        mutated = self._decide_with_disabled("rule5_hoist", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "gate-object-type")

    # Round 10 repair (2026-09-06, review-u3-2026-09-06.md W-01): a
    # relocation's own address difference never refutes identity, so rule
    # L's different_administrative_area branch exempts lifecycle=relocated
    # (id relocated_address_not_a_refutation) and falls through to the
    # merge ladder instead of REJECT MATCH / KEEP SEPARATE.
    def test_relocated_exempts_different_administrative_area_from_rule_L(self):
        s = sheet(
            track="address",
            lifecycle="relocated",
            location_comparison="different_administrative_area",
            evidence_strength="strong",
            history_exists=True,
        )
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "SUGGEST MERGE")
        self.assertEqual(result["rule_fired"], "B")

    def test_disabling_relocated_address_not_a_refutation_flips_to_rule_L(self):
        s = sheet(
            track="address",
            lifecycle="relocated",
            location_comparison="different_administrative_area",
            evidence_strength="strong",
            history_exists=True,
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "SUGGEST MERGE")
        self.assertEqual(baseline["rule_fired"], "B")
        mutated = self._decide_with_disabled("relocated_address_not_a_refutation", s)
        self.assertEqual(mutated["answer"], "KEEP SEPARATE")
        self.assertEqual(mutated["rule_fired"], "L")

    # Round 10 repair (2026-09-06, review-u3-2026-09-06.md W-19, addendum
    # rule 9): medium evidence with no confirmation reason stated
    # separately from that evidence itself (irreversible=False,
    # history_exists=False) must ESCALATE, not fall through to SUGGEST
    # MERGE. id medium_needs_confirmation isolates the branch.
    def test_medium_evidence_with_no_confirmation_reason_escalates(self):
        s = sheet(
            track="identifier",
            evidence_strength="medium",
            evidence_reasons=["unvalidated_crosswalk"],
            irreversible=False,
            history_exists=False,
        )
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "B")

    def test_disabling_medium_needs_confirmation_flips_to_suggest_merge(self):
        s = sheet(
            track="identifier",
            evidence_strength="medium",
            evidence_reasons=["unvalidated_crosswalk"],
            irreversible=False,
            history_exists=False,
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "ESCALATE")
        self.assertEqual(baseline["rule_fired"], "B")
        mutated = self._decide_with_disabled("medium_needs_confirmation", s)
        self.assertEqual(mutated["answer"], "SUGGEST MERGE")
        self.assertEqual(mutated["rule_fired"], "B")

    # Round 11 repair (2026-09-06, review-u4-2026-09-06.md W4-03, id
    # site_store_write_refutes): a per-object operational split REFUTES a
    # proposed write, it does not merely record a relation alongside it.
    def test_site_store_record_write_rejects_not_links(self):
        s = sheet(
            track="entity-object",
            distinct_operational_attributes=True,
            requested_action="record_write",
        )
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "site_store")

    def test_disabling_site_store_write_refutes_flips_to_link(self):
        s = sheet(
            track="entity-object",
            distinct_operational_attributes=True,
            requested_action="record_write",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        mutated = self._decide_with_disabled("site_store_write_refutes", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "site_store")

    # Round 11 repair (2026-09-06, review-u4-2026-09-06.md W4-04, id
    # link_vs_reject_write_refutes): a stated relation answers what the
    # relation is, it does not license a write; requested_action=
    # record_write lets the refuting stated_difference win instead.
    def test_link_vs_reject_record_write_rejects_not_links(self):
        s = sheet(
            track="entity-object",
            stated_difference="different_legal_entity",
            stated_relation="role_pair",
            requested_action="record_write",
        )
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "REJECT MATCH")
        self.assertEqual(result["rule_fired"], "2")

    def test_disabling_link_vs_reject_write_refutes_flips_to_link(self):
        s = sheet(
            track="entity-object",
            stated_difference="different_legal_entity",
            stated_relation="role_pair",
            requested_action="record_write",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "2")
        mutated = self._decide_with_disabled("link_vs_reject_write_refutes", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "link_vs_reject")

    # Round 11 repair (2026-09-06, review-u4-2026-09-06.md W4-23, id
    # same_site_only_never_a_relation): same_site_only reads like "none"
    # at rule_r's guard and boundary rule 1, exactly as rule 5 already
    # reads it for the one-to-many case; a shared address alone never
    # falls through to LINK AS RELATED on its own.
    def test_same_site_only_alone_keeps_separate_not_link(self):
        s = sheet(
            track="entity-object",
            stated_relation="same_site_only",
        )
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "KEEP SEPARATE")
        self.assertEqual(result["rule_fired"], "1")

    def test_disabling_same_site_only_never_a_relation_flips_to_link(self):
        s = sheet(
            track="entity-object",
            stated_relation="same_site_only",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "KEEP SEPARATE")
        self.assertEqual(baseline["rule_fired"], "1")
        mutated = self._decide_with_disabled("same_site_only_never_a_relation", s)
        self.assertEqual(mutated["answer"], "LINK AS RELATED")
        self.assertEqual(mutated["rule_fired"], "1")


class TestTemporalRules(unittest.TestCase):
    """review-temporal-track-2026-09-06.md: three date-ordering rules for
    the TM track's own R1/R2/NO-DATA vocabulary, gated on "R1" being a
    member of the case's own allowed_answers, never on track (module
    docstring precedence item 1.5)."""

    def _decide_with_disabled(self, rule_id, fact_sheet):
        old = os.environ.get("JBEQ_DECIDE_DISABLE_RULES")
        os.environ["JBEQ_DECIDE_DISABLE_RULES"] = rule_id
        try:
            result = jbeq_decide.decide(fact_sheet)
        finally:
            if old is None:
                os.environ.pop("JBEQ_DECIDE_DISABLE_RULES", None)
            else:
                os.environ["JBEQ_DECIDE_DISABLE_RULES"] = old
        expected_ids = sorted({tok.strip() for tok in rule_id.split(",") if tok.strip()})
        self.assertEqual(result.get("mutation"), {"disabled": expected_ids})
        self.assertTrue(
            result["why"].startswith(
                "MUTATION SEAM ACTIVE (rules disabled: %s): " % ", ".join(expected_ids)
            ),
            result["why"],
        )
        return result

    def _temporal_sheet(self, **overrides):
        base = dict(
            track="temporal",
            allowed_answers=["R1", "R2", "NO-DATA"],
            effective_dates={"as_of": None, "candidate_effective_date": None,
                              "conflict": False},
        )
        base.update(overrides)
        return sheet(**base)

    # TM-03 shape: as_of after the successor's own effective date.
    def test_at_or_after_successor_is_r2(self):
        s = self._temporal_sheet(effective_dates={
            "as_of": "2026-09-05", "candidate_effective_date": "2026-01-15",
            "conflict": False,
        })
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "R2")
        self.assertEqual(result["rule_fired"], "temporal_at_or_after_successor_is_r2")

    # TM-02 shape: as_of before the successor's start, predecessor active.
    def test_before_successor_is_r1(self):
        s = self._temporal_sheet(lifecycle="active", effective_dates={
            "as_of": "2025-02-10", "candidate_effective_date": "2025-04-01",
            "conflict": False,
        })
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "R1")
        self.assertEqual(result["rule_fired"], "temporal_before_successor_is_r1")

    # TM-09 shape: as_of before the successor's start, predecessor closed,
    # no prior_valid_to stated: a dormancy gap cannot be ruled out.
    def test_gap_is_nodata(self):
        s = self._temporal_sheet(lifecycle="closed", effective_dates={
            "as_of": "2026-03-15", "candidate_effective_date": "2026-04-01",
            "conflict": False,
        })
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "temporal_gap_is_nodata")
        self.assertIn("dormancy", result["why"])

    # TM-01 shape (blind round 12, 2026-09-07): as_of "2024-02" is before
    # candidate_effective_date "2024-06-01", lifecycle is stated closed,
    # but prior_valid_to "2024-03-31" (the closure date) is itself after
    # as_of: the record was still live as of as_of, so lifecycle=closed
    # (today's status) must not send this to rule_r's unconditional
    # lifecycle bar. Round 12 found the engine answering NO-DATA via
    # rule_r here instead of R1.
    def test_before_closure_with_prior_valid_to_is_r1(self):
        s = self._temporal_sheet(lifecycle="closed", effective_dates={
            "as_of": "2024-02", "candidate_effective_date": "2024-06-01",
            "conflict": False, "prior_valid_to": "2024-03-31",
        })
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "R1")
        self.assertEqual(result["rule_fired"], "temporal_before_closure_is_r1")

    # TM-09 shape (blind round 12, 2026-09-07): as_of "2026-03-15" is
    # before candidate_effective_date "2026-04-01", lifecycle is stated
    # closed, and prior_valid_to "2025-12-31" (the closure date) is at or
    # before as_of: the record really was already closed by as_of, so
    # temporal_before_closure_is_r1 must not fire and the case still
    # reaches rule_r's lifecycle clause, KEEP SEPARATE remapped to
    # NO-DATA (round 12's own correct answer, must not regress).
    def test_after_closure_with_prior_valid_to_stays_nodata(self):
        s = self._temporal_sheet(lifecycle="closed", effective_dates={
            "as_of": "2026-03-15", "candidate_effective_date": "2026-04-01",
            "conflict": False, "prior_valid_to": "2025-12-31",
        })
        result = jbeq_decide.decide(s)
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "rule_r")

    def test_disabling_at_or_after_successor_is_r2_flips_away_from_r2(self):
        s = self._temporal_sheet(effective_dates={
            "as_of": "2026-09-05", "candidate_effective_date": "2026-01-15",
            "conflict": False,
        })
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "R2")
        mutated = self._decide_with_disabled(
            "temporal_at_or_after_successor_is_r2", s)
        self.assertNotEqual(mutated["answer"], "R2")

    def test_disabling_before_successor_is_r1_flips_away_from_r1(self):
        s = self._temporal_sheet(lifecycle="active", effective_dates={
            "as_of": "2025-02-10", "candidate_effective_date": "2025-04-01",
            "conflict": False,
        })
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "R1")
        mutated = self._decide_with_disabled(
            "temporal_before_successor_is_r1", s)
        self.assertNotEqual(mutated["answer"], "R1")

    # Baseline and mutated both land on NO-DATA here (nothing else in the
    # ladder can answer R1 or R2 either), so the flip is proven on
    # rule_fired instead of answer: the honest gap reasoning is gone.
    def test_disabling_gap_is_nodata_changes_the_rule_that_fired(self):
        s = self._temporal_sheet(lifecycle="closed", effective_dates={
            "as_of": "2026-03-15", "candidate_effective_date": "2026-04-01",
            "conflict": False,
        })
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["rule_fired"], "temporal_gap_is_nodata")
        mutated = self._decide_with_disabled("temporal_gap_is_nodata", s)
        self.assertNotEqual(mutated["rule_fired"], "temporal_gap_is_nodata")

    # Same as_of/candidate_effective_date as the R2 case above, but this
    # case's own allowed_answers is the ordinary decision vocabulary, never
    # R1: the gate is on the vocabulary, never on track, so the three
    # rules must not fire even though track == "temporal" (U1 to U4 shape).
    def test_non_temporal_allowed_answers_are_untouched_by_the_same_dates(self):
        s = sheet(
            track="temporal",
            effective_dates={
                "as_of": "2026-09-05", "candidate_effective_date": "2026-01-15",
                "conflict": False,
            },
        )
        result = jbeq_decide.decide(s)
        self.assertNotIn(result["answer"], ("R1", "R2"))
        self.assertEqual(result["rule_fired"], "1")

    # Both dates fall in 2026-06; as_of carries no day, so the order inside
    # that month is unknowable and none of the three rules may fire.
    def test_month_precision_collision_does_not_fire(self):
        s = self._temporal_sheet(effective_dates={
            "as_of": "2026-06", "candidate_effective_date": "2026-06-15",
            "conflict": False,
        })
        result = jbeq_decide.decide(s)
        self.assertNotIn(result["rule_fired"], (
            "temporal_at_or_after_successor_is_r2",
            "temporal_before_successor_is_r1",
            "temporal_gap_is_nodata",
        ))

    def test_validate_refuses_same_area_different_lot_lookalike(self):
        # Round 12 (2026-09-07): same_area_different_lot is the correct
        # enum value; proves validate() does not also accept a lookalike
        # string still off the list.
        import subprocess
        import tempfile

        s = sheet(location_comparison="same_area_different_lot_number")
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-05": s}, fh)
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, jbeq_decide.EXIT_NOT_DECIDED)
            self.assertIn("location_comparison", result.stdout)
            self.assertIn("same_area_different_lot_number", result.stdout)


class BlankFieldsWithAStatedRelation(unittest.TestCase):
    """Regression for a crash two lanes hit independently the same day
    (2026-09-06): rule A's ESCALATE branch built its "why" string from the
    bare local `identifiers`, which rule_r binds only inside its own
    branch (stated_relation == "none"). Any fact sheet that reaches rule
    A's observable-blank-field ESCALATE without rule_r having run raised
    UnboundLocalError instead of answering, whether because stated_relation
    already states a relation (never "none", so rule_r's body never runs
    at all) or because JBEQ_DECIDE_DISABLE_RULES turns rule_r off. The fix
    reads fact_sheet["identifiers"] directly, matching what rule A's own
    `observable` check already reads. See the comment beside that line in
    scripts/jbeq_decide.py.
    """

    def _sheet(self, **overrides):
        # stated_relation is a RELATIONSHIP_ONLY_RELATIONS value, never
        # "none", so rule_r's whole branch is skipped and its local
        # `identifiers` name is never bound. blank_fields plus a
        # corroborating fact makes rule A's `observable` true, which is
        # what routes into the ESCALATE branch the old code crashed on.
        base = {
            "stated_relation": "parent_child",
            "blank_fields": ["effective_dates"],
            "corroborating_facts": ["a shared invoice number is stated"],
        }
        base.update(overrides)
        return sheet(**base)

    def test_stated_relation_with_no_rule_r_trigger_escalates_without_raising(self):
        result = jbeq_decide.decide(self._sheet())
        self.assertEqual(result["answer"], "ESCALATE")
        self.assertEqual(result["rule_fired"], "A")

    def test_disabling_rule_r_still_answers_with_the_mutation_marker(self):
        s = self._sheet()
        old = os.environ.get("JBEQ_DECIDE_DISABLE_RULES")
        os.environ["JBEQ_DECIDE_DISABLE_RULES"] = "rule_r"
        try:
            result = jbeq_decide.decide(s)
        finally:
            if old is None:
                os.environ.pop("JBEQ_DECIDE_DISABLE_RULES", None)
            else:
                os.environ["JBEQ_DECIDE_DISABLE_RULES"] = old
        self.assertEqual(result.get("mutation"), {"disabled": ["rule_r"]})
        self.assertTrue(
            result["why"].startswith(
                "MUTATION SEAM ACTIVE (rules disabled: rule_r): "
            ),
            result["why"],
        )
        self.assertEqual(result["answer"], "ESCALATE")

    def test_every_observable_empty_is_no_data_not_a_crash(self):
        result = jbeq_decide.decide(self._sheet(corroborating_facts=[]))
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "A")


class TestKnownRuleIds(unittest.TestCase):
    """C2 (2026-09-06, qa-review-jbeq-2026-09-06.md): JBEQ_DECIDE_DISABLE_
    RULES used to accept any token and silently disable nothing for an
    unknown one, so a mutation test with a mistyped id could never fail
    for the right reason. _disabled_rules() now raises ValueError for a
    token outside KNOWN_RULE_IDS, and this class also proves
    KNOWN_RULE_IDS itself cannot drift from the code: it greps
    jbeq_decide.py for every literal ever compared against `disabled` and
    asserts each one is a member.
    """

    def _set_disabled(self, value):
        old = os.environ.get("JBEQ_DECIDE_DISABLE_RULES")
        os.environ["JBEQ_DECIDE_DISABLE_RULES"] = value
        return old

    def _restore_disabled(self, old):
        if old is None:
            os.environ.pop("JBEQ_DECIDE_DISABLE_RULES", None)
        else:
            os.environ["JBEQ_DECIDE_DISABLE_RULES"] = old

    def test_unknown_rule_id_raises(self):
        old = self._set_disabled("totally_made_up")
        try:
            with self.assertRaises(ValueError) as ctx:
                jbeq_decide._disabled_rules()
            self.assertIn("totally_made_up", str(ctx.exception))
        finally:
            self._restore_disabled(old)

    def test_unknown_rule_id_raises_through_decide(self):
        # The raise must reach a caller that never touches _disabled_rules
        # directly, not just the private helper's own caller.
        old = self._set_disabled("bogus_typo_id_xyz")
        try:
            with self.assertRaises(ValueError) as ctx:
                jbeq_decide.decide(sheet())
            self.assertIn("bogus_typo_id_xyz", str(ctx.exception))
        finally:
            self._restore_disabled(old)

    def test_known_rule_id_still_disables(self):
        # A real id must keep working exactly as before: no exception, and
        # the case it protects still flips.
        s = sheet(
            track="entity-object", evidence_strength="strong",
            object_type_a="legal_entity", object_type_b="store",
            requested_action="match_on_stated_basis",
        )
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertEqual(baseline["rule_fired"], "rule_r")
        old = self._set_disabled("rule_r")
        try:
            self.assertEqual(jbeq_decide._disabled_rules(), {"rule_r"})
            mutated = jbeq_decide.decide(s)
            self.assertNotEqual(mutated["rule_fired"], "rule_r")
        finally:
            self._restore_disabled(old)

    def test_known_rule_ids_cannot_drift_from_the_code(self):
        # Grep the module source for every literal compared against
        # `disabled` and assert each one is in KNOWN_RULE_IDS, so adding a
        # new guarded id without also adding it here fails loudly instead
        # of silently reopening C2.
        import re

        src_path = os.path.join(REPO, "scripts", "jbeq_decide.py")
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        literals = set(re.findall(r'"([^"]*)" (?:not )?in disabled', src))
        self.assertTrue(literals, "the scan itself found nothing; the "
                        "regex or the source shape changed")
        missing = literals - jbeq_decide.KNOWN_RULE_IDS
        self.assertEqual(
            missing, set(),
            "id(s) compared against `disabled` in jbeq_decide.py but "
            "missing from KNOWN_RULE_IDS: %s" % sorted(missing),
        )

    def test_cli_decide_exits_nonzero_naming_the_unknown_token(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            out_path = os.path.join(tmp, "answers.json")
            decisions_path = os.path.join(tmp, "decisions.jsonl")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-01": sheet()}, fh)
            env = dict(os.environ)
            env["JBEQ_DECIDE_DISABLE_RULES"] = "bogus_typo_id_xyz"
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "decide", sheets_path, "--out", out_path,
                 "--decisions", decisions_path],
                capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bogus_typo_id_xyz", result.stderr)


class TestRefutedIdentityNeverMerges(unittest.TestCase):
    """M2 (2026-09-06, qa-review-jbeq-2026-09-06.md): the per-rule negative
    tests above each bind one fixture. This is the suite-level invariant
    they do not add up to: across the full population of committed
    regression sheets, a stated_difference of different_legal_entity or a
    lifecycle of closed/identifier_reused must never answer AUTO-MERGE or
    SUGGEST MERGE, whatever rule fires. A rule added tomorrow is covered by
    this test the moment its fixture lands in one of the three files,
    without anyone writing a new assertion for it.
    """

    REGRESSION_FILES = (
        "regression-sheets-2026-09-06.json",
        "regression-sheets-2026-09-06b.json",
        "regression-sheets-2026-09-06c.json",
    )

    def _load_all_sheets(self):
        sheets = {}
        for name in self.REGRESSION_FILES:
            path = os.path.join(REPO, "benchmarks", "jbeq", "mdm", name)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            for case_id, s in data.items():
                sheets[(name, case_id)] = s
        return sheets

    def test_no_merge_answer_on_a_refuted_identity(self):
        sheets = self._load_all_sheets()
        sys.stderr.write("sheets=%d\n" % len(sheets))
        violations = []
        for (name, case_id), s in sheets.items():
            refuted = (
                s.get("stated_difference") == "different_legal_entity"
                or s.get("lifecycle") in ("closed", "identifier_reused")
            )
            if not refuted:
                continue
            result = jbeq_decide.decide(s)
            if result["answer"] in ("AUTO-MERGE", "SUGGEST MERGE"):
                violations.append(
                    "%s/%s: answer=%s rule_fired=%s"
                    % (name, case_id, result["answer"], result["rule_fired"])
                )
        self.assertEqual(
            violations, [],
            "merge answer on a refuted identity: %s" % "; ".join(violations),
        )


class TestCLI(unittest.TestCase):
    def test_validate_and_decide_round_trip(self):
        import subprocess
        import tempfile

        cases = load_generalization_cases()["cases"]
        sheets = {}
        for case in cases:
            relation = "same_site_only" if case["track"] == "entity-object" else (
                "group_company_code_shared_entity" if case["track"] == "hierarchy"
                else "none"
            )
            sheets[case["id"]] = sheet(
                track=case["track"],
                allowed_answers=case["allowed"],
                stated_relation=relation,
                one_to_many_object=(relation == "same_site_only"),
            )
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            out_path = os.path.join(tmp, "answers.json")
            decisions_path = os.path.join(tmp, "decisions.jsonl")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump(sheets, fh)

            rc = subprocess.call(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path]
            )
            self.assertEqual(rc, 0)

            rc = subprocess.call(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "decide", sheets_path, "--out", out_path,
                 "--decisions", decisions_path]
            )
            self.assertEqual(rc, 0)
            with open(out_path, encoding="utf-8") as fh:
                answers = json.load(fh)
            self.assertEqual(set(answers), set(sheets))
            with open(decisions_path, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(lines), len(sheets))

    def test_validate_refuses_missing_field(self):
        import subprocess
        import tempfile

        s = sheet()
        del s["blank_fields"]
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-01": s}, fh)
            rc = subprocess.call(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path]
            )
            self.assertEqual(rc, jbeq_decide.EXIT_NOT_DECIDED)

    def test_validate_refuses_unrecognized_enum_value(self):
        # Round 5 repair (2026-09-06), the L12 fix: validate now checks
        # value membership, not only key presence.
        import subprocess
        import tempfile

        s = sheet(stated_difference="no_capital_relationship")
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-02": s}, fh)
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, jbeq_decide.EXIT_NOT_DECIDED)
            self.assertIn("stated_difference", result.stdout)
            self.assertIn("no_capital_relationship", result.stdout)

    def test_validate_refuses_stated_difference_outside_grown_enum(self):
        # Round 10 (2026-09-06): stated_difference grew a sixth value,
        # different_hierarchy_dimension. Proves the growth did not loosen
        # validation into accepting a lookalike string that is still not
        # on the list.
        import subprocess
        import tempfile

        s = sheet(stated_difference="different_hierarchy")
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-03": s}, fh)
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, jbeq_decide.EXIT_NOT_DECIDED)
            self.assertIn("stated_difference", result.stdout)
            self.assertIn("different_hierarchy", result.stdout)

    def test_validate_refuses_location_comparison_outside_the_enum(self):
        # 2026-09-06 founder ruling (decision-p0-3-same-area-renamed-
        # 2026-09-06) changed what same_area_renamed answers, not the
        # location_comparison enum itself. Proves that change did not
        # loosen validate() into accepting a lookalike string still off
        # the list (same_area_renamed is on it; this is not).
        import subprocess
        import tempfile

        s = sheet(location_comparison="same_area_rename")
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-04": s}, fh)
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, jbeq_decide.EXIT_NOT_DECIDED)
            self.assertIn("location_comparison", result.stdout)
            self.assertIn("same_area_rename", result.stdout)

    # m1 (2026-09-06, qa-review-jbeq-2026-09-06.md): one CLI-driven case
    # per remaining enum field, each asserting `validate` exits 1 naming
    # the field and the off-enum value (location_comparison and
    # stated_difference are already covered above).
    def _assert_validate_refuses_enum(self, case_id, field, bad_value, **overrides):
        import subprocess
        import tempfile

        s = sheet(**{field: bad_value}, **overrides)
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({case_id: s}, fh)
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "scripts", "jbeq_decide.py"),
                 "validate", sheets_path],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, jbeq_decide.EXIT_NOT_DECIDED)
            self.assertIn(field, result.stdout)
            self.assertIn(bad_value, result.stdout)

    def test_validate_refuses_stated_relation_outside_the_enum(self):
        self._assert_validate_refuses_enum(
            "X-05", "stated_relation", "shared_site_only",
        )

    def test_validate_refuses_evidence_strength_outside_the_enum(self):
        self._assert_validate_refuses_enum(
            "X-06", "evidence_strength", "very_strong",
        )

    def test_validate_refuses_requested_action_outside_the_enum(self):
        self._assert_validate_refuses_enum(
            "X-07", "requested_action", "record_delete",
        )

    def test_validate_refuses_lifecycle_outside_the_enum(self):
        self._assert_validate_refuses_enum(
            "X-08", "lifecycle", "terminated",
        )


if __name__ == "__main__":
    unittest.main()
