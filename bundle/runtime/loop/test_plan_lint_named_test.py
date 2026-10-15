#!/usr/bin/env python3
"""NAMED-TEST: a test file a spec section names that the loop's brief check (B2 IMPORTS SHOWN) would have to open but
cannot find is BLOCKING before the spend. Measured 2026-10-14 on the first live loop run: LL1.c and WR1.a were WITHHELD
by brief_check B2 after a paid run had started, because the lint read both sections as clean. The decision is the one
function brief_check.tests_to_read, called by B2 and by the lint, so this file also pins that they cannot disagree.

Entry points under test: plan_lint.lint_plan (the report) and plan_lint.admission (the pool), with brief_check.check
beside them for the parity cases. Every fixture lives in a tempfile directory; no HOME file is read.

Run: python3 -B scripts/loop/test_plan_lint_named_test.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import brief_check  # noqa: E402
import plan_lint  # noqa: E402  (the module under test, beside this file)

RULE = "NAMED-TEST"
FIX = ("name the test by its full path, label it NEW, or describe a fixture file in words, "
       "not as a backticked tree path")
BUILT = "Built by: a session builder (effort high) in its own worktree"

# (case id, text naming test_y.py in the section, files on disk under the root, test paths that must be BLOCKING)
CASES = [
    ("existing", "`scripts/test_y.py`", ["scripts/test_y.py"], []),
    ("new_prefix_inside", "`NEW: scripts/test_y.py`", [], []),
    ("new_suffix_inside", "`scripts/test_y.py (NEW)`", [], []),
    ("new_word_after", "`scripts/test_y.py` NEW", [], []),
    ("new_paren_after", "`scripts/test_y.py` (NEW)", [], []),
    ("new_word_before", "NEW `scripts/test_y.py`", [], []),
    ("missing_unlabelled", "`scripts/test_y.py`", [], ["scripts/test_y.py"]),
    ("lowercase_new_is_no_label", "`new: scripts/test_y.py`", [], ["scripts/test_y.py"]),
    ("existing_label_is_no_new", "`existing: scripts/test_y.py`", [], ["scripts/test_y.py"]),
    ("fixture_tree_path", "`tests/fixtures/test_data.py`", [], ["tests/fixtures/test_data.py"]),
    ("dot_slash_spelling", "`./scripts/test_y.py`", [], ["scripts/test_y.py"]),
    ("same_missing_path_twice", "`scripts/test_y.py` and again `scripts/test_y.py`", [], ["scripts/test_y.py"]),
    ("bare_with_one_full_path", "`scripts/test_y.py` and `test_y.py`", ["scripts/test_y.py"], []),
    ("bare_with_one_full_path_missing", "`scripts/test_y.py` and `test_y.py`", [], ["scripts/test_y.py"]),
    ("bare_with_two_full_paths", "`scripts/test_y.py`, `tests/test_y.py` and `test_y.py`",
     ["scripts/test_y.py", "tests/test_y.py"], ["test_y.py"]),
    ("bare_with_no_full_path", "`test_y.py`", [], ["test_y.py"]),
    ("bare_matching_a_new_path", "`scripts/test_y.py` NEW and `test_y.py`", [], []),
    ("bare_matching_a_new_path_among_two_full", "`scripts/test_y.py` NEW, `tests/test_y.py` and `test_y.py`",
     ["tests/test_y.py"], []),
    ("two_spellings_of_one_missing_path", "`./scripts/test_y.py` and `scripts/test_y.py`", [], ["scripts/test_y.py"]),
    ("bare_present_at_the_root", "`test_y.py`", ["test_y.py"], []),
    ("not_a_test_file_name", "`scripts/helper_y.py` and `scripts/y_test.py` and `scripts/test_y.sh`", [], []),
]


def spec_text(naming, sub="U.1", more=""):
    """One spec: the open section U.1 (plus `more`), whose Files line names `naming` beside a NEW own test."""
    return "\n".join(["# U", "", "## %s do a thing" % sub, "",
                      "Files: `scripts/x.py`, `scripts/test_x.py` (NEW), %s" % naming, "", BUILT, "",
                      "Done check: `python3 -B scripts/test_x.py`", "", more])


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="namedtest_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        self.touch("scripts/x.py")
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")   # absent: the prior, never a HOME file

    def touch(self, rel):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")

    def unit(self, text, sub_units=("U.1",), evidence=""):
        owns = sorted(set(brief_check.named_paths(text)) | {"scripts/x.py", "scripts/test_x.py"})
        return {"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_x.py",
                "spec": "docs/plan/specs/U.md", "sub_units": list(sub_units), "evidence": evidence, "owns": owns}

    def write_spec(self, text):
        with open(os.path.join(self.root, "docs", "plan", "specs", "U.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def report(self, text, **unit_kw):
        self.write_spec(text)
        plan = os.path.join(self.root, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [self.unit(text, **unit_kw)]}, fh)
        return plan_lint.lint_plan(plan, root=self.root, ledger=None, env={})

    def admit(self, text, sub="U.1"):
        self.write_spec(text)
        unit = self.unit(text)
        return plan_lint.admission({"units": [unit]}, unit, sub, self.root, {}, ledger=self.ledger)

    @staticmethod
    def hits(findings):
        return [f for f in findings if f["rule"] == RULE]

    @staticmethod
    def blocked_paths(hits):
        """The test path each finding names (the text carries it between 'test file ' and the next comma)."""
        return sorted(f["text"].split("test file ", 1)[1].split(",", 1)[0] for f in hits)


class TestEveryCase(Fixture):
    def test_each_case_through_lint_plan(self):
        for cid, naming, disk, want in CASES:
            with self.subTest(case=cid):
                for rel in disk:
                    self.touch(rel)
                hits = self.hits(self.report(spec_text(naming))["findings"])
                self.assertEqual(self.blocked_paths(hits), sorted(want), hits)
                for f in hits:
                    self.assertEqual((f["severity"], f["unit"], f["sub"], f["fix"]), ("BLOCKING", "U", "U.1", FIX))
                for rel in disk:
                    os.remove(os.path.join(self.root, rel))

    def test_each_case_through_admission(self):
        for cid, naming, disk, want in CASES:
            with self.subTest(case=cid):
                for rel in disk:
                    self.touch(rel)
                lines, why = self.admit(spec_text(naming))
                mine = [l for l in lines if " %s: " % RULE in l]
                self.assertEqual(len(mine), len(want), lines)
                for line in mine:
                    self.assertTrue(line.startswith("LINT BLOCKING U U.1 NAMED-TEST: the section names the test file "), line)
                if want:
                    self.assertTrue(why.startswith("LINT NAMED-TEST: the section names the test file "), (why, lines))
                    self.assertIn(want[0], why)
                else:
                    self.assertEqual((lines, why), ([], ""))   # every other rule is silent too: a clean twin
                for rel in disk:
                    os.remove(os.path.join(self.root, rel))

    def test_the_finding_names_the_loop_consequence_and_the_three_remedies(self):
        hits = self.hits(self.report(spec_text("`scripts/test_y.py`"))["findings"])
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("not in the tree and not labelled NEW", hits[0]["text"])
        self.assertIn("WITHHOLD", hits[0]["text"])
        self.assertEqual(hits[0]["fix"], FIX)


class TestParityWithBriefCheck(Fixture):
    """The lint and brief_check.check (B2) read the same section with the same function: they agree on every fixture."""

    def b2(self, text):
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            rows = brief_check.check("U.1", text, "")
        finally:
            os.chdir(cwd)
        return dict(rows)["B2 IMPORTS SHOWN"]

    def test_the_two_readers_agree_on_every_fixture(self):
        for cid, naming, disk, want in CASES:
            with self.subTest(case=cid):
                for rel in disk:
                    self.touch(rel)
                text = spec_text(naming)
                refused = self.b2(text)
                hits = self.hits(self.report(text)["findings"])
                self.assertEqual(refused.startswith("REFUSED"), bool(hits), (refused, hits))
                self.assertEqual(refused.startswith("REFUSED"), bool(want), (refused, want))
                for path in self.blocked_paths(hits):
                    self.assertIn(path, refused)   # both name the same file
                for rel in disk:
                    os.remove(os.path.join(self.root, rel))

    def test_the_lint_uses_the_one_function_b2_uses(self):
        """tests_to_read is the decision: the lint's blocked set is its answer filtered by the same exists()."""
        text = spec_text("`scripts/test_y.py`, `scripts/test_z.py` NEW, `test_w.py`")
        section = brief_check.section_of(text, "U.1")
        there = lambda p: os.path.lexists(os.path.join(self.root, p))  # noqa: E731
        want = [p for p in brief_check.tests_to_read(section, exists=there) if not there(p)]
        got = self.blocked_paths(self.hits(self.report(text)["findings"]))
        self.assertEqual(got, sorted(set(want)))
        self.assertEqual(got, ["scripts/test_y.py", "test_w.py"])
        self.assertIsNone(plan_lint.__dict__.get("tests_to_read"))   # no private copy of the decision
        self.assertIs(plan_lint.brief_check, brief_check)


