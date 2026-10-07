#!/usr/bin/env python3
"""The staged candidate carries the data its tools read beside themselves (deploy_stamped.CANDIDATE_DATA).

Review 15, 2026-10-03: stage_candidate stages the import closure, Python only, so the candidate held every grader module
and not the sandbox profile grade_build reads from its own directory (SANDBOX_PROFILE, scripts/loop/sandbox.sb); the
closer's done check under a proof's code root then refused NO-DATA ("sandbox profile is missing") on every unit. The bin
deploy copies the whole of scripts/loop and never saw it. These cases sit in their own file: scripts/test_candidate_stage.py
replays the deploy suite, whose four live process cases are red on the run line (3de8cbb43 stopped carrying the test only
fake process table they inject), and a file the gate selects must answer for its own cases alone.
Run: python3 scripts/test_candidate_data.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
import deploy_stamped as D  # noqa: E402
from test_candidate_stage import fixture_source, scratch  # noqa: E402


class TheCandidateCarriesItsData(unittest.TestCase):
    def setUp(self):
        self.box = scratch("run-cand-data-")
        self.dest = self.box / "candidate"
        home = self.box / "home"
        (home / ".claude" / "hooks").mkdir(parents=True)
        (home / ".claude" / "hooks" / "bm_session_cap.py").write_text('"""scratch stand in"""\nimport json\n')
        self.home = home

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self.box), ignore_errors=True)

    def stage(self, src, dest=None):
        from unittest import mock
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            return D.stage_candidate(src, dest or self.dest)

    def test_the_sandbox_profile_is_staged_beside_the_grader_when_the_source_holds_it(self):
        src = fixture_source(self.box, {"scripts/loop/sandbox.sb": "(version 1)\n"})
        staged = self.stage(src)
        self.assertIn("scripts/loop/sandbox.sb", staged)
        self.assertEqual((self.dest / "scripts/loop/sandbox.sb").read_text(), "(version 1)\n")

    def test_a_source_without_the_profile_stages_the_tools_alone(self):
        staged = self.stage(fixture_source(self.box))
        self.assertNotIn("scripts/loop/sandbox.sb", staged)
        self.assertTrue((self.dest / "scripts/close_unit.py").is_file())
        self.assertFalse((self.dest / "scripts/loop/sandbox.sb").exists())

    def test_every_declared_data_file_is_a_real_file_of_this_tree(self):
        # the declaration names what exists: a renamed profile would stage nothing and read NO-DATA at every close
        repo = os.path.dirname(HERE)
        for rel in D.CANDIDATE_DATA:
            self.assertTrue(os.path.isfile(os.path.join(repo, rel)), rel)


if __name__ == "__main__":
    unittest.main()
