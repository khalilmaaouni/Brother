#!/usr/bin/env python3
"""C20.b: BRIEF-BUDGET builds a sub unit's minimum brief exactly as the loop does (build_brief.py then brief_check.py,
from the tree root) and refuses, before any worker is paid, a section the builder's own budget starves. Measured
2026-10-14: CF1.a was WITHHELD on a paid run because scripts/receipt_door.py's unsliceable remainder (23,327 bytes) did
not fit its share of the brief (19,770 bytes), and brief_check B1 and B2 refused the brief.

Entry points under test: plan_lint.lint_plan (the report), plan_lint.main (the CLI the loop intake runs) and
plan_lint.admission (the pool). Every fixture lives in a tempfile directory and HOME is a tempfile directory for every
case (its private names list is written here), so no file of the real home folder is read or written.

Run: python3 -B scripts/loop/test_plan_lint_budget.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import plan_lint  # noqa: E402  (the module under test, beside this file)

RULE = "BRIEF-BUDGET"
BUILT = "Built by: a session builder (effort high) in its own worktree"
# A module whose remainder after the slicer has cut everything it may cut is about 120,000 bytes of imports: more than
# the 110,000 byte ceiling build_brief.shares gives any one file, so no budget can show it.
UNSLICEABLE = "".join("import os as m%05d\n" % i for i in range(6000))


def spec(files, prose=""):
    return "\n".join(["# U", "", "## U.1 do a thing", "", "Files: %s, `scripts/test_x.py` (NEW)" % files, "", BUILT, "",
                      "Done check: `python3 -B scripts/test_x.py`", "", prose, ""])


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c20b_")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "root")
        self.home = os.path.join(tmp.name, "home")
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        os.makedirs(self.home)
        # one fake token no fixture carries: an EMPTY list screens nothing and fails closed (PRIV1, 2026-10-18)
        self.private_names("zz-fixture-private-token\n")
        saved = os.environ.get("HOME")
        self.addCleanup(lambda: os.environ.__setitem__("HOME", saved) if saved is not None else os.environ.pop("HOME", None))
        os.environ["HOME"] = self.home
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")

    def private_names(self, text):
        with open(os.path.join(self.home, ".brothersbe-private-names"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def plan(self, text, owns):
        self.write("docs/plan/specs/U.md", text)
        unit = {"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_x.py", "spec": "docs/plan/specs/U.md",
                "sub_units": ["U.1"], "evidence": "", "owns": list(owns) + ["scripts/test_x.py"]}
        return self.write("plan.json", json.dumps({"units": [unit]})), unit

    def hits(self, findings):
        return [f for f in findings if f["rule"] == RULE]

    def lint(self, path):
        return self.hits(plan_lint.lint_plan(path, root=self.root, ledger=None, env={})["findings"])

    def run_main(self, path):
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out):
                code = plan_lint.main(["--report", path, "--ledger", self.ledger])
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def patch(self, name, value):
        old = getattr(plan_lint, name)
        setattr(plan_lint, name, value)
        self.addCleanup(setattr, plan_lint, name, old)


class Refused(Fixture):
    """REQ-C20-b-01 to 03: each way the builder's own budget starves a section is BLOCKING."""

    def test_a_named_module_that_cannot_fit_its_share_is_blocking(self):
        self.write("scripts/big.py", UNSLICEABLE)
        path, unit = self.plan(spec("`scripts/big.py`"), ["scripts/big.py"])
        hits = self.lint(path)
        self.assertEqual([(f["sub"], f["severity"]) for f in hits], [("U.1", "BLOCKING")], hits)
        self.assertIn("scripts/big.py NOT SHOWN for its share of the brief", hits[0]["text"])
        self.assertIn("(B1)", hits[0]["text"])
        code, out = self.run_main(path)
        self.assertEqual(code, 0, out)
        self.assertIn("LINT BLOCKING U U.1 BRIEF-BUDGET: the loop's brief builder leaves scripts/big.py NOT SHOWN", out)
        _lines, why = plan_lint.admission({"units": [unit]}, unit, "U.1", self.root, {}, ledger=self.ledger)
        self.assertTrue(why.startswith("LINT BRIEF-BUDGET: "), why)

    def test_a_section_too_big_for_the_brief_is_blocking_at_b3(self):
        path, _unit = self.plan(spec("`scripts/absent.py` (NEW)", prose="word " * 40000), ["scripts/absent.py"])
        hits = self.lint(path)
        self.assertEqual([f["severity"] for f in hits], ["BLOCKING"], hits)
        self.assertIn("B3 BUDGET", hits[0]["text"])

    def test_a_section_whose_files_cannot_even_be_named_is_blocking(self):
        self.write("scripts/small.py", "X = 1\n")
        path, _unit = self.plan(spec("`scripts/small.py`", prose="word " * 40000), ["scripts/small.py"])
        hits = self.lint(path)
        self.assertEqual([f["severity"] for f in hits], ["BLOCKING"], hits)
        self.assertIn("cannot all be named within the brief budget", hits[0]["text"])


