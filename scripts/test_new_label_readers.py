#!/usr/bin/env python3
"""FX-09.2: the two sibling readers of the NEW label route through brief_check.labelled_paths.

WHY: a spec may write a new file as `NEW: p` or `p (NEW)`, the label INSIDE the backticks (docs/plan/specs/L0.md does).
Three readers each re read the label with their own regex that assumed it sat OUTSIDE: brief_check's B2 (fixed in
FX-09.1), build_brief.stale_claims, which searched for the literal `p` and so never told the builder that a file the
spec calls NEW already exists, and spec_score, which anchored its window at sec.find("`p`") = -1, the section start,
and so judged a NEW file "called existing and absent". stale_claims is lifted by source (build_brief.py works at
import, the same honest caveat as test_build_brief_corrections._load); spec_score is driven at its entry point, as a
subprocess in a temp git repository with its own HOME. Run: python3 -B scripts/test_new_label_readers.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
import brief_check  # noqa: E402


def _load_stale_claims():
    with open(os.path.join(LOOP, "build_brief.py"), encoding="utf-8") as fh:
        src = fh.read()
    ns = {"os": os, "re": re, "labelled_paths": brief_check.labelled_paths}
    exec(compile(src[src.index("def stale_claims("):src.index("corrections = stale_claims(")], "stale", "exec"), ns)
    return ns["stale_claims"], src


class StaleClaimsReadsEverySpelling(unittest.TestCase):
    def setUp(self):
        self.stale_claims, self.src = _load_stale_claims()

    def test_stale_claims_fires_for_the_inside_spellings(self):
        for section in ("Write `NEW: scripts/already.py` here.", "Write `scripts/already.py (NEW)` here."):
            out = self.stale_claims(section, ["scripts/already.py"])
            self.assertEqual(len(out), 1, section)
            self.assertIn("scripts/already.py is called NEW", out[0])

    def test_stale_claims_keeps_the_outside_spellings(self):
        for section in ("Write `scripts/already.py` NEW here.", "Write NEW `scripts/already.py` here.",
                        "Create the NEW module `scripts/already.py`."):
            self.assertEqual(len(self.stale_claims(section, ["scripts/already.py"])), 1, section)

    def test_an_existing_label_without_new_says_nothing(self):
        self.assertEqual(self.stale_claims("Patch `existing: scripts/already.py` only.", ["scripts/already.py"]), [])
        self.assertEqual(self.stale_claims("Patch `scripts/already.py (existing)` only.", ["scripts/already.py"]), [])

    def test_a_lowercase_label_is_no_exemption_and_no_correction(self):
        self.assertEqual(self.stale_claims("Patch `new: scripts/already.py` only.", ["scripts/already.py"]), [])

    def test_a_correction_names_only_the_path_its_NEW_token_labels(self):
        # scripts/old.py exists and nothing near it says NEW; the NEW token more than 120 characters away labels fresh.py
        section = ("Patch `scripts/old.py` as it stands." + " The helper keeps its shape." * 8
                   + " Write `NEW: scripts/fresh.py` beside it.")
        out = self.stale_claims(section, ["scripts/old.py", "scripts/fresh.py"])
        self.assertEqual([o.split(" ", 1)[0] for o in out], ["scripts/fresh.py"])

    def test_the_one_reader_is_wired(self):
        self.assertIn("labelled_paths(section_text)", self.src)
        self.assertIn("from brief_check import SPEC_PATH, labelled_paths", self.src)


PLAN = {"initiative": "t", "units": [{"id": "X", "title": "x", "spec": "docs/plan/specs/X.md", "sub_units": ["X.1"]}]}


class SpecScoreAnchorsAtTheRealToken(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="label-readers-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        os.makedirs(os.path.join(self.root, "_home"))
        with open(os.path.join(self.root, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as fh:
            json.dump(PLAN, fh)
        r = subprocess.run(["git", "init", "-q"], cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def score(self, section):
        with open(os.path.join(self.root, "docs", "plan", "specs", "X.md"), "w") as fh:
            fh.write("# X\n\n" + section + "\nDone check: `python3 -B scripts/test_fresh_mod.py`\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_") and k != "HOME"}
        env["HOME"] = os.path.join(self.root, "_home")
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "spec_score.py"), "X"], cwd=self.root, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        line = next((l for l in r.stdout.splitlines() if l.startswith("X.1")), None)
        self.assertIsNotNone(line, r.stdout)
        return line

    def test_spec_score_does_not_call_an_inside_labelled_new_file_existing(self):
        line = self.score("#### X.1 extend the existing plumbing\nThis sub unit adds one helper module and its test, "
                          "and nothing more than that.\nFiles: `NEW: scripts/fresh_mod.py` and `scripts/test_fresh_mod.py (NEW)`.\n")
        self.assertNotIn("PATHS TRUE", line)

    def test_spec_score_still_flags_an_inside_labelled_existing_file_that_is_absent(self):
        line = self.score("#### X.1 add a small thing\nThis sub unit changes one module and adds its test, and "
                          "nothing more than that.\nFiles: `existing: scripts/gone_mod.py` is the module this changes, and the section "
                          "adds nothing else to it at all.\nTests: `scripts/test_fresh_mod.py` NEW.\n")
        self.assertIn("PATHS TRUE: scripts/gone_mod.py is called existing and is absent", line)

    def test_spec_score_keeps_todays_plain_token_window(self):
        line = self.score("#### X.1 add a small thing\nThis sub unit changes one module and adds its test, and "
                          "nothing more than that.\nPatch the existing `scripts/gone_mod.py`, which is the module this changes, and "
                          "nothing else at all.\nTests: `scripts/test_fresh_mod.py` NEW.\n")
        self.assertIn("PATHS TRUE: scripts/gone_mod.py is called existing and is absent", line)

    def test_spec_score_anchors_at_the_first_occurrence_of_a_path(self):
        # the first `scripts/gone_mod.py` is called existing; a later one sits next to an unrelated NEW token
        line = self.score("#### X.1 add a small thing\nThis sub unit changes one module and adds its test, and "
                          "nothing more than that.\nPatch the existing `scripts/gone_mod.py`, which is the module this "
                          "changes, and nothing else at all.\n" + "The change keeps every caller as it is today.\n" * 3
                          + "Tests: `scripts/test_fresh_mod.py` NEW, beside `scripts/gone_mod.py`.\n")
        self.assertIn("PATHS TRUE: scripts/gone_mod.py is called existing and is absent", line)


if __name__ == "__main__":
    unittest.main()
