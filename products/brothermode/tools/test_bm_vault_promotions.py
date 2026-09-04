#!/usr/bin/env python3
"""Calibration for tools/bm_vault_promotions.py, the write side of D12.

The property under test is the write-side half of the row's own observable:
recording a legal move actually lands it with who and when, an illegal move
is refused and nothing is written, and a whole-vault census counts every
bucket without ever silently reading zero. No em or en dashes anywhere in
this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_lifecycle as lc         # noqa: E402
import bm_vault_promotions as promo     # noqa: E402

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


def note(promotion=None, by=None, at=None, no_frontmatter=False, author=None):
    if no_frontmatter:
        return "# a note with no frontmatter at all\n"
    lines = ["---", "type: finding", "status: standing"]
    if author:
        lines.append("author: %s" % author)
    if promotion:
        lines.append("promotion: %s" % promotion)
    if by:
        lines.append("promoted_by: %s" % by)
    if at:
        lines.append("promoted_at: %s" % at)
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-promotions-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _text(self, name):
        with open(os.path.join(self.vault, name), encoding="utf-8") as fh:
            return fh.read()


class ALegalTransitionIsRecorded(VaultFixture):
    def test_candidate_to_validated_is_recorded_with_who_and_when(self):
        self._write("a.md", note("candidate", author="agent-session"))
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 0)
        state, record, problems = lc.read_promotion(self._text("a.md"))
        self.assertEqual(state, "validated")
        self.assertEqual(record["promoted_by"], "khalil")
        self.assertEqual(record["promoted_at"], "2026-08-30")
        self.assertEqual(problems, [])

    def test_dry_run_writes_nothing(self):
        original = note("candidate", author="agent-session")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                "2026-08-30", apply_changes=False)
        self.assertEqual(rc, 0)
        self.assertEqual(self._text("a.md"), original)

    def test_legacy_bootstraps_to_candidate_only(self):
        self._write("a.md", note())
        rc = promo.cmd_promote(self.vault, "a.md", "candidate", None,
                                None, apply_changes=True)
        self.assertEqual(rc, 0)
        state, _r, _p = lc.read_promotion(self._text("a.md"))
        self.assertEqual(state, "candidate")


class AnIllegalTransitionIsRefusedAndNothingIsWritten(VaultFixture):
    def test_candidate_to_canonical_is_refused(self):
        original = note("candidate")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "canonical", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._text("a.md"), original, "a refused move must not write")

    def test_legacy_straight_to_validated_is_refused(self):
        original = note()
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._text("a.md"), original)

    def test_nothing_leaves_a_terminal_rejection(self):
        original = note("rejected", by="khalil", at="2026-08-29")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "candidate", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._text("a.md"), original)

    def test_promoting_without_by_is_refused_before_any_write(self):
        original = note("candidate")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "validated", None,
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 2)
        self.assertEqual(self._text("a.md"), original)


class CALIBRATION_the_guard_actually_guards(VaultFixture):
    """Flip legal_move to accept everything, watch the illegal-move test
    fail, then restore. Proves the refusal test is exercising the guard and
    not a tautology. Purges __pycache__ so the swap is not served stale."""

    def test_breaking_legal_move_makes_the_illegal_jump_pass_undetected(self):
        original = note("candidate", author="agent-session")
        self._write("a.md", original)
        real = promo.legal_move
        promo.legal_move = lambda old, new: True
        try:
            rc = promo.cmd_promote(self.vault, "a.md", "canonical", "khalil",
                                    "2026-08-30", apply_changes=True)
            self.assertEqual(rc, 0, "with the guard broken the illegal jump "
                              "must slip through, proving the real test bites")
            self.assertNotEqual(self._text("a.md"), original)
        finally:
            promo.legal_move = real


class TheWriteIsAtomicInShape(VaultFixture):
    """open(path, "w") truncates in place with no lock, on a corpus other
    sessions read concurrently: a crash mid-write would leave a truncated
    note. The fix writes a temp file in the same directory then os.replace()s
    it over the target. Prove the shape by making os.replace raise after the
    temp file is already written, and check the original survived whole."""

    def test_a_crash_between_temp_write_and_replace_leaves_the_original_whole(self):
        original = note("candidate", author="agent-session")
        self._write("a.md", original)
        real_replace = promo.os.replace

        def _boom(_src, _dst):
            raise OSError("simulated crash between temp write and replace")

        promo.os.replace = _boom
        try:
            with self.assertRaises(OSError):
                promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                   "2026-08-30", apply_changes=True)
        finally:
            promo.os.replace = real_replace
        self.assertEqual(self._text("a.md"), original,
                          "the original must be untouched, never truncated")
        leftovers = [f for f in os.listdir(self.vault) if f.startswith(".bm-promote-")]
        self.assertEqual(leftovers, [], "a crashed write must not leave temp litter behind")


class CALIBRATION_the_atomic_write_actually_matters(VaultFixture):
    """Swap _atomic_write for the old open(path, "w") in-place write (which
    truncates BEFORE it can fail) and watch the file end up NOT equal to the
    original on the very same simulated crash. Proves the real test above
    is exercising the fix and is not a tautology."""

    def test_the_naive_in_place_write_truncates_before_it_can_fail(self):
        original = note("candidate", author="agent-session")
        self._write("a.md", original)

        def _naive_write(path, text):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
                raise OSError("simulated crash mid-write")

        real_atomic = promo._atomic_write
        promo._atomic_write = _naive_write
        try:
            with self.assertRaises(OSError):
                promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                   "2026-08-30", apply_changes=True)
        finally:
            promo._atomic_write = real_atomic
        self.assertNotEqual(self._text("a.md"), original,
                             "with the naive write the crash lands after truncation, "
                             "which is exactly the defect the atomic write fixes")


class PromotingToTheStateAlreadyHeldIsANoOp(VaultFixture):
    def test_reapplying_the_same_state_is_a_success_that_writes_nothing(self):
        original = note("validated", by="khalil", at="2026-08-29")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self._text("a.md"), original,
                          "a same-state retry must not rewrite the recorded date")

    def test_reapplying_needs_no_by(self):
        self._write("a.md", note("candidate"))
        rc = promo.cmd_promote(self.vault, "a.md", "candidate", None, None,
                                apply_changes=True)
        self.assertEqual(rc, 0)

    def test_a_genuinely_illegal_jump_still_refuses(self):
        original = note("candidate")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "canonical", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._text("a.md"), original)


class CALIBRATION_the_idempotency_no_op_actually_matters(VaultFixture):
    """Without the no-op branch, cmd_promote falls through to legal_move for a
    same-state pair, which is not in the contract's LEGAL set, so a retried
    script run would be REFUSED (rc 1) instead of a clean no-op success. Proves
    the real test above is exercising the fix rather than a tautology."""

    def test_without_the_no_op_branch_a_same_state_retry_would_be_refused(self):
        self.assertFalse(promo.legal_move("validated", "validated"),
                          "the contract itself has no same-state move, which is exactly "
                          "why cmd_promote needs its own early no-op branch")


class StateReportsLegacyAndReal(VaultFixture):
    def test_legacy_default(self):
        self._write("a.md", note())
        rc = promo.cmd_state(self.vault, "a.md")
        self.assertEqual(rc, 0)

    def test_unresolved_ident_is_NO_DATA(self):
        rc = promo.cmd_state(self.vault, "no-such-note.md")
        self.assertEqual(rc, 2)


class CheckDelegatesToTheContractsOwnCensus(VaultFixture):
    def test_check_matches_the_contract_module_directly(self):
        self._write("legacy.md", note())
        self._write("cand.md", note("candidate"))
        self._write("canon.md", note("canonical", by="khalil", at="2026-08-29"))
        self.assertEqual(promo.cmd_check(self.vault), lc.cmd_check(self.vault))

    def test_check_never_writes(self):
        self._write("legacy.md", note())
        before = self._text("legacy.md")
        promo.cmd_check(self.vault)
        self.assertEqual(self._text("legacy.md"), before)


class SeparationOfDutiesReachesCmdPromote(VaultFixture):
    """V14: bm_vault_lifecycle.check_separation_of_duties exists and is
    enforced by default, but nothing called it from the write side until now.
    Wired here, in cmd_promote, before any frontmatter is touched."""

    def test_the_author_cannot_approve_their_own_candidate(self):
        original = note("candidate", author="agent-session")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "agent-session",
                                "2026-09-02", apply_changes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._text("a.md"), original,
                          "a refused approval must not write")

    def test_a_different_approver_succeeds(self):
        self._write("a.md", note("candidate", author="agent-session"))
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                "2026-09-02", apply_changes=True)
        self.assertEqual(rc, 0)
        state, record, problems = lc.read_promotion(self._text("a.md"))
        self.assertEqual(state, "validated")
        self.assertEqual(record["promoted_by"], "khalil")
        self.assertEqual(problems, [])

    def test_no_author_of_record_is_refused_fail_closed(self):
        original = note("candidate")
        self._write("a.md", original)
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "khalil",
                                "2026-09-02", apply_changes=True)
        self.assertEqual(rc, 1, "a note with no author must never be silently "
                          "allowed through")
        self.assertEqual(self._text("a.md"), original)

    def test_comparison_is_casefold_and_strip_via_the_shared_contract(self):
        self._write("a.md", note("candidate", author=" Agent-Session "))
        rc = promo.cmd_promote(self.vault, "a.md", "validated", "agent-session",
                                "2026-09-02", apply_changes=True)
        self.assertEqual(rc, 1)


class CALIBRATION_the_sod_wiring_actually_bites(VaultFixture):
    """Flip lc.check_separation_of_duties to always allow, watch the
    same-author refusal disappear, then restore. Proves the refusal test
    above is exercising the real wiring, not a tautology."""

    def test_breaking_the_check_lets_the_author_approve_their_own_candidate(self):
        self._write("a.md", note("candidate", author="agent-session"))
        real = lc.check_separation_of_duties
        lc.check_separation_of_duties = lambda author, approver, enforce=None: None
        try:
            rc = promo.cmd_promote(self.vault, "a.md", "validated", "agent-session",
                                    "2026-09-02", apply_changes=True)
            self.assertEqual(rc, 0, "with the check broken the self-approval must "
                              "slip through, proving the real test bites")
        finally:
            lc.check_separation_of_duties = real


if __name__ == "__main__":
    unittest.main()