class Passes(Fixture):
    """REQ-C20-b-04 and 05: a brief that fits is clean, and a refusal that is not about the budget is not this rule's."""

    def test_a_section_whose_files_fit_passes(self):
        self.write("scripts/small.py", "def f():\n    return 1\n")
        path, unit = self.plan(spec("`scripts/small.py`"), ["scripts/small.py"])
        self.assertEqual(self.lint(path), [])
        lines, _why = plan_lint.admission({"units": [unit]}, unit, "U.1", self.root, {}, ledger=self.ledger)
        self.assertFalse([l for l in lines if RULE in l], lines)

    def test_a_file_withheld_for_a_private_term_is_not_a_budget_finding(self):
        self.private_names("zebrafish\n")
        self.write("scripts/small.py", "ZEBRAFISH = 1\n")
        path, _unit = self.plan(spec("`scripts/small.py`"), ["scripts/small.py"])
        self.assertEqual(self.lint(path), [])


class NoData(Fixture):
    """REQ-C20-b-06 and 07: a builder that cannot be found or run reads NO-DATA, never clean."""

    def test_a_missing_brief_builder_reads_no_data(self):
        self.write("scripts/big.py", UNSLICEABLE)
        path, _unit = self.plan(spec("`scripts/big.py`"), ["scripts/big.py"])
        self.patch("_HERE", os.path.join(self.root, "no-loop-here"))
        hits = self.lint(path)
        self.assertEqual([f["severity"] for f in hits], ["NO-DATA"], hits)
        code, out = self.run_main(path)
        self.assertEqual(code, 3, out)
        self.assertIn("LINT NO-DATA U U.1 BRIEF-BUDGET: build_brief.py or brief_check.py is not beside plan_lint.py", out)

    def test_a_builder_that_does_not_finish_reads_no_data(self):
        self.write("scripts/small.py", "X = 1\n")
        path, _unit = self.plan(spec("`scripts/small.py`"), ["scripts/small.py"])
        self.patch("BRIEF_TIMEOUT_S", 0.001)
        hits = self.lint(path)
        self.assertEqual([f["severity"] for f in hits], ["NO-DATA"], hits)
        self.assertIn("TimeoutExpired", hits[0]["text"])

    def test_a_root_that_is_not_a_directory_reads_no_data(self):
        _path, unit = self.plan(spec("`scripts/small.py`"), ["scripts/small.py"])
        unit["spec"] = os.path.join(self.root, "docs/plan/specs/U.md")   # absolute: readable whatever the root
        lines, _why = plan_lint.admission({"units": [unit]}, unit, "U.1", os.path.join(self.root, "absent"), {},
                                          ledger=self.ledger)
        self.assertTrue([l for l in lines if l.startswith("LINT NO-DATA U U.1 BRIEF-BUDGET: the repository root is "
                                                         "not a directory")], lines)


if __name__ == "__main__":
    unittest.main()
