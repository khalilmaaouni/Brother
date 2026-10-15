#!/usr/bin/env python3
"""PG1.c: SHARED-FILE. A path in two units' owns with no after path between them is BLOCKING in the plan lint and
refused at admission. Entry points under test: plan_lint.lint_plan, plan_lint.admission and plan_lint.main (where
the rule is wired), with shared_file_findings and after_closure beside them. Fixtures live in a tempfile directory;
no HOME file is read.

Run: python3 -B scripts/loop/test_plan_lint_shared.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import plan_lint  # noqa: E402  (the module under test, beside this file)


def unit(uid, owns, after=None, depends_on=None, state="OPEN"):
    rec = {"id": uid, "state": state, "owns": owns, "done_check": "python3 -B scripts/test_x.py",
           "spec": "docs/plan/specs/%s.md" % uid, "sub_units": [], "evidence": ""}
    if after is not None:
        rec["after"] = after
    if depends_on is not None:
        rec["depends_on"] = depends_on
    return rec


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pg1c_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", "test_x.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n\n\n"
                     "if __name__ == '__main__':\n    unittest.main()\n")   # the done_check's test, so TEST-FOUND stays silent

    def lint(self, units, scope="."):
        path = os.path.join(self.root, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"units": units}, fh)
        report = plan_lint.lint_plan(path, root=self.root, scope=scope, ledger=self.ledger, env={})
        return [f for f in report["findings"] if f["rule"] == "SHARED-FILE"]


class TestSharedFile(Fixture):
    def test_two_units_sharing_a_file_with_no_after_is_blocking(self):
        hits = self.lint([unit("A", ["scripts/x.py"]), unit("B", ["scripts/x.py"])])
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual((hits[0]["unit"], hits[0]["severity"]), ("A", "BLOCKING"))
        self.assertIn("scripts/x.py", hits[0]["text"])
        self.assertIn("A", hits[0]["text"])
        self.assertIn("B", hits[0]["text"])
        self.assertIn("add A to the after of B", hits[0]["fix"])
        self.assertEqual(self.lint([unit("A", ["scripts/x.py"])]), [])            # one unit: no pair
        self.assertEqual(self.lint([unit("A", ["scripts/x.py"]), unit("B", ["scripts/y.py"])]), [])
        three = self.lint([unit(i, ["scripts/x.py"]) for i in "ABC"])
        self.assertEqual(len(three), 3, three)                                      # a path in three units: three pairs

    def test_a_direct_or_transitive_after_clears_the_finding(self):
        x = ["scripts/x.py"]
        self.assertEqual(self.lint([unit("A", x), unit("B", x, after=["A"])]), [])        # later unit after earlier
        self.assertEqual(self.lint([unit("A", x, after=["B"]), unit("B", x)]), [])        # the reverse direction
        self.assertEqual(self.lint([unit("A", x), unit("B", x, depends_on=["A"])]), [])   # depends_on counts
        chain = [unit("A", x), unit("M", ["scripts/m.py"], after=["A"]), unit("B", x, after=["M"])]
        self.assertEqual(self.lint(chain), [])                                            # transitive
        chain3 = [unit("A", x), unit("M", ["scripts/m.py"], after=["A"]), unit("N", ["scripts/n.py"], after=["M"]),
                  unit("B", x, after=["N"])]
        self.assertEqual(self.lint(chain3), [])                                           # two hops
        self.assertEqual(len(self.lint([unit("A", x), unit("M", ["scripts/m.py"], after=["A"]), unit("B", x)])), 1)
        self.assertEqual(plan_lint.after_closure({"units": chain3})["B"], {"N", "M", "A"})

    def test_done_units_and_spec_paths_are_exempt(self):
        x = ["scripts/x.py"]
        self.assertEqual(self.lint([unit("A", x, state="DONE"), unit("B", x)]), [])
        self.assertEqual(self.lint([unit("A", x), unit("B", x, state="DONE")]), [])
        self.assertEqual(len(self.lint([unit("A", x, state="DEFERRED"), unit("B", x)])), 1)   # only DONE is exempt
        spec = ["docs/plan/specs/x.md"]
        self.assertEqual(self.lint([unit("A", spec), unit("B", spec)]), [])
        self.assertEqual(len(self.lint([unit("A", ["docs/plan/BROTHER-X.json"]), unit("B", ["docs/plan/BROTHER-X.json"])])), 1)
        self.assertEqual(len(self.lint([unit("A", ["./scripts/x.py"]), unit("B", ["scripts/x.py"])])), 1)   # normalised

    def test_lint_plan_reports_once_and_admission_refuses(self):
        x = ["scripts/x.py"]
        units = [unit("A", x), unit("B", x), unit("C", ["scripts/c.py"])]
        hits = self.lint(units)
        self.assertEqual(len(hits), 1, hits)                                        # once, not once per unit
        self.assertEqual(self.lint(units, scope="^B$"), [])                         # filed under the first unit only
        self.assertEqual(len(self.lint(units, scope="^A$")), 1)
        plan = {"units": units}
        for who in ("A", "B"):                                                      # either side of the pair is refused
            lines, why = plan_lint.admission(plan, [u for u in units if u["id"] == who][0], who + ".1", self.root, {},
                                             ledger=self.ledger)
            self.assertTrue(why.startswith("LINT SHARED-FILE: scripts/x.py"), (who, lines, why))
            self.assertTrue(any(l.startswith("LINT BLOCKING A - SHARED-FILE") for l in lines), lines)
        _, why = plan_lint.admission(plan, units[2], "C.1", self.root, {}, ledger=self.ledger)
        self.assertNotIn("SHARED-FILE", why)                                        # the third unit is not in the pair
        cleared = {"units": [unit("A", x), unit("B", x, after=["A"])]}
        _, why = plan_lint.admission(cleared, cleared["units"][1], "B.1", self.root, {}, ledger=self.ledger)
        self.assertNotIn("SHARED-FILE", why)

    def test_main_prints_the_line_and_counts_it_blocking(self):
        path = os.path.join(self.root, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"units": [unit("A", ["scripts/x.py"]), unit("B", ["scripts/x.py"])]}, fh)
        out = io.StringIO()
        here = os.getcwd()
        os.chdir(self.root)   # main reads the repository from the current directory
        self.addCleanup(os.chdir, here)
        with contextlib.redirect_stdout(out):
            plan_lint.main(["--report", path, "--ledger", self.ledger])
        text = out.getvalue()
        self.assertIn("LINT BLOCKING A - SHARED-FILE:", text)
        self.assertIn("LINT SUMMARY blocking 1", text)

    def test_malformed_owns_and_cycles_never_read_clean(self):
        x = ["scripts/x.py"]
        for bad in ("scripts/x.py", 7, ["ok.py", 3], [""], {"a": 1}):
            hits = self.lint([unit("A", bad), unit("B", x)])
            self.assertEqual([(h["severity"], h["unit"]) for h in hits], [("NO-DATA", "A")], (bad, hits))
        for bad_after in (5, "A", ["A", None]):
            hits = self.lint([unit("A", x), unit("B", x, after=bad_after)])
            self.assertEqual([(h["severity"], h["unit"]) for h in hits], [("NO-DATA", "B")], (bad_after, hits))
        self.assertEqual([h["severity"] for h in self.lint([unit("A", x), {"id": 3, "owns": x}])], ["NO-DATA"])
        cyc = [unit("A", x, after=["B"]), unit("B", x, after=["A"])]
        self.assertEqual(self.lint(cyc), [])                                        # a cycle orders them either way
        self.assertEqual(plan_lint.after_closure({"units": cyc}), {"A": {"B"}, "B": {"A"}})
        selfref = [unit("A", x, after=["A"]), unit("B", x)]
        self.assertEqual(len(self.lint(selfref)), 1)                                # an unit after itself orders nothing
        self.assertEqual(self.lint([unit("A", None), unit("B", x)]), [])            # no owns: nothing to share
        big = unit("A", ["scripts/f%d.py" % i for i in range(5000)] + ["scripts/x.py"])
        self.assertEqual(len(self.lint([big, unit("B", x)])), 1)
        with self.assertRaises(ValueError):
            plan_lint.after_closure({"units": [unit("A", x), "B"]})
        with self.assertRaises(ValueError):
            plan_lint.shared_file_findings({"units": "nope"})
        # a non record entry is the per unit rules' to report; the pair rule still judges the records around it
        self.assertEqual(len(self.lint([unit("A", x), "junk", unit("B", x)])), 1)

    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
    def test_the_real_plan_has_no_shared_file_finding(self):
        path = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.4-WBS.json")
        if not os.path.isfile(path):
            self.skipTest("the 1.1.4 plan is not on this tree")
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
        self.assertEqual(plan_lint.shared_file_findings(plan), [])
        # not vacuous: with every after stripped the same plan does share files
        stripped = {"units": [dict(u, after=[], depends_on=[]) for u in plan["units"]]}
        self.assertTrue(plan_lint.shared_file_findings(stripped))


if __name__ == "__main__":
    unittest.main()
