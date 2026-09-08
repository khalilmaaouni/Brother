#!/usr/bin/env python3
"""Self-test for scripts/fast_path.py (design-P2.md section 4, item 1: the
eligibility table and the escalation marker; items 2-4, the stubbed-run
escalation drive, the receipt-format comparison and the benchmark
extension, are NOT built here on purpose: this lane never touches
brother_run.py, see fast_path.py's own docstring and the P2 report's NOT
DONE line."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fast_path


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True)


def _clean_repo():
    """A fresh git repo, one commit, clean working tree."""
    d = tempfile.mkdtemp(prefix="fast-path-test-")
    _git(["init", "-q"], d)
    _git(["config", "user.email", "test@example.com"], d)
    _git(["config", "user.name", "Test"], d)
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("seed\n")
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "seed"], d)
    return d


def _unit(**kw):
    base = {"id": "U1", "objective": "a small named change",
            "done_check": "false", "owns": ["file.txt"]}
    base.update(kw)
    return base


class EligibilityTable(unittest.TestCase):
    """Twelve unsafe rows (each False with a reason naming the refusing
    classifier) plus two positive rows, driven against real git repos so
    integrate.dirty_paths and the done_check subprocess are exercised for
    real, not mocked."""

    @classmethod
    def setUpClass(cls):
        cls.clean = _clean_repo()
        cls.dirty = _clean_repo()
        with open(os.path.join(cls.dirty, "scratch.txt"), "w") as f:
            f.write("uncommitted\n")
        cls.non_git = tempfile.mkdtemp(prefix="fast-path-nongit-")

    @classmethod
    def tearDownClass(cls):
        for d in (cls.clean, cls.dirty, cls.non_git):
            shutil.rmtree(d, ignore_errors=True)

    def _refused(self, unit, cwd, want_in_reason):
        ok, reason = fast_path.eligible("demo", unit, cwd)
        self.assertFalse(ok, "expected ineligible, got reason=%r" % (reason,))
        self.assertIn(want_in_reason, reason.lower(),
                       "reason %r does not name %r" % (reason, want_in_reason))

    # -- receipt_door.risk_triggers rows -----------------------------------

    def test_auth_wording_refused(self):
        self._refused(_unit(objective="rotate the login password hash"),
                       self.clean, "risk_triggers")

    def test_migration_wording_refused(self):
        self._refused(_unit(objective="backfill the orders table"),
                       self.clean, "risk_triggers")

    def test_money_wording_refused(self):
        self._refused(_unit(objective="process the refund payment"),
                       self.clean, "risk_triggers")

    def test_destructive_done_check_refused(self):
        self._refused(_unit(done_check="rm -rf build/"),
                       self.clean, "risk_triggers")

    def test_public_api_path_refused(self):
        self._refused(_unit(objective="change the public API endpoint"),
                       self.clean, "risk_triggers")

    # -- fast_path's own rules ----------------------------------------------

    def test_three_declared_paths_refused(self):
        self._refused(_unit(owns=["a.txt", "b.txt", "c.txt"]),
                       self.clean, "declared write paths")

    def test_second_unit_dependency_refused(self):
        self._refused(_unit(depends_on=["U0"]),
                       self.clean, "depends")

    def test_empty_done_check_refused(self):
        self._refused(_unit(done_check=""), self.clean, "done_check")

    def test_manifest_write_refused(self):
        self._refused(_unit(owns=["requirements.txt"]),
                       self.clean, "fast_forbidden_paths")

    def test_dirty_tree_refused(self):
        self._refused(_unit(done_check="false"), self.dirty, "dirty tree")

    def test_dirty_paths_none_refused(self):
        self._refused(_unit(done_check="false"), self.non_git,
                       "could not run")

    def test_check_already_passing_refused(self):
        self._refused(_unit(done_check="true"), self.clean,
                       "already passes")

    # -- work_record.check_units row (a twelfth unsafe row: no write scope) -

    def test_no_write_scope_refused(self):
        self._refused(_unit(owns=[]), self.clean, "check_units")

    # -- two positive rows, docs and code, mirroring tiny_task_cost.py ------

    def test_docs_case_eligible(self):
        unit = {"id": "D1", "objective": "add the missing line to the notes file",
                 "done_check": "test -f NOTES.md && grep -q written NOTES.md",
                 "owns": ["NOTES.md"]}
        ok, reason = fast_path.eligible("docs", unit, self.clean)
        self.assertTrue(ok, "docs case refused: %s" % reason)

    def test_code_case_eligible(self):
        d = tempfile.mkdtemp(prefix="fast-path-code-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            with open(os.path.join(d, "widget.py"), "w") as f:
                f.write("def width():\n    return 2\n")
            with open(os.path.join(d, "test_widget.py"), "w") as f:
                f.write("import widget\n"
                        "assert widget.width() == 3, 'not fixed yet'\n"
                        "print('ok')\n")
            _git(["add", "."], d)
            _git(["commit", "-q", "-m", "seed"], d)
            unit = {"id": "C1", "objective": "make the existing test pass",
                     "done_check": "python3 test_widget.py",
                     "owns": ["widget.py"]}
            ok, reason = fast_path.eligible("code", unit, d)
            self.assertTrue(ok, "code case refused: %s" % reason)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class NeverRaises(unittest.TestCase):
    """eligible() on garbage input always returns a (bool, str) tuple."""

    def _safe(self, outcome, unit, cwd):
        result = fast_path.eligible(outcome, unit, cwd)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        ok, reason = result
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)
        self.assertFalse(ok)

    def test_all_none(self):
        self._safe(None, None, None)

    def test_wrong_types(self):
        self._safe(1, 2, 3)

    def test_unit_not_a_dict(self):
        self._safe("x", ["not", "a", "dict"], ".")

    def test_owns_not_a_list(self):
        self._safe("x", {"id": "U1", "done_check": "true", "owns": "a.txt"}, ".")

    def test_nonexistent_cwd(self):
        self._safe("x", {"id": "U1", "done_check": "true", "owns": ["a"]},
                    "/no/such/path/anywhere-xyz")

    def test_done_check_not_a_string(self):
        self._safe("x", {"id": "U1", "done_check": 12345, "owns": ["a"]}, ".")


class Escalation(unittest.TestCase):

    def test_empty_undeclared_is_none(self):
        self.assertIsNone(fast_path.escalation([], "next command"))
        self.assertIsNone(fast_path.escalation(None, "next command"))

    def test_marker_and_paths_and_next_command(self):
        line = fast_path.escalation(["a/b.py", "c/d.py"], "brother_run.py --resume X")
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith(fast_path.ESCALATION_MARKER))
        self.assertIn("a/b.py", line)
        self.assertIn("c/d.py", line)
        self.assertIn("brother_run.py --resume X", line)


if __name__ == "__main__":
    unittest.main()
