"""ORCH-26 calibration.

THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a linter whose test suite only
proves its fields exist and its functions can be called, never that it
actually tells a good brief from a bad one. Two adversaries predicted this
row becomes exactly that kind of paperwork. So the suite below is built
around one question: given a brief a presence-only check would wave through
(every field present, right type, non-empty), does this linter still catch
what makes it bad? Every "bad" test below constructs a brief that a
mechanical "is this key here" check would pass clean.

Also asserted: the demotion itself. main() must exit 0 whenever it could
read the plan, findings or not, because a blocking exit code here is exactly
the design the debate rejected.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brief_lint as B  # noqa: E402
import orchestrator_invariants as INV  # noqa: E402

TASK_CLASSES = INV.TASK_CLASSES
EVIDENCE_OBLIGATIONS = INV.EVIDENCE_OBLIGATIONS
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_WBS = os.path.join(REPO_ROOT, "docs", "plan", "ORCH-1020-WBS.json")


class LintBriefDiscriminates(unittest.TestCase):
    def test_good_brief_every_field_present_and_consistent_has_no_findings(self):
        brief = {
            "id": "u1",
            "owns": ["scripts/foo.py"],
            "done_check": "python3 scripts/test_foo.py -v",
            "task_class": "implementation",
            "evidence_obligation": "REQUIRED_FOR_MERGE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertEqual(findings, [])

    def test_done_check_present_and_nonempty_but_copy_pasted_is_still_caught(self):
        # A presence-only check would pass this: every field exists, is the
        # right type, is non-empty. Only comparing done_check against owns
        # catches that it was copied from a different unit.
        brief = {
            "id": "u2",
            "owns": ["scripts/bar.py"],
            "done_check": "python3 scripts/test_unrelated_module.py -v",
            "task_class": "implementation",
            "evidence_obligation": "REQUIRED_FOR_MERGE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertTrue(findings, "a copy-pasted done_check must be flagged")
        self.assertTrue(any("done_check" in f and "bar.py" in f for f in findings))

    def test_done_check_matches_on_basename_not_only_full_path(self):
        brief = {
            "id": "u3",
            "owns": ["scripts/baz.py"],
            "done_check": "python3 scripts/test_baz.py -v",
            "task_class": "implementation",
            "evidence_obligation": "REQUIRED_FOR_MERGE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertEqual(findings, [], "test_baz.py implies baz.py is exercised")

    def test_empty_owns_is_flagged_even_though_the_key_is_present(self):
        brief = {
            "id": "u4",
            "owns": [],
            "done_check": "python3 scripts/test_something.py -v",
            "task_class": "implementation",
            "evidence_obligation": "REQUIRED_FOR_MERGE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertTrue(any("owns is empty" in f for f in findings))

    def test_missing_owns_key_is_flagged(self):
        brief = {
            "id": "u5",
            "done_check": "python3 scripts/test_something.py -v",
            "task_class": "implementation",
            "evidence_obligation": "REQUIRED_FOR_MERGE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertTrue(any("owns is empty" in f for f in findings))

    def test_unknown_task_class_is_flagged_against_the_real_vocabulary(self):
        brief = {
            "id": "u6",
            "owns": ["scripts/foo.py"],
            "done_check": "python3 scripts/test_foo.py -v",
            "task_class": "not_a_real_class",
            "evidence_obligation": "REQUIRED_FOR_MERGE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertTrue(any("task_class" in f and "not_a_real_class" in f for f in findings))

    def test_unknown_evidence_obligation_is_flagged_against_the_real_vocabulary(self):
        brief = {
            "id": "u7",
            "owns": ["scripts/foo.py"],
            "done_check": "python3 scripts/test_foo.py -v",
            "task_class": "implementation",
            "evidence_obligation": "SOMEDAY_MAYBE",
        }
        findings = B.lint_brief(brief, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertTrue(any("evidence_obligation" in f and "SOMEDAY_MAYBE" in f for f in findings))

    def test_no_allowed_sets_supplied_skips_enum_checks_without_crashing(self):
        brief = {"id": "u8", "owns": ["scripts/foo.py"], "done_check": "scripts/foo.py",
                 "task_class": "whatever", "evidence_obligation": "whatever"}
        findings = B.lint_brief(brief)
        self.assertEqual(findings, [])


class LintWbsCrossBriefCollision(unittest.TestCase):
    def test_two_units_owning_the_same_path_are_both_flagged(self):
        # Each unit alone would pass lint_brief clean; the collision is
        # visible only by comparing the two units against each other.
        wbs = {
            "units": [
                {"id": "A", "owns": ["scripts/shared.py"],
                 "done_check": "python3 scripts/test_shared.py -v",
                 "task_class": "implementation", "evidence_obligation": "REQUIRED_FOR_MERGE"},
                {"id": "B", "owns": ["scripts/shared.py"],
                 "done_check": "python3 scripts/test_shared.py -v",
                 "task_class": "implementation", "evidence_obligation": "REQUIRED_FOR_MERGE"},
            ]
        }
        self.assertEqual(B.lint_brief(wbs["units"][0], TASK_CLASSES, EVIDENCE_OBLIGATIONS), [])
        self.assertEqual(B.lint_brief(wbs["units"][1], TASK_CLASSES, EVIDENCE_OBLIGATIONS), [])
        results = B.lint_wbs(wbs, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertIn("A", results)
        self.assertIn("B", results)
        self.assertTrue(any("shared.py" in f and "'B'" in f for f in results["A"]))
        self.assertTrue(any("shared.py" in f and "'A'" in f for f in results["B"]))

    def test_three_way_collision_names_the_other_two_for_each(self):
        wbs = {"units": [
            {"id": "A", "owns": ["x.py"]}, {"id": "B", "owns": ["x.py"]},
            {"id": "C", "owns": ["x.py"]},
        ]}
        results = B.lint_wbs(wbs)
        collision_a = [f for f in results["A"] if "scope collision" in f][0]
        self.assertIn("B", collision_a)
        self.assertIn("C", collision_a)

    def test_no_collision_when_paths_differ(self):
        wbs = {"units": [
            {"id": "A", "owns": ["a.py"], "done_check": "a.py"},
            {"id": "B", "owns": ["b.py"], "done_check": "b.py"},
        ]}
        results = B.lint_wbs(wbs)
        self.assertEqual(results, {})

    def test_not_a_dict_is_flagged_not_crashed(self):
        results = B.lint_wbs(["not", "a", "dict"])
        self.assertIn("<wbs>", results)

    def test_missing_units_key_is_flagged_not_crashed(self):
        results = B.lint_wbs({"no_units_here": []})
        self.assertIn("<wbs>", results)


class MainIsAdvisoryNeverBlocks(unittest.TestCase):
    def _run_with_plan(self, wbs):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8") as fh:
            json.dump(wbs, fh)
            path = fh.name
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = B.main(["--plan", path])
            return rc, out.getvalue()
        finally:
            os.unlink(path)

    def test_exit_zero_with_real_findings_present(self):
        wbs = {"units": [{"id": "u1", "owns": [],
                          "done_check": "echo done",
                          "task_class": "implementation",
                          "evidence_obligation": "REQUIRED_FOR_MERGE"}]}
        rc, out = self._run_with_plan(wbs)
        self.assertEqual(rc, 0, "advisory: findings must never turn into a nonzero exit")
        self.assertIn("owns is empty", out)

    def test_exit_zero_with_no_findings(self):
        wbs = {"units": [{"id": "u1", "owns": ["scripts/foo.py"],
                          "done_check": "python3 scripts/test_foo.py -v",
                          "task_class": "implementation",
                          "evidence_obligation": "REQUIRED_FOR_MERGE"}]}
        rc, out = self._run_with_plan(wbs)
        self.assertEqual(rc, 0)
        self.assertIn("no findings", out)

    def test_malformed_json_is_no_data_exit_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8") as fh:
            fh.write("{ not valid json")
            path = fh.name
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = B.main(["--plan", path])
            self.assertEqual(rc, 2)
            self.assertIn("NO-DATA", out.getvalue())
        finally:
            os.unlink(path)

    def test_unreadable_file_is_no_data_exit_two(self):
        path = os.path.join(tempfile.gettempdir(), "brief_lint_does_not_exist.json")
        if os.path.exists(path):
            os.unlink(path)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = B.main(["--plan", path])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out.getvalue())


class AgainstTheRealPlan(unittest.TestCase):
    """The deciding property for this row: the command must be EXECUTED and
    CHALLENGED against real input, not rewarded for merely existing. Run the
    linter over this run's own plan file and require it to find something,
    proving the checks fire on live data rather than only on hand-built
    fixtures designed to trip them."""

    def test_linter_finds_real_findings_in_this_runs_own_plan(self):
        if not os.path.isfile(REAL_WBS):
            self.skipTest("ORCH-1020-WBS.json not present in this checkout")
        with open(REAL_WBS, encoding="utf-8") as fh:
            wbs = json.load(fh)
        results = B.lint_wbs(wbs, TASK_CLASSES, EVIDENCE_OBLIGATIONS)
        self.assertTrue(results, "expected at least one real finding in the live plan; "
                                  "zero here means the checks are not firing on real data")

    def test_main_against_the_real_plan_still_exits_zero(self):
        if not os.path.isfile(REAL_WBS):
            self.skipTest("ORCH-1020-WBS.json not present in this checkout")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = B.main(["--plan", REAL_WBS])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
