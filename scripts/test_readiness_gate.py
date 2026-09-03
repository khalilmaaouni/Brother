"""What the readiness gate must keep true.

Driven backwards, same discipline as test_parity_gate.py: a fixture evidence
file flips an item to PASS, removing it flips the item back to NO-DATA, and
the gate's own exit code is nonzero while any critical item is unproven and
zero only once every critical item passes.
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readiness_gate as RG  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silent_main(argv):
    """RG.main() prints a full report; swallow it so a verbose test run's
    tail is the unittest summary, never a gate report a test happened to
    print last (stdout is block-buffered when redirected to a file, so an
    unswallowed print can land after unittest's own OK/FAILED line)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return RG.main(argv)


def _write_script(path, exit_code=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import sys\nsys.exit(%d)\n" % exit_code)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class AnItemIsGrantedByEvidenceNeverByAssertion(unittest.TestCase):
    def test_a_missing_suite_file_is_no_data(self):
        d = tempfile.mkdtemp()
        verdict, evidence = RG._check_suite(d, os.path.join("scripts", "x.py"))
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("does not exist", evidence)

    def test_an_existing_passing_suite_flips_to_pass(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("scripts", "x.py")
        _write_script(os.path.join(d, rel), exit_code=0)
        verdict, _evidence = RG._check_suite(d, rel)
        self.assertEqual(verdict, RG.PASS)

    def test_removing_the_evidence_file_flips_it_back_to_no_data(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("scripts", "x.py")
        path = os.path.join(d, rel)
        _write_script(path, exit_code=0)
        self.assertEqual(RG._check_suite(d, rel)[0], RG.PASS)
        os.remove(path)
        self.assertEqual(RG._check_suite(d, rel)[0], RG.NODATA)

    def test_a_failing_suite_is_fail_not_no_data(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("scripts", "x.py")
        _write_script(os.path.join(d, rel), exit_code=1)
        self.assertEqual(RG._check_suite(d, rel)[0], RG.FAIL)

    def test_a_missing_record_is_no_data(self):
        d = tempfile.mkdtemp()
        verdict, evidence = RG._check_record(d, os.path.join("docs", "r.json"))
        self.assertEqual(verdict, RG.NODATA)
        self.assertIn("does not exist", evidence)

    def test_a_record_with_passed_true_is_pass(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("docs", "r.json")
        os.makedirs(os.path.join(d, "docs"))
        with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            json.dump({"passed": True}, fh)
        self.assertEqual(RG._check_record(d, rel)[0], RG.PASS)

    def test_a_record_with_passed_false_is_fail(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("docs", "r.json")
        os.makedirs(os.path.join(d, "docs"))
        with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            json.dump({"passed": False}, fh)
        self.assertEqual(RG._check_record(d, rel)[0], RG.FAIL)

    def test_a_record_with_no_passed_field_is_no_data(self):
        d = tempfile.mkdtemp()
        rel = os.path.join("docs", "r.json")
        os.makedirs(os.path.join(d, "docs"))
        with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            json.dump({"something_else": True}, fh)
        self.assertEqual(RG._check_record(d, rel)[0], RG.NODATA)

    def test_a_no_data_verdict_names_the_blocking_wbs_row(self):
        d = tempfile.mkdtemp()
        rows = RG.evaluate(d)
        tenancy = [r for r in rows if r["id"] == "tenancy-leakage-zero"][0]
        self.assertEqual(tenancy["verdict"], RG.NODATA)
        self.assertIn("VB3-03", tenancy["evidence"])


class NoDataIsNeverAPass(unittest.TestCase):
    def test_no_data_blocks_a_critical_item_exactly_like_fail(self):
        rows = [{"id": "x", "title": "X", "critical": True, "verdict": RG.NODATA,
                 "evidence": ""}]
        self.assertEqual(len(RG.blocking(rows)), 1)

    def test_no_data_does_not_block_a_noncritical_item(self):
        rows = [{"id": "x", "title": "X", "critical": False, "verdict": RG.NODATA,
                 "evidence": ""}]
        self.assertEqual(RG.blocking(rows), [])

    def test_a_noncritical_fail_is_caught_but_a_noncritical_no_data_is_not(self):
        """The gap six acceptance reviewers all caught: a definite FAIL is a
        proven break and worse than a NO-DATA, so a non-critical FAIL must be
        surfaced even though it is off the critical path, while a non-critical
        NO-DATA stays a non-blocking honest unknown."""
        rows = [
            {"id": "f", "title": "F", "critical": False, "verdict": RG.FAIL, "evidence": ""},
            {"id": "n", "title": "N", "critical": False, "verdict": RG.NODATA, "evidence": ""},
            {"id": "p", "title": "P", "critical": False, "verdict": RG.PASS, "evidence": ""},
        ]
        caught = RG.noncritical_fails(rows)
        self.assertEqual([r["id"] for r in caught], ["f"])


class TheGateExitsNonzeroWhileACriticalItemIsUnproven(unittest.TestCase):
    """Driven backwards on a full fixture root, same shape as production:
    every critical item's evidence path present and passing gives exit 0;
    removing any one of them gives a nonzero exit. Non-critical items are
    left absent (NO-DATA) throughout, and must never affect the exit code."""

    def _all_critical_pass_root(self):
        d = tempfile.mkdtemp()
        _write_script(os.path.join(d, "scripts", "test_make_benchmark_bundle.py"), 0)
        _write_script(os.path.join(d, "scripts", "test_tenancy_isolation.py"), 0)
        _write_script(os.path.join(d, "scripts", "test_policy_fail_closed.py"), 0)
        # V6/M4: japanese-threshold is now critical (see readiness_gate.py's
        # ITEMS comment), so a fixture claiming every critical item passes
        # must give it passing evidence too, not leave it absent.
        _write_script(os.path.join(d, "scripts", "test_japanese_threshold.py"), 0)
        os.makedirs(os.path.join(d, "docs", "plan"), exist_ok=True)
        with open(os.path.join(d, "docs", "plan", "RESTORE-DRILL-ENTERPRISE-RESULT.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"passed": True}, fh)
        return d

    def test_exit_zero_once_every_critical_item_passes(self):
        d = self._all_critical_pass_root()
        self.assertEqual(_silent_main(["--root", d]), 0)

    def test_exit_nonzero_when_one_critical_item_is_missing(self):
        d = self._all_critical_pass_root()
        os.remove(os.path.join(d, "docs", "plan", "RESTORE-DRILL-ENTERPRISE-RESULT.json"))
        self.assertEqual(_silent_main(["--root", d]), 1)

    def test_exit_nonzero_when_one_critical_item_fails(self):
        d = self._all_critical_pass_root()
        _write_script(os.path.join(d, "scripts", "test_policy_fail_closed.py"), 1)
        self.assertEqual(_silent_main(["--root", d]), 1)

    def test_a_missing_noncritical_item_never_blocks_a_clean_run(self):
        """reproducible-release-artifact stays absent in every fixture above
        (japanese-threshold turned critical at V6/M4 and is now given passing
        evidence by _all_critical_pass_root) and the gate still opens: proves
        criticality, not merely completeness, decides the exit code."""
        d = self._all_critical_pass_root()
        rows = RG.evaluate(d)
        noncritical = [r for r in rows if not r["critical"]]
        self.assertTrue(all(r["verdict"] == RG.NODATA for r in noncritical))
        self.assertEqual(_silent_main(["--root", d]), 0)

    def test_a_missing_critical_japanese_item_still_blocks(self):
        """V6/M4 contract: japanese-threshold flipped critical (see
        readiness_gate.py's ITEMS comment). Removing its evidence file must
        NOT read as a shrug-worthy NO-DATA off the critical path; an unproven
        critical item blocks READY exactly like a FAIL, same as any other
        critical row."""
        d = self._all_critical_pass_root()
        os.remove(os.path.join(d, "scripts", "test_japanese_threshold.py"))
        self.assertEqual(_silent_main(["--root", d]), 1)

    def test_a_no_data_noncritical_item_still_opens_the_gate(self):
        """The other side of the same coin: a non-critical NO-DATA (the suite
        file absent) must NOT block, so the stricter rule did not over-reach
        into treating unknowns as breaks."""
        d = self._all_critical_pass_root()
        # reproducible-release-artifact stays absent -> NO-DATA
        self.assertEqual(_silent_main(["--root", d]), 0)

    def _write_expectations(self, d, name, review_by):
        p = os.path.join(d, "docs", "plan", "BATTERY-EXPECTATIONS.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"checks": {name: {"class": "known_no_data",
                                         "review_by": review_by}}}, fh)

    def test_an_expired_declared_exception_forces_not_ready(self):
        """Red-team item 6, end to end: a declared exception past its review_by
        must block the gate, not just the consolidated battery verdict. A
        failure cemetery is not allowed."""
        d = self._all_critical_pass_root()
        self._write_expectations(d, "stale-x", "2026-01-01")
        self.assertEqual(_silent_main(["--root", d, "--today", "2026-06-01"]), 1)

    def test_a_future_dated_exception_does_not_block(self):
        d = self._all_critical_pass_root()
        self._write_expectations(d, "fresh-x", "2027-01-01")
        self.assertEqual(_silent_main(["--root", d, "--today", "2026-06-01"]), 0)

    def test_on_the_real_tree_today_the_gate_is_ready(self):
        """Calibration against the actual repository, updated when B3 and B4
        landed scripts/test_tenancy_isolation.py and
        scripts/test_policy_fail_closed.py (the Brother-side black-box
        proofs of VB3-03 and VB3-04, run against a vendored, frozen copy of
        the BrotherModeUp modules that merged those rows -- see
        scripts/fixtures/bmu_vault_seam/PROVENANCE.md) and the restore
        drill already recorded a pass. Every currently-defined critical
        item now proves itself; the gate must read READY. A gate that
        still reported NOT READY on this tree, with all three of those
        already landed, would be lying in the other direction."""
        self.assertEqual(_silent_main(["--root", REPO_ROOT]), 0)


class TheFifteenQuestionReferenceIsHonestNotInvented(unittest.TestCase):
    """The review's fifteen definition-of-done questions do not exist anywhere
    in this repository (docs/plan, docs/plan/research, or git history -- see
    readiness_gate.py's module docstring for the search log). Per this
    estate's own rule against fabricating evidence, this page must NOT claim
    fifteen numbered questions it cannot quote from a real source; it must
    name the gap. This test is the drift guard on that honesty, not on a
    fabricated count: it fails if the page starts asserting fifteen numbered
    questions again without a real source landing first."""

    PAGE = os.path.join(REPO_ROOT, "docs", "plan", "FIFTEEN-QUESTION-PR-BAR.md")

    def test_the_reference_page_exists(self):
        self.assertTrue(os.path.isfile(self.PAGE), self.PAGE)

    def test_it_names_the_row_of_record(self):
        with open(self.PAGE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("VB3-12", text)
        self.assertIn("VAULT-WBS-V2-2026-08-29.json", text)

    def test_it_reports_no_data_rather_than_inventing_fifteen_questions(self):
        with open(self.PAGE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(RG.NODATA, text)
        # A genuinely-sourced fifteen-question list would number them 1-15.
        # None of those markers should appear paired with question text here
        # today, because no such list was found.
        for n in range(1, 16):
            self.assertNotIn("%d. " % n, text.split("### What was searched")[0])


if __name__ == "__main__":
    unittest.main()
