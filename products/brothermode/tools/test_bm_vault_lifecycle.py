#!/usr/bin/env python3
"""Calibration for tools/bm_vault_lifecycle.py, benchmark row D12.

The property under test is the row's own observable: a candidate is provably
excluded from canonical until promoted, and promotion is recorded with who and
when. The two guards are the point: the candidate-to-canonical jump must be
illegal, and a state above candidate with no record must be a finding, because
both are auto-promotion wearing a legal-looking state.

No em or en dashes anywhere in this file.

VB3-06 EXTENSION. The new property under test is the row's own observable:
the candidate-to-canonical jump stays blocked with the three new states in
play; an author-approves attempt is refused under a separation-of-duties
policy and legal without one; mutating any covered field of an approval
record invalidates its hash; and a validated/canonical state reads expired
past its horizon, with revalidation modeled as the legal expired ->
under_review move, never an edit of the old record.
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_lifecycle as lc  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def note(promotion=None, by=None, at=None):
    lines = ["---", "type: finding", "status: standing"]
    if promotion:
        lines.append("promotion: %s" % promotion)
    if by:
        lines.append("promoted_by: %s" % by)
    if at:
        lines.append("promoted_at: %s" % at)
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


class TheRowsOwnObservable(unittest.TestCase):
    def test_a_candidate_is_never_canonical(self):
        state, _r, problems = lc.read_promotion(note("candidate"))
        self.assertFalse(lc.counts_as_canonical(state, problems))

    def test_a_recorded_canonical_counts(self):
        state, _r, problems = lc.read_promotion(
            note("canonical", by="khalil", at="2026-08-29"))
        self.assertEqual(problems, [])
        self.assertTrue(lc.counts_as_canonical(state, problems))

    def test_a_canonical_with_NO_record_does_not_count(self):
        """Auto-promotion wearing a legal-looking state."""
        state, _r, problems = lc.read_promotion(note("canonical"))
        self.assertTrue(problems)
        self.assertFalse(lc.counts_as_canonical(state, problems))


class TheLegalMovesAndOnlyThose(unittest.TestCase):
    def test_the_two_forward_moves_and_two_rejections_are_legal(self):
        for old, new in (("candidate", "validated"), ("validated", "canonical"),
                         ("candidate", "rejected"), ("validated", "rejected")):
            self.assertTrue(lc.legal_transition(old, new), (old, new))

    def test_the_candidate_to_canonical_jump_is_illegal(self):
        """Skipping validation is the one shortcut that makes canonical
        meaningless."""
        self.assertFalse(lc.legal_transition("candidate", "canonical"))

    def test_nothing_leaves_a_terminal_rejection(self):
        for new in ("candidate", "validated", "canonical"):
            self.assertFalse(lc.legal_transition("rejected", new))


class ReadingIsHonest(unittest.TestCase):
    def test_absent_is_legacy_not_any_real_state(self):
        state, _r, problems = lc.read_promotion(note())
        self.assertEqual(state, "legacy")
        self.assertEqual(problems, [])
        self.assertFalse(lc.counts_as_canonical(state, problems))

    def test_an_unknown_value_is_a_finding_not_a_state(self):
        state, _r, problems = lc.read_promotion(note("blessed"))
        self.assertIsNone(state)
        self.assertIn("blessed", problems[0])

    def test_a_garbage_promoted_at_is_a_finding(self):
        _s, _r, problems = lc.read_promotion(note("validated", by="khalil", at="soonish"))
        self.assertTrue(any("not a date" in p for p in problems))


class TheCheckReadsARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-lifecycle-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_an_unrecorded_validated_is_reported_and_exits_1(self):
        self._write("ok.md", note("validated", by="khalil", at="2026-08-29"))
        self._write("bare.md", note("validated"))
        self.assertEqual(lc.cmd_check(self.vault), 1)

    def test_legacy_plus_clean_states_exit_0(self):
        self._write("legacy.md", note())
        self._write("cand.md", note("candidate"))
        self._write("canon.md", note("canonical", by="khalil", at="2026-08-29"))
        self.assertEqual(lc.cmd_check(self.vault), 0)



class VB306NewStatesJoinTheMachine(unittest.TestCase):
    def test_the_candidate_to_canonical_jump_still_stays_blocked(self):
        """The one shortcut VB3-06 must not reopen, whatever else joins the
        machine."""
        self.assertFalse(lc.legal_transition("candidate", "canonical"))

    def test_the_new_forward_moves_are_legal(self):
        for old, new in (("candidate", "under_review"),
                          ("under_review", "validated"),
                          ("under_review", "rejected"),
                          ("validated", "expired"),
                          ("canonical", "revoked"),
                          ("canonical", "expired"),
                          ("expired", "under_review")):
            self.assertTrue(lc.legal_transition(old, new), (old, new))

    def test_under_review_cannot_jump_straight_to_canonical(self):
        """Skipping validated the same way candidate cannot."""
        self.assertFalse(lc.legal_transition("under_review", "canonical"))

    def test_revoked_and_expired_are_terminal_for_now(self):
        for old in ("revoked",):
            for new in ("candidate", "validated", "canonical", "under_review"):
                self.assertFalse(lc.legal_transition(old, new), (old, new))

    def test_new_states_above_candidate_still_need_a_recorded_promotion(self):
        """Every state but candidate goes through the same promoted_by/
        promoted_at gate; a bare declaration is a finding, not a state."""
        for state in ("under_review", "revoked", "expired"):
            _s, _r, problems = lc.read_promotion(note(state))
            self.assertTrue(problems, state)
            self.assertIn("without", problems[0])

    def test_new_states_above_candidate_recorded_are_clean(self):
        for state in ("under_review", "revoked", "expired"):
            _s, _r, problems = lc.read_promotion(
                note(state, by="khalil", at="2026-08-29"))
            self.assertEqual(problems, [], state)


class VB306ApprovalRecordsAreHashBound(unittest.TestCase):
    def _approval(self):
        return lc.make_approval("khalil", "steward", "matches its evidence",
                                 "policy-v1", "the note's own content")

    def test_a_fresh_approval_verifies(self):
        self.assertTrue(lc.verify_approval(self._approval()))

    def test_mutating_any_covered_field_invalidates_the_hash(self):
        """Driven backwards: flip each covered field one at a time and
        confirm each flip alone is caught."""
        for field in lc.APPROVAL_HASH_FIELDS:
            record = self._approval()
            record[field] = record[field] + "-tampered"
            self.assertFalse(lc.verify_approval(record), field)

    def test_mutating_the_record_hash_itself_invalidates_it(self):
        record = self._approval()
        record["record_hash"] = "0" * 64
        self.assertFalse(lc.verify_approval(record))

    def test_a_record_missing_its_hash_never_verifies(self):
        record = self._approval()
        del record["record_hash"]
        self.assertFalse(lc.verify_approval(record))

    def test_garbage_input_never_raises(self):
        self.assertFalse(lc.verify_approval(None))
        self.assertFalse(lc.verify_approval("not a dict"))
        self.assertFalse(lc.verify_approval({}))

    def test_artifact_hash_changes_when_the_note_content_changes(self):
        original = lc.make_approval("khalil", "steward", "ok", "policy-v1",
                                     "version one")
        edited = lc.make_approval("khalil", "steward", "ok", "policy-v1",
                                   "version two")
        self.assertNotEqual(original["artifact_hash"], edited["artifact_hash"])


class VB306SeparationOfDuties(unittest.TestCase):
    def test_author_approves_refused_under_the_policy(self):
        refusal = lc.check_separation_of_duties("khalil", "khalil", enforce=True)
        self.assertIsNotNone(refusal)
        self.assertIn("separation of duties", refusal)
        self.assertIn("khalil", refusal)

    def test_author_approves_allowed_without_the_policy(self):
        self.assertIsNone(
            lc.check_separation_of_duties("khalil", "khalil", enforce=False))

    def test_a_different_approver_is_always_allowed(self):
        self.assertIsNone(
            lc.check_separation_of_duties("khalil", "someone_else", enforce=True))

    def test_the_default_policy_is_on_the_same_actor_is_refused(self):
        self.assertTrue(lc.SEPARATION_OF_DUTIES_ENFORCED)
        self.assertIsNotNone(lc.check_separation_of_duties("khalil", "khalil"))

    def test_the_same_actor_with_no_enforce_argument_is_refused_by_default(self):
        refusal = lc.check_separation_of_duties("x", "x")
        self.assertIsNotNone(refusal)
        self.assertIn("separation of duties", refusal)

    def test_comparison_is_casefold_and_strip(self):
        refusal = lc.check_separation_of_duties(" Khalil", "khalil ", enforce=True)
        self.assertIsNotNone(refusal)

    def test_an_unknown_identity_on_either_side_is_never_a_match(self):
        self.assertIsNone(lc.check_separation_of_duties(None, "khalil", enforce=True))
        self.assertIsNone(lc.check_separation_of_duties("khalil", None, enforce=True))
        self.assertIsNone(lc.check_separation_of_duties("", "", enforce=True))


class VB306ExpiryAndRevalidation(unittest.TestCase):
    def test_a_fresh_validated_state_is_not_expired(self):
        today = datetime.date(2026, 8, 30)
        promoted = datetime.date(2026, 8, 1)
        self.assertEqual(
            lc.effective_state("validated", promoted, today=today), "validated")

    def test_flips_exactly_at_the_horizon_boundary(self):
        promoted = datetime.date(2026, 1, 1)
        on_horizon = promoted + datetime.timedelta(days=lc.EXPIRY_HORIZON_DAYS)
        past_horizon = on_horizon + datetime.timedelta(days=1)
        self.assertEqual(
            lc.effective_state("canonical", promoted, today=on_horizon), "canonical")
        self.assertEqual(
            lc.effective_state("canonical", promoted, today=past_horizon), "expired")

    def test_an_iso_string_promoted_at_is_accepted(self):
        past_horizon = datetime.date(2026, 1, 1) + datetime.timedelta(
            days=lc.EXPIRY_HORIZON_DAYS + 1)
        self.assertEqual(
            lc.effective_state("validated", "2026-01-01", today=past_horizon),
            "expired")

    def test_a_missing_promoted_at_is_never_read_as_expired(self):
        far_future = datetime.date(2099, 1, 1)
        self.assertEqual(
            lc.effective_state("validated", None, today=far_future), "validated")

    def test_a_malformed_promoted_at_is_never_read_as_expired(self):
        far_future = datetime.date(2099, 1, 1)
        self.assertEqual(
            lc.effective_state("canonical", "soonish", today=far_future), "canonical")

    def test_candidate_and_rejected_never_expire(self):
        ancient = datetime.date(2000, 1, 1)
        far_future = datetime.date(2099, 1, 1)
        self.assertEqual(
            lc.effective_state("candidate", ancient, today=far_future), "candidate")
        self.assertEqual(
            lc.effective_state("rejected", ancient, today=far_future), "rejected")

    def test_revalidation_is_the_legal_expired_to_under_review_move(self):
        """Revalidation is a recorded transition, never an edit in place: the
        contract models it as exactly the move the machine already legalizes."""
        self.assertTrue(lc.legal_transition("expired", "under_review"))
        self.assertFalse(lc.legal_transition("expired", "validated"))
        self.assertFalse(lc.legal_transition("expired", "canonical"))


class VB306NoDataWhereStoresAreAbsent(unittest.TestCase):
    def test_main_reports_no_data_for_an_unreadable_vault(self):
        self.assertEqual(
            lc.main(["check", "--vault", "/no/such/vault/anywhere"]), 2)

    def test_main_reports_no_data_for_no_vault_at_all(self):
        self.assertEqual(lc.main(["check", "--vault", ""]), 2)


if __name__ == "__main__":
    unittest.main()