class TestTestsToRead(unittest.TestCase):
    """brief_check.tests_to_read itself: the injected exists(), the default, and the refusal of a non text section."""

    def sec(self, body):
        return "## U.1 x\n\n%s\n" % body

    def test_exists_is_injected_and_decides_the_new_and_bare_skips(self):
        absent = lambda p: False  # noqa: E731
        present = lambda p: True  # noqa: E731
        self.assertEqual(brief_check.tests_to_read(self.sec("`a/test_x.py` NEW"), exists=absent), [])
        self.assertEqual(brief_check.tests_to_read(self.sec("`a/test_x.py` NEW"), exists=present), ["a/test_x.py"])
        both = self.sec("`a/test_x.py` and `test_x.py`")
        self.assertEqual(brief_check.tests_to_read(both, exists=absent), ["a/test_x.py"])
        self.assertEqual(brief_check.tests_to_read(both, exists=present), ["a/test_x.py", "test_x.py"])
        two = self.sec("`a/test_x.py`, `b/test_x.py`, `test_x.py`")
        self.assertEqual(brief_check.tests_to_read(two, exists=absent), ["a/test_x.py", "b/test_x.py", "test_x.py"])

    def test_the_default_exists_is_the_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="namedtest_cwd_") as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                open("test_here.py", "w").close()
                self.assertEqual(brief_check.tests_to_read(self.sec("`test_here.py` NEW")), ["test_here.py"])
                self.assertEqual(brief_check.tests_to_read(self.sec("`test_gone.py` NEW")), [])
            finally:
                os.chdir(cwd)

    def test_only_test_py_files_and_paths_are_normalised(self):
        got = brief_check.tests_to_read(self.sec("`./a//test_x.py`, `a/helper.py`, `a/test_y.sh`, `a/xtest_z.py`"),
                                        exists=lambda p: False)
        self.assertEqual(got, ["a/test_x.py"])

    def test_duplicates_are_kept_so_the_b2_line_is_byte_for_byte_what_it_was(self):
        got = brief_check.tests_to_read(self.sec("`./a/test_x.py` and `a/test_x.py`"), exists=lambda p: False)
        self.assertEqual(got, ["a/test_x.py", "a/test_x.py"])

    def test_a_section_that_is_not_text_is_refused_by_name(self):
        for bad in (None, 7, b"## U.1", ["x"], {"a": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    brief_check.tests_to_read(bad)


class TestOpenSectionsOnly(Fixture):
    MORE = "\n".join(["## U.2 another", "", "Files: `scripts/x.py`, `scripts/test_x.py` (NEW), `scripts/test_late.py`", "",
                      BUILT, "", "Done check: `python3 -B scripts/test_x.py`", ""])

    def test_a_landed_sub_unit_is_not_read_and_an_open_one_is(self):
        text = spec_text("`scripts/test_y.py` NEW", more=self.MORE)
        landed = self.hits(self.report(text, sub_units=("U.1", "U.2"), evidence="U.2 landed")["findings"])
        self.assertEqual(landed, [])
        open_ = self.hits(self.report(text, sub_units=("U.1", "U.2"), evidence="")["findings"])
        self.assertEqual([(f["sub"], f["severity"]) for f in open_], [("U.2", "BLOCKING")])
        self.assertEqual(self.blocked_paths(open_), ["scripts/test_late.py"])

    def test_a_done_unit_is_never_section_read(self):
        text = spec_text("`scripts/test_y.py`")
        self.write_spec(text)
        unit = self.unit(text)
        unit["state"] = "DONE"
        unit["evidence"] = "U.1 landed"
        plan = os.path.join(self.root, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [unit]}, fh)
        self.assertEqual(self.hits(plan_lint.lint_plan(plan, root=self.root, ledger=None, env={})["findings"]), [])

    def test_admission_reads_only_the_sub_it_is_asked_about(self):
        text = spec_text("`scripts/test_y.py` NEW", more=self.MORE)
        lines, why = self.admit(text, sub="U.1")
        self.assertEqual([l for l in lines if RULE in l], [])
        lines, why = self.admit(text, sub="U.2")
        self.assertEqual(len([l for l in lines if " U U.2 %s: " % RULE in l]), 1, lines)
        self.assertTrue(why.startswith("LINT NAMED-TEST: "), why)


class TestFailDirection(Fixture):
    """An unknown root, a section the reader cannot take, or no section at all never reads as the safe case."""

    def test_an_unknown_root_blocks_every_unlabelled_test(self):
        text = spec_text("`scripts/test_y.py`, `scripts/test_z.py` NEW")
        for root in (None, "", 7, b"/x"):
            with self.subTest(root=root):
                found = plan_lint._named_test_rule_findings("U", "U.1", text, root)
                self.assertEqual(self.blocked_paths(found), ["scripts/test_y.py"], found)

    def test_a_reader_refusal_is_blocking_and_names_the_reason(self):
        real = brief_check.tests_to_read

        def refuse(section, exists=None):
            raise ValueError("named_paths: section is not a str: bytes")

        brief_check.tests_to_read = refuse
        try:
            found = plan_lint._named_test_rule_findings("U", "U.1", spec_text("`scripts/test_y.py` NEW"), self.root)
        finally:
            brief_check.tests_to_read = real
        self.assertEqual([(f["rule"], f["severity"]) for f in found], [(RULE, "BLOCKING")])
        self.assertIn("section is not a str", found[0]["text"])
        self.assertEqual(found[0]["fix"], "fix the section text")

    def test_no_section_or_a_spec_that_is_not_text_is_left_to_section_check(self):
        self.assertEqual(plan_lint._named_test_rule_findings("U", "U.9", spec_text("`scripts/test_y.py`"), self.root), [])
        for bad in (None, 7, b"## U.1"):
            self.assertEqual(plan_lint._named_test_rule_findings("U", "U.1", bad, self.root), [])
        self.assertEqual(plan_lint._named_test_rule_findings("U", None, spec_text("`scripts/test_y.py`"), self.root), [])
        lines, why = self.admit("# U\n\n## U.1 do a thing\n\nnothing is named here\n")
        self.assertEqual([l for l in lines if RULE in l], [])
        self.assertTrue(any(" SECTION-CHECK: " in l for l in lines), lines)   # the missing done check still blocks

    def test_a_section_naming_no_test_has_no_finding(self):
        text = "\n".join(["# U", "", "## U.1 do a thing", "", "Files: `scripts/x.py`, `scripts/test_x.py` (NEW)", "",
                          BUILT, "", "Done check: `python3 -B scripts/test_x.py`", ""])
        self.assertEqual(self.hits(self.report(text)["findings"]), [])


@unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
class TestRealPlan(unittest.TestCase):
    def test_the_1_1_4_plan_has_no_named_test_finding(self):
        plan = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.4-WBS.json")
        if not os.path.isfile(plan):
            self.skipTest("the 1.1.4 plan is not in this tree")
        report = plan_lint.lint_plan(plan, root=ROOT, ledger=None, env={})
        self.assertEqual(report["exit"], 0)
        self.assertEqual([f for f in report["findings"] if f["rule"] == RULE], [])


if __name__ == "__main__":
    unittest.main()
