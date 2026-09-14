#!/usr/bin/env python3
"""Tests for claim_lifecycle.py (WBS-50.01)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import claim_lifecycle as CL
import contract_check as CC

BASE = {
    "schema_version": "claim-lifecycle-v1", "claim_id": "c1",
    "revision_id": "r1", "state": "DRAFT", "supersedes": None,
}


def rev(**overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return rec


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = CC.load_json(CL.DEFAULT_SCHEMA, "schema")

    def test_a_valid_draft_record_passes(self):
        self.assertEqual(CL.check(rev(), self.schema), [])

    def test_every_chain_state_alone_is_a_valid_record(self):
        for state in CL.STATES:
            problems = CL.check(rev(state=state), self.schema)
            self.assertEqual(problems, [], (state, problems))

    def test_missing_required_field_is_refused(self):
        rec = rev()
        del rec["claim_id"]
        problems = CL.check(rec, self.schema)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_wrong_schema_version_is_refused(self):
        problems = CL.check(rev(schema_version="wrong"), self.schema)
        self.assertTrue(problems)

    def test_unknown_state_is_refused(self):
        problems = CL.check(rev(state="APPROVED"), self.schema)
        self.assertTrue(problems)

    def test_additional_property_is_refused(self):
        rec = rev()
        rec["not_a_real_field"] = "x"
        problems = CL.check(rec, self.schema)
        self.assertTrue(problems)

    def test_supersedes_may_be_null_or_a_string(self):
        self.assertEqual(CL.check(rev(supersedes=None), self.schema), [])
        self.assertEqual(CL.check(rev(revision_id="r2", supersedes="r1"), self.schema), [])

    def test_supersedes_naming_own_revision_id_is_refused(self):
        problems = CL.check(rev(revision_id="r1", supersedes="r1"), self.schema)
        self.assertTrue(any("cannot supersede itself" in p for p in problems), problems)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write(self, name, record):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            json.dump(record, fh)
        return path

    def test_main_exits_0_on_pass_and_2_on_no_data(self):
        path = self.write("r.json", rev())
        self.assertEqual(CL.main([path]), 0)
        self.assertEqual(CL.main(["/no/such/record.json"]), 2)

    def test_main_exits_1_on_fail(self):
        rec = rev()
        del rec["state"]
        path = self.write("r.json", rec)
        self.assertEqual(CL.main([path]), 1)


class NextStateAndTransitionTests(unittest.TestCase):
    def test_next_state_walks_the_whole_chain_in_order(self):
        self.assertEqual(
            [CL.next_state(s) for s in CL.STATES],
            ["CHECKED", "FROZEN", "DECISION_USED", "OUTCOME_AVAILABLE",
             "RESOLVED", "SCORED", None])

    def test_next_state_of_unknown_value_is_none(self):
        self.assertIsNone(CL.next_state("NOT_A_STATE"))

    def test_is_valid_transition_true_for_every_adjacent_pair(self):
        for a, b in zip(CL.STATES, CL.STATES[1:]):
            self.assertTrue(CL.is_valid_transition(a, b), (a, b))

    def test_is_valid_transition_false_for_skip_ahead(self):
        self.assertFalse(CL.is_valid_transition("DRAFT", "FROZEN"))

    def test_is_valid_transition_false_for_backward_move(self):
        self.assertFalse(CL.is_valid_transition("FROZEN", "CHECKED"))

    def test_is_valid_transition_false_for_staying_put(self):
        self.assertFalse(CL.is_valid_transition("DRAFT", "DRAFT"))

    def test_scored_is_a_boundary_with_no_next_state(self):
        self.assertIsNone(CL.next_state("SCORED"))
        self.assertFalse(CL.is_valid_transition("SCORED", "DRAFT"))


class CheckTransitionTests(unittest.TestCase):
    def test_happy_path_one_step_forward_is_accepted(self):
        old = rev(state="DRAFT")
        new = rev(state="CHECKED")
        self.assertEqual(CL.check_transition(old, new), [])

    def test_full_happy_path_walk_through_every_state_is_accepted(self):
        state = rev(state="DRAFT")
        for target in CL.STATES[1:]:
            nxt = dict(state, state=target)
            self.assertEqual(CL.check_transition(state, nxt), [], target)
            state = nxt

    def test_skip_ahead_from_draft_to_frozen_is_refused(self):
        problems = CL.check_transition(rev(state="DRAFT"), rev(state="FROZEN"))
        self.assertTrue(any("not a valid transition" in p for p in problems), problems)

    def test_backward_move_is_refused(self):
        problems = CL.check_transition(rev(state="FROZEN"), rev(state="CHECKED"))
        self.assertTrue(any("not a valid transition" in p for p in problems), problems)

    def test_transition_past_scored_is_refused(self):
        problems = CL.check_transition(rev(state="SCORED"), rev(state="DRAFT"))
        self.assertTrue(problems)

    def test_changing_claim_id_mid_transition_is_refused(self):
        old = rev(state="DRAFT")
        new = rev(state="CHECKED", claim_id="different")
        problems = CL.check_transition(old, new)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_changing_revision_id_mid_transition_is_refused(self):
        old = rev(state="DRAFT")
        new = rev(state="CHECKED", revision_id="r2")
        problems = CL.check_transition(old, new)
        self.assertTrue(any("revision_id" in p for p in problems), problems)

    def test_changing_supersedes_mid_transition_is_refused(self):
        old = rev(state="DRAFT", supersedes=None)
        new = rev(state="CHECKED", supersedes="somewhere")
        problems = CL.check_transition(old, new)
        self.assertTrue(any("supersedes" in p for p in problems), problems)


class CheckMutationTests(unittest.TestCase):
    def test_editing_a_field_before_frozen_is_unrestricted(self):
        # DRAFT and CHECKED are before the FROZEN boundary: the roadmap
        # only forbids silent mutation once a revision is FROZEN or later.
        old = rev(state="CHECKED", claim_id="c1")
        new = rev(state="CHECKED", claim_id="c1-renamed")
        self.assertEqual(CL.check_mutation(old, new), [])

    def test_silently_changing_claim_id_once_frozen_is_refused(self):
        old = rev(state="FROZEN")
        new = rev(state="FROZEN", claim_id="different")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("claim_id" in p and "cannot be silently mutated" in p
                            for p in problems), problems)

    def test_silently_changing_supersedes_once_frozen_is_refused(self):
        old = rev(state="FROZEN", supersedes=None)
        new = rev(state="FROZEN", supersedes="r0")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("supersedes" in p for p in problems), problems)

    def test_advancing_state_forward_once_frozen_is_still_allowed(self):
        old = rev(state="FROZEN")
        new = rev(state="DECISION_USED")
        self.assertEqual(CL.check_mutation(old, new), [])

    def test_advancing_state_past_frozen_stays_guarded_every_step(self):
        # Boundary check across the whole tail of the chain, not only the
        # one FROZEN -> DECISION_USED step.
        old = rev(state="OUTCOME_AVAILABLE")
        new = rev(state="RESOLVED", claim_id="swapped")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_skip_ahead_state_change_once_frozen_is_refused(self):
        old = rev(state="FROZEN")
        new = rev(state="RESOLVED")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("not a valid transition" in p for p in problems), problems)

    def test_comparing_different_revision_ids_is_refused_outright(self):
        old = rev(state="FROZEN", revision_id="r1")
        new = rev(state="FROZEN", revision_id="r2")
        problems = CL.check_mutation(old, new)
        self.assertTrue(problems)

    def test_h3_a_field_outside_the_old_hardcoded_allowlist_is_now_refused(self):
        # Opus H3: check_mutation used to compare only claim_id,
        # revision_id and supersedes -- schema_version (a real field the
        # schema requires) could change once FROZEN and pass silently.
        # It must now be caught by the full-field comparison.
        old = rev(state="FROZEN", schema_version="claim-lifecycle-v1")
        new = rev(state="FROZEN", schema_version="claim-lifecycle-v2")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("schema_version" in p and "cannot be silently mutated" in p
                            for p in problems), problems)

    def test_m3_a_malformed_state_string_no_longer_silently_bypasses_the_guard(self):
        # Opus M3: a lowercase or otherwise unrecognized state used to
        # return [] no matter what else changed, silently disabling the
        # whole guard. It must now be flagged itself.
        old = rev(state="frozen", claim_id="c1")
        new = rev(state="frozen", claim_id="different")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("unrecognized state" in p for p in problems), problems)

    def test_m3_a_none_state_no_longer_silently_bypasses_the_guard(self):
        old = rev(state=None, claim_id="c1")
        new = rev(state=None, claim_id="different")
        problems = CL.check_mutation(old, new)
        self.assertTrue(any("unrecognized state" in p for p in problems), problems)


class CheckCorrectionTests(unittest.TestCase):
    def test_a_well_formed_correction_is_accepted(self):
        old = rev(state="FROZEN", revision_id="r1", supersedes=None)
        new = rev(state="DRAFT", revision_id="r2", supersedes="r1")
        self.assertEqual(CL.check_correction(old, new), [])

    def test_reusing_the_same_revision_id_is_refused_as_a_correction(self):
        old = rev(state="FROZEN", revision_id="r1")
        new = rev(state="DRAFT", revision_id="r1", supersedes="r1")
        problems = CL.check_correction(old, new)
        self.assertTrue(any("revision_id" in p for p in problems), problems)

    def test_correction_not_linked_via_supersedes_is_refused(self):
        old = rev(state="FROZEN", revision_id="r1")
        new = rev(state="DRAFT", revision_id="r2", supersedes=None)
        problems = CL.check_correction(old, new)
        self.assertTrue(any("supersedes" in p for p in problems), problems)

    def test_correction_not_starting_at_draft_is_refused(self):
        old = rev(state="FROZEN", revision_id="r1")
        new = rev(state="CHECKED", revision_id="r2", supersedes="r1")
        problems = CL.check_correction(old, new)
        self.assertTrue(any("DRAFT" in p for p in problems), problems)

    def test_correction_changing_claim_id_is_refused(self):
        old = rev(state="FROZEN", revision_id="r1", claim_id="c1")
        new = rev(state="DRAFT", revision_id="r2", supersedes="r1", claim_id="c2")
        problems = CL.check_correction(old, new)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_old_revision_is_never_mutated_by_making_a_correction(self):
        # check_correction only validates the shape of the new record; it
        # takes no path that would rewrite the old dict passed in, proving
        # "old claim remains auditable" structurally rather than by
        # asserting a docstring.
        old = rev(state="FROZEN", revision_id="r1")
        before = dict(old)
        new = rev(state="DRAFT", revision_id="r2", supersedes="r1")
        CL.check_correction(old, new)
        self.assertEqual(old, before)


class CheckScoringTargetTests(unittest.TestCase):
    def test_scoring_the_exact_decision_used_revision_is_accepted(self):
        decision_used = rev(state="DECISION_USED", revision_id="r1")
        scored = rev(state="SCORED", revision_id="r1")
        self.assertEqual(CL.check_scoring_target(decision_used, scored), [])

    def test_scoring_a_corrected_successor_instead_is_refused(self):
        # The historical revision r1 reached DECISION_USED; r2 is a later
        # correction (supersedes r1). Scoring r2 in r1's place is exactly
        # the rewritten-successor case the roadmap forbids.
        decision_used = rev(state="DECISION_USED", revision_id="r1")
        scored = rev(state="SCORED", revision_id="r2", supersedes="r1")
        problems = CL.check_scoring_target(decision_used, scored)
        self.assertTrue(any("historical revision" in p for p in problems), problems)

    def test_reference_record_not_actually_decision_used_is_refused(self):
        not_decision_used = rev(state="FROZEN", revision_id="r1")
        scored = rev(state="SCORED", revision_id="r1")
        problems = CL.check_scoring_target(not_decision_used, scored)
        self.assertTrue(any("DECISION_USED" in p for p in problems), problems)

    def test_scored_record_not_actually_in_scored_state_is_refused(self):
        decision_used = rev(state="DECISION_USED", revision_id="r1")
        not_scored = rev(state="RESOLVED", revision_id="r1")
        problems = CL.check_scoring_target(decision_used, not_scored)
        self.assertTrue(any("SCORED" in p for p in problems), problems)

    def test_scoring_a_different_claim_id_entirely_is_refused(self):
        decision_used = rev(state="DECISION_USED", revision_id="r1", claim_id="c1")
        scored = rev(state="SCORED", revision_id="r1", claim_id="c9")
        problems = CL.check_scoring_target(decision_used, scored)
        self.assertTrue(any("claim_id" in p for p in problems), problems)

    def test_h2_reused_revision_id_with_different_supersedes_is_refused(self):
        # Opus H2: a forged record can reuse the DECISION_USED record's
        # exact revision_id (so the revision_id equality check alone sees
        # a match) while openly declaring itself a successor via
        # supersedes. This is exactly "scoring resolves the historical
        # claim, not its rewritten successor" bypassed.
        historical = rev(state="DECISION_USED", revision_id="r1", supersedes=None)
        forged = rev(state="SCORED", revision_id="r1", supersedes="r2")
        problems = CL.check_scoring_target(historical, forged)
        self.assertTrue(any("supersedes" in p for p in problems), problems)


class CheckCorrectionRevisionIdHistoryTests(unittest.TestCase):
    def test_h2_reusing_a_revision_id_from_earlier_in_the_history_is_refused(self):
        # Opus H2 related: check_correction only ever compared new's
        # revision_id against `old`'s -- a revision_id reused from
        # further back in the same claim's history (not the immediate
        # predecessor) passed silently. seen_revision_ids closes that.
        old = rev(state="FROZEN", revision_id="r2", supersedes="r1")
        new = rev(state="DRAFT", revision_id="r1", supersedes="r2")
        problems = CL.check_correction(old, new, seen_revision_ids={"r1", "r2"})
        self.assertTrue(any("already been used" in p for p in problems), problems)

    def test_a_genuinely_new_revision_id_is_still_accepted_with_history_given(self):
        old = rev(state="FROZEN", revision_id="r2", supersedes="r1")
        new = rev(state="DRAFT", revision_id="r3", supersedes="r2")
        problems = CL.check_correction(old, new, seen_revision_ids={"r1", "r2"})
        self.assertEqual(problems, [])

    def test_no_history_given_falls_back_to_the_immediate_predecessor_check_only(self):
        # Backward-compatible default: omitting seen_revision_ids does not
        # newly refuse a correction check_correction previously accepted.
        old = rev(state="FROZEN", revision_id="r1", supersedes=None)
        new = rev(state="DRAFT", revision_id="r2", supersedes="r1")
        self.assertEqual(CL.check_correction(old, new), [])


if __name__ == "__main__":
    unittest.main()
