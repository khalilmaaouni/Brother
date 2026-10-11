#!/usr/bin/env python3
"""LS1.b: the scorer applies the landing standard's rules (own test, mirrored file, checklist) and floors at 0.

Runs scripts/loop/spec_score.py as a subprocess in a throwaway git repository holding a one unit plan, under a
throwaway HOME, so no real plan is scored and no real claim ledger is written.

Run: python3 -B scripts/loop/test_spec_score_standard.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCORER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_score.py")
CHECKLIST = "\n".join("%s yes" % k for k in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12"))


def score(section, worker="the session", extra_files=()):
    root = tempfile.mkdtemp(prefix="score-std-")
    try:
        os.makedirs(os.path.join(root, "docs", "plan", "specs"))
        for rel in extra_files:
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            open(os.path.join(root, rel), "w").write("x = 1\n")
        unit = {"id": "X", "spec": "docs/plan/specs/X.md", "sub_units": ["X.a"], "worker": worker}
        json.dump({"units": [unit]}, open(os.path.join(root, "docs", "plan", "T.json"), "w"))
        open(os.path.join(root, "docs", "plan", "specs", "X.md"), "w").write("# X\n\n## 2. Sub-units\n\n" + section)
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            done = subprocess.run(argv, cwd=root, capture_output=True, text=True)
            if done.returncode != 0:
                raise AssertionError("fixture git %s failed: %s" % (argv, done.stderr))
        env = dict(os.environ, BROTHER_WBS="docs/plan/T.json", HOME=root)
        out = os.path.join(root, "out.json")
        r = subprocess.run([sys.executable, "-B", SCORER, "X", "--json", out], cwd=root, env=env,
                           capture_output=True, text=True, timeout=300)
        if not os.path.exists(out):
            raise AssertionError("the scorer wrote no scores (exit %d): %s" % (r.returncode, (r.stderr or r.stdout)[-600:]))
        return json.load(open(out))["X.a"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ScorerAppliesTheStandard(unittest.TestCase):
    def test_a_done_check_running_another_sub_units_test_is_refused(self):
        """The RL3.a shape: the section names its own test, the done check runs a sibling's."""
        sec = ("### X.a The thing\n\nFiles: `scripts/x.py` (existing), `scripts/test_x.py` (existing)\n\n"
               "Done check: `python3 -B scripts/test_other.py`\n\n" + CHECKLIST + "\n")
        got = score(sec, extra_files=("scripts/x.py", "scripts/test_x.py", "scripts/test_other.py"))
        self.assertTrue(any(m.startswith("OWN TEST") and "scripts/test_other.py" in m for m in got["missing"]), got)

    def test_a_section_without_the_checklist_names_the_missing_keys(self):
        sec = "### X.a The thing\n\nFiles: `scripts/x.py` (existing)\n\nDone check: `python3 -B scripts/test_x.py`\n"
        got = score(sec, extra_files=("scripts/x.py", "scripts/test_x.py"))
        self.assertIn("CHECKLIST: missing S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12", got["missing"])

    def test_a_loop_built_section_naming_bundle_is_refused(self):
        sec = ("### X.a The thing\n\nFiles: `bundle/runtime/x.py` (existing)\n\nDone check: `python3 -B scripts/test_x.py`\n\n"
               + CHECKLIST + "\n")
        got = score(sec, worker="the loop (fixture)", extra_files=("bundle/runtime/x.py", "scripts/test_x.py"))
        self.assertTrue(any(m.startswith("MIRRORED") for m in got["missing"]), got)

    def test_a_session_built_section_naming_bundle_is_not_refused_for_it(self):
        sec = ("### X.a The thing\n\nFiles: `bundle/runtime/x.py` (existing)\n\nDone check: `python3 -B scripts/test_x.py`\n\n"
               + CHECKLIST + "\n")
        got = score(sec, worker="the session", extra_files=("bundle/runtime/x.py", "scripts/test_x.py"))
        self.assertFalse(any(m.startswith("MIRRORED") for m in got["missing"]), got)

    def test_a_loop_built_section_that_only_reads_a_mirrored_file_is_not_refused(self):
        """N19.a, 2026-10-11: a Reads: line names a file the build never writes."""
        sec = ("### X.a The thing\n\nFiles: `scripts/x.py` (existing)\n\nReads: `bundle/runtime/y.py` (existing)\n\n"
               "Done check: `python3 -B scripts/test_x.py`\n\n" + CHECKLIST + "\n")
        got = score(sec, worker="the loop (fixture)", extra_files=("scripts/x.py", "bundle/runtime/y.py", "scripts/test_x.py"))
        self.assertFalse(any(m.startswith("MIRRORED") for m in got["missing"]), got)

    def test_more_than_ten_misses_floor_at_zero(self):
        """A section missing nearly everything: the score never goes below 0."""
        sec = "### X.a The thing\n\nFiles: `bundle/runtime/gone.py` (existing)\n\nIt calls `ghost_zz_fn()`.\n\nTBD\n"
        got = score(sec, worker="the loop (fixture)")
        self.assertGreater(len(got["missing"]), 10, got)
        self.assertEqual(got["score"], 0, got)


if __name__ == "__main__":
    unittest.main()
