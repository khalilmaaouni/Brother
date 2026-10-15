#!/usr/bin/env python3
"""LL1.b: OWNS. A section that names a path outside its unit's owns is BLOCKING before a worker starts, whoever builds
it. Entry points under test: plan_lint.admission and plan_lint.lint_plan (where the gate lives), with
_owns_findings and hard_findings beside them. Every fixture lives in a tempfile directory; no HOME file is read.

Run: python3 -B scripts/loop/test_plan_lint_owns.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import plan_lint  # noqa: E402  (the module under test, beside this file)

SESSION = "Built by: a session builder (effort high) in its own worktree"
LOOP = "Built by: Brother.Loop"
OWNS = ["scripts/x.py", "scripts/test_x.py"]


def spec_text(files="`scripts/x.py` (existing), `scripts/test_x.py` (NEW)", built=SESSION, extra="", sub="U.1"):
    """One spec with one section. `built` is the whole Built by line ('' omits it)."""
    if "scripts/test_x.py" not in files:
        files += ", `scripts/test_x.py` (NEW)"   # the section's own test, so TEST-FOUND stays silent
    parts = ["# U", "", "## %s do a thing" % sub, "", "Files: %s" % files, ""]
    if built:
        parts += [built, ""]
    if extra:
        parts += [extra, ""]
    parts += ["Done check: `python3 -B scripts/test_x.py`", ""]
    return "\n".join(parts)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ll1b_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        os.makedirs(os.path.join(self.root, "scripts"))
        for rel in ("x.py", "other.py"):
            with open(os.path.join(self.root, "scripts", rel), "w", encoding="utf-8") as fh:
                fh.write("X = 1\n")
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")   # absent: the prior, never a HOME file

    def unit(self, owns=OWNS):
        unit = {"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_x.py",
                "spec": "docs/plan/specs/U.md", "sub_units": ["U.1"], "evidence": ""}
        if owns != "absent":
            unit["owns"] = owns
        return unit

    def admit(self, text, owns=OWNS):
        with open(os.path.join(self.root, "docs", "plan", "specs", "U.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        unit = self.unit(owns)
        return plan_lint.admission({"units": [unit]}, unit, "U.1", self.root, {}, ledger=self.ledger)

    def owns_lines(self, text, owns=OWNS):
        lines, why = self.admit(text, owns)
        return [l for l in lines if " OWNS: " in l], why


class TestLL1B(Fixture):
    def test_a_files_path_outside_owns_is_blocking_and_names_the_path(self):
        self.assertEqual(self.admit(spec_text()), ([], ""))   # the clean twin: every other rule is silent
        hits, why = self.owns_lines(spec_text(files="`scripts/x.py`, `scripts/other.py`"))
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].startswith("LINT BLOCKING U U.1 OWNS: scripts/other.py is named by the section"), hits[0])
        self.assertIn("reads belong on a Reads line", hits[0])
        self.assertTrue(why.startswith("LINT OWNS: scripts/other.py"), why)
        hits, _ = self.owns_lines(spec_text(files="`scripts/x.py`, `scripts/other.py`, `scripts/third.py`"))
        self.assertEqual(len(hits), 2, hits)   # one finding per distinct unowned path
        hits, why = self.owns_lines(spec_text(files="`scripts/x.py`"), owns=[])   # a unit that owns nothing
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].startswith("LINT BLOCKING U U.1 OWNS: the unit names no owns"), hits[0])
        self.assertTrue(why.startswith("LINT OWNS: the unit names no owns"), why)

    def test_every_owned_path_passes_including_a_new_test_file(self):
        hits, why = self.owns_lines(spec_text(files="`scripts/x.py` (existing), `scripts/test_x.py` (NEW)"))
        self.assertEqual((hits, why), ([], ""))
        hits, why = self.owns_lines(spec_text(files="`scripts/x.py`, `scripts/pkg/deep/a.py`"),
                                    owns=OWNS + ["scripts/pkg/"])   # a trailing slash is a prefix
        self.assertEqual((hits, why), ([], ""))
        hits, _ = self.owns_lines(spec_text(files="`scripts/x.py`, `scripts/pkgother/a.py`"), owns=OWNS + ["scripts/pkg/"])
        self.assertEqual(len(hits), 1, hits)   # the prefix is a folder, not a string prefix
        hits, _ = self.owns_lines(spec_text(files="`scripts/x.py`, `scripts/y.py`"), owns=OWNS + ["scripts/*.py"])
        self.assertEqual(len(hits), 1, hits)   # no glob is honoured

    def test_path_spellings_match_the_owns_entry_after_normalising(self):
        for spelling in ("./scripts/x.py", "scripts//x.py", "scripts/./x.py", "scripts/sub/../x.py"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.owns_lines(spec_text(files="`%s`" % spelling))[0], [])
        hits, _ = self.owns_lines(spec_text(files="`scripts/x.py`"), owns=["./scripts/x.py", "scripts/test_x.py"])
        self.assertEqual(hits, [])   # the entry is normalised too
        hits, _ = self.owns_lines(spec_text(files="`/abs/scripts/x.py`"))
        self.assertEqual(len(hits), 1, hits)   # an absolute path is compared as written, so it is outside

    def test_docs_plan_specs_paths_are_exempt(self):
        hits, why = self.owns_lines(spec_text(files="`scripts/x.py`, `docs/plan/specs/U.md`, `./docs/plan/specs/NEW.md`"))
        self.assertEqual((hits, why), ([], ""))
        hits, _ = self.owns_lines(spec_text(files="`scripts/x.py`, `docs/plan/notes.md`"))   # control: only the specs folder
        self.assertEqual(len(hits), 1, hits)

    def test_owns_missing_empty_or_malformed_is_blocking(self):
        """NO OWNS IS NEVER CLEAN (2026-10-14): absent, empty and malformed all BLOCK, at admission and in the report."""
        section = plan_lint._section(spec_text(files="`scripts/x.py`"), "U.1")
        for owns in ("absent", [], "scripts/x.py", [3], [["scripts/x.py"]], [""], [None], {"a": 1}, ("scripts/x.py",)):
            with self.subTest(owns=owns):
                found = plan_lint._owns_findings("U", "U.1", self.unit(owns), section)
                self.assertEqual([(f["rule"], f["severity"]) for f in found], [("OWNS", "BLOCKING")])
                lines, why = self.admit(spec_text(files="`scripts/x.py`"), owns=owns)
                self.assertTrue(why.startswith("LINT OWNS: "), (why, lines))
                self.assertTrue(any(l.startswith("LINT BLOCKING U U.1 OWNS: ") for l in lines), lines)
        self.assertFalse(hasattr(plan_lint, "REPORT_ABSENT_OWNS"))   # the knob that let a unit with no owns read clean is gone

    def test_a_unit_with_no_usable_owns_is_blocking_through_lint_plan_too(self):
        """The report entry point, not only admission: absent, empty, not a list, a non text entry, a blank entry."""
        with open(os.path.join(self.root, "docs", "plan", "specs", "U.md"), "w", encoding="utf-8") as fh:
            fh.write(spec_text(files="`scripts/x.py`"))
        for owns in ("absent", [], "scripts/x.py", [7], ["scripts/x.py", None], [""], ["  "], {"a": 1}):
            with self.subTest(owns=owns):
                plan = os.path.join(self.root, "plan.json")
                with open(plan, "w", encoding="utf-8") as fh:
                    json.dump({"units": [self.unit(owns)]}, fh)
                report = plan_lint.lint_plan(plan, root=self.root, ledger=None, env={})
                hits = [f for f in report["findings"] if f["rule"] == "OWNS"]
                self.assertEqual([f["severity"] for f in hits], ["BLOCKING"], hits)
        plan = os.path.join(self.root, "plan.json")   # control: a real owns list leaves the rule silent
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [self.unit(OWNS)]}, fh)
        report = plan_lint.lint_plan(plan, root=self.root, ledger=None, env={})
        self.assertEqual([f for f in report["findings"] if f["rule"] == "OWNS"], [])

    def test_a_reads_line_and_a_read_only_annotation_do_not_count_but_a_prose_edit_does(self):
        reads = "Reads: `scripts/other.py` (the shape to copy), and update the notes in `scripts/third.py`"
        self.assertEqual(self.owns_lines(spec_text(extra=reads))[0], [])
        files = "`scripts/x.py`, `scripts/other.py` (read only), `scripts/third.py` (unchanged)"
        self.assertEqual(self.owns_lines(spec_text(files=files))[0], [])
        for sentence in ("The build also edits `scripts/other.py`.", "Register the test in `scripts/third.py`."):
            with self.subTest(sentence=sentence):
                self.assertEqual(len(self.owns_lines(spec_text(extra=sentence))[0]), 1)
        self.assertEqual(self.owns_lines(spec_text(extra="It never edits `scripts/other.py`."))[0], [])
        self.assertEqual(self.owns_lines(spec_text(extra="Compare with `scripts/other.py`."))[0], [])

    def test_a_session_built_section_is_checked_for_owns_too(self):
        for built in (SESSION, "Built by: the session, direct.", LOOP, ""):
            with self.subTest(built=built):
                hits, _ = self.owns_lines(spec_text(files="`scripts/x.py`, `scripts/other.py`", built=built))
                self.assertEqual(len(hits), 1, hits)

    def test_one_section_with_both_defects_returns_both_findings_in_order(self):
        text = spec_text(files="`scripts/x.py`, `scripts/check_all.sh`", built=LOOP)
        got = plan_lint.hard_findings("U", "U.1", self.unit(), text)
        self.assertEqual([(f["rule"], f["severity"]) for f in got],
                         [("PROTECTED-TOUCH", "BLOCKING"), ("OWNS", "BLOCKING"), ])
        self.assertIn("scripts/check_all.sh", got[0]["text"])
        self.assertIn("scripts/check_all.sh", got[1]["text"])
        lines, why = self.admit(text)
        self.assertTrue(why.startswith("LINT PROTECTED-TOUCH: "), why)   # the first blocking line is the pool's `why`
        self.assertEqual(len([l for l in lines if " OWNS: " in l]), 1)
        # lint_plan reports both as BLOCKING for the same section
        plan = os.path.join(self.root, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [self.unit()]}, fh)
        report = plan_lint.lint_plan(plan, root=self.root, ledger=None, env={})
        rules = [(f["rule"], f["severity"]) for f in report["findings"] if f["rule"] in ("PROTECTED-TOUCH", "OWNS")]
        self.assertEqual(rules, [("PROTECTED-TOUCH", "BLOCKING"), ("OWNS", "BLOCKING")])
        # same call twice: same findings in the same order
        self.assertEqual(got, plan_lint.hard_findings("U", "U.1", self.unit(), text))

    def test_hostile_owns_values_never_raise_and_never_pass(self):
        text = spec_text(files="`scripts/x.py`")
        for bad in ("scripts/x.py", ["scripts/x.py", 7], [["scripts/x.py"]], [None], [""], ["  "], 7, 1.5, True, {"scripts/x.py"},
                    ("scripts/x.py",), float("nan"), b"scripts/x.py", object()):
            with self.subTest(owns=repr(bad)):
                unit = self.unit(bad)
                got = plan_lint.hard_findings("U", "U.1", unit, text)
                self.assertEqual([(f["rule"], f["severity"]) for f in got], [("OWNS", "BLOCKING")])
                self.assertEqual([(f["rule"], f["severity"]) for f in plan_lint._owns_findings("U", "U.1", unit, text)],
                                 [("OWNS", "BLOCKING")])
        # a unit that is not a record never raises either (LL1.a's NO-DATA answers it)
        self.assertEqual([f["severity"] for f in plan_lint.hard_findings("U", "U.1", None, text)], ["NO-DATA"])
        # a section cut off mid paragraph: whatever parsed is checked
        cut = "## U.1 cut\n\nFiles: `scripts/x.py`, `scripts/other.py"
        self.assertEqual(plan_lint.hard_findings("U", "U.1", self.unit(), cut), [])
        cut = "## U.1 cut\n\nFiles: `scripts/x.py`, `scripts/other.py`,"
        self.assertEqual(len(plan_lint.hard_findings("U", "U.1", self.unit(), cut)), 1)


if __name__ == "__main__":
    unittest.main()
