#!/usr/bin/env python3
"""C20.a: the plan lint asks the loop's own sibling_new rule (SIBLING-NEW, L07) about every open section, before any
worker is paid. HERMETIC-HOME (L27) was withdrawn 2026-10-16 (docs/plan/specs/C20.md): importing hermetic_test_check
pulled a dynamic loader into the loop's frozen closure. The pre-push hermetic gate keeps the HOME rule.

Entry points under test: plan_lint.lint_plan (the report), plan_lint.main (the CLI the loop intake runs) and
plan_lint.admission (the pool). Per rule: a spec that must be refused, one that must pass, and one where the rule's
function cannot be imported (NO-DATA, never clean). Every fixture lives in a tempfile directory and HOME is a
tempfile directory for every case, so no file of the real home folder is read or written.

Run: python3 -B scripts/loop/test_plan_lint_rules_115.py
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

BUILT = "Built by: a session builder (effort high) in its own worktree"


def section(sub, files, done="python3 -B scripts/test_x.py"):
    return "\n".join(["## %s do a thing" % sub, "", "Files: %s" % files, "", BUILT, "", "Done check: `%s`" % done, ""])


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="c20a_")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "root")
        self.home = os.path.join(tmp.name, "home")
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        os.makedirs(self.home)
        with open(os.path.join(self.home, ".brothersbe-private-names"), "w", encoding="utf-8") as fh:
            fh.write("# empty list: no private term in these fixtures\n")
        saved = {k: os.environ.get(k) for k in ("HOME", "BROTHER_SCRATCH")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        os.environ["HOME"] = self.home
        os.environ.pop("BROTHER_SCRATCH", None)
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")

    def write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def unit(self, subs, owns, evidence=""):
        return {"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_x.py", "spec": "docs/plan/specs/U.md",
                "sub_units": list(subs), "evidence": evidence, "owns": list(owns)}

    def plan(self, spec, subs, owns, evidence=""):
        self.write("docs/plan/specs/U.md", spec)
        unit = self.unit(subs, owns, evidence)
        return self.write("plan.json", json.dumps({"units": [unit]})), unit

    def hits(self, findings, rule):
        return [f for f in findings if f["rule"] == rule]

    def run_main(self, argv):
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out):
                code = plan_lint.main(argv)
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def hide_module(self, name):
        """The module cannot be imported for the rest of this case (sys.modules None makes `import` raise)."""
        had = name in sys.modules
        old = sys.modules.get(name)
        sys.modules[name] = None

        def back():
            if had:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)
        self.addCleanup(back)


SIBLING_SPEC = "# U\n\n" + section("U.a", "`scripts/a.py` (NEW), `scripts/test_x.py` (NEW)") + "\n" + \
    section("U.b", "`scripts/a.py` (NEW), `scripts/b.py` (NEW)")
SIBLING_OWNS = ["scripts/a.py", "scripts/b.py", "scripts/test_x.py"]


class SiblingNew(Fixture):
    """REQ-C20-a-01 to 04: SIBLING-NEW is sibling_new.sibling_new_conflicts, asked of every open section."""

    def test_a_later_sibling_labelling_an_earlier_new_path_new_is_reported_on_the_later_one(self):
        path, _unit = self.plan(SIBLING_SPEC, ["U.a", "U.b"], SIBLING_OWNS)
        hits = self.hits(plan_lint.lint_plan(path, root=self.root, ledger=None, env={})["findings"], "SIBLING-NEW")
        self.assertEqual([(f["sub"], f["severity"]) for f in hits], [("U.b", "ADVISORY")], hits)
        self.assertIn("scripts/a.py", hits[0]["text"])
        self.assertIn("U.a", hits[0]["text"])
        code, out = self.run_main(["--report", path, "--ledger", self.ledger])
        self.assertEqual(code, 0, out)
        self.assertIn("LINT ADVISORY U U.b SIBLING-NEW: the section labels scripts/a.py NEW", out)

    def test_the_plan_order_decides_which_sibling_is_the_later_one(self):
        path, _unit = self.plan(SIBLING_SPEC, ["U.b", "U.a"], SIBLING_OWNS)
        hits = self.hits(plan_lint.lint_plan(path, root=self.root, ledger=None, env={})["findings"], "SIBLING-NEW")
        self.assertEqual([f["sub"] for f in hits], ["U.a"], hits)

    def test_a_landed_earlier_sibling_still_created_the_file(self):
        path, _unit = self.plan(SIBLING_SPEC, ["U.a", "U.b"], SIBLING_OWNS, evidence="U.a landed 2026-10-16: green.")
        report = plan_lint.lint_plan(path, root=self.root, ledger=None, env={})
        self.assertEqual(report["read"]["sections"], 1)
        self.assertEqual([f["sub"] for f in self.hits(report["findings"], "SIBLING-NEW")], ["U.b"])

    def test_an_existing_label_and_a_repeat_inside_one_section_pass(self):
        spec = "# U\n\n" + section("U.a", "`scripts/a.py` (NEW), `scripts/test_x.py` (NEW)") + "\n" + \
            section("U.b", "`scripts/a.py` (existing), `scripts/b.py` (NEW), `scripts/b.py` (NEW)")
        path, unit = self.plan(spec, ["U.a", "U.b"], SIBLING_OWNS)
        self.assertEqual(self.hits(plan_lint.lint_plan(path, root=self.root, ledger=None, env={})["findings"],
                                   "SIBLING-NEW"), [])
        lines, _why = plan_lint.admission({"units": [unit]}, unit, "U.b", self.root, {}, ledger=self.ledger)
        self.assertFalse([l for l in lines if "SIBLING-NEW" in l], lines)

    def test_an_unimportable_sibling_new_reads_no_data_never_clean(self):
        path, unit = self.plan(SIBLING_SPEC, ["U.a", "U.b"], SIBLING_OWNS)
        self.hide_module("sibling_new")
        hits = self.hits(plan_lint.lint_plan(path, root=self.root, ledger=None, env={})["findings"], "SIBLING-NEW")
        self.assertEqual(sorted((f["sub"], f["severity"]) for f in hits), [("U.a", "NO-DATA"), ("U.b", "NO-DATA")])
        code, out = self.run_main(["--report", path, "--ledger", self.ledger])
        self.assertEqual(code, 3, out)
        self.assertIn("LINT NO-DATA U U.a SIBLING-NEW: the loop's sibling_new rule could not run", out)
        _lines, why = plan_lint.admission({"units": [unit]}, unit, "U.a", self.root, {}, ledger=self.ledger)
        self.assertTrue(why.startswith("LINT "), why)


if __name__ == "__main__":
    unittest.main()
