#!/usr/bin/env python3
"""RR lane F4: the grading wrappers carry the grader's own exit, and spec_accept says what happened in its exit.

The modules under test are scripts/loop/grade_lane.sh, grade_one.sh, grade_all.sh, grade_all_par.sh and spec_accept.py,
each driven at its entry point. Codex audit of 2026-09-27 (F-grading-and-build): finding 3 (grade_lane.sh took ANY line
starting PASS), finding 9 (grade_one.sh and grade_all.sh exited 0 after the grader refused) and finding 10 (spec_accept
exited 0 on a missing drafts folder and on a refused draft). The grader is a stub here, so only the wrapper is tested.

Run: python3 -B scripts/loop/test_grade_wrappers_f4.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def bash(argv, home, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
    env["HOME"] = home
    return subprocess.run(["bash"] + argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


class TheWrappersCarryTheGradersExit(unittest.TestCase):
    """Codex audit F3 and F9: grade_lane.sh took any PASS line; grade_one.sh and grade_all.sh exited 0 after a refusal.
    The grader here is a stub that refuses any build whose name has no 'pass' in it, so the wrapper is all that is tested."""
    STUB = ("import os, sys\nok = 'pass' in os.path.basename(sys.argv[1])\n"
            "print('PASS' if ok else 'FAIL: stub refused'); sys.exit(0 if ok else 1)\n")

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="grade-wrap-f4-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = os.path.join(self.d, "home")
        bin_ = os.path.join(self.home, ".claude", "bin")
        os.makedirs(bin_)
        with open(os.path.join(bin_, "grade_build.py"), "w", encoding="utf-8") as fh:
            fh.write(self.STUB)
        os.symlink(os.path.join(HERE, "grade_one.sh"), os.path.join(bin_, "grade_one.sh"))
        self.w = os.path.join(self.d, "wave")
        os.makedirs(os.path.join(self.w, "out"))
        os.makedirs(os.path.join(self.w, "grades"))

    def build(self, name):
        with open(os.path.join(self.w, "out", name + "-build.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        return os.path.join(self.w, "out", name + "-build.json")

    def grade_file(self, name, text):
        with open(os.path.join(self.w, "grades", name + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_grade_one_exits_with_the_graders_refusal(self):
        r = bash([os.path.join(HERE, "grade_one.sh"), self.build("X-r0")], self.home)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        with open(os.path.join(self.w, "grades", "X-r0.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().splitlines()[-1], "exit=1")

    def test_control_grade_one_exits_0_on_the_graders_pass(self):
        self.assertEqual(bash([os.path.join(HERE, "grade_one.sh"), self.build("X-pass")], self.home).returncode, 0)

    def test_grade_all_exits_1_when_any_build_was_refused(self):
        self.build("A-pass"); self.build("B-r0")
        r = bash([os.path.join(HERE, "grade_all.sh"), self.w], self.home)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_control_grade_all_exits_0_when_every_build_passed(self):
        self.build("A-pass"); self.build("B-pass")
        r = bash([os.path.join(HERE, "grade_all.sh"), self.w], self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_grade_all_reads_a_recorded_refusal_without_regrading(self):
        self.build("A-pass")
        self.grade_file("A-pass", "PASS\nexit=1\n")
        self.assertEqual(bash([os.path.join(HERE, "grade_all.sh"), self.w], self.home).returncode, 1)

    def test_grade_all_and_its_parallel_form_say_no_data_on_an_empty_wave(self):
        for script in ("grade_all.sh", "grade_all_par.sh"):
            r = bash([os.path.join(HERE, script), self.w], self.home)
            self.assertEqual(r.returncode, 2, script + r.stdout + r.stderr)
            self.assertIn("NO-DATA", r.stdout, script)
            self.assertEqual(os.listdir(os.path.join(self.w, "grades")), [], "an empty wave wrote a grade: " + script)

    def test_grade_all_par_exits_1_when_any_build_was_refused_and_0_when_none_was(self):
        self.build("A-pass"); self.build("B-r0")
        self.assertEqual(bash([os.path.join(HERE, "grade_all_par.sh"), self.w], self.home).returncode, 1)
        os.remove(os.path.join(self.w, "out", "B-r0-build.json")); os.remove(os.path.join(self.w, "grades", "B-r0.txt"))
        self.assertEqual(bash([os.path.join(HERE, "grade_all_par.sh"), self.w], self.home).returncode, 0)

    def lane(self, text):
        self.build("X-r0")
        self.grade_file("X-r0", text)
        return bash([os.path.join(HERE, "grade_lane.sh"), self.w, "X"], self.home)

    def test_grade_lane_the_codex_shape_a_pass_line_above_the_real_fail(self):
        r = self.lane("UNKNOWNS           worker text\nPASS\nFAIL: tests pass without the code\nexit=1\n")
        self.assertEqual((r.returncode, "X NO-PASS" in r.stdout), (1, True), r.stdout)

    def test_grade_lane_a_pass_verdict_with_a_nonzero_exit_is_not_a_pass(self):
        r = self.lane("PASS\nexit=1\n")
        self.assertEqual((r.returncode, "X NO-PASS" in r.stdout), (1, True), r.stdout)

    def test_grade_lane_a_later_fail_verdict_wins_over_an_earlier_pass_even_at_exit_0(self):
        r = self.lane("PASS\nFAIL: the last word\nexit=0\n")
        self.assertEqual((r.returncode, "X NO-PASS" in r.stdout), (1, True), r.stdout)

    def test_grade_lane_a_verdict_that_only_starts_with_pass_is_not_a_pass(self):
        r = self.lane("PASSED by the worker\nexit=0\n")
        self.assertEqual((r.returncode, "X NO-PASS" in r.stdout), (1, True), r.stdout)

    def test_control_grade_lane_the_graders_own_pass_at_exit_0_is_a_pass(self):
        r = self.lane("RED-WITHOUT-CODE   yes\nPASS\nexit=0\n")
        self.assertEqual((r.returncode, "X PASS X-r0" in r.stdout), (0, True), r.stdout)


class SpecAcceptSaysWhatHappened(unittest.TestCase):
    """Codex audit F10: a missing drafts folder and a draft refused for a missing section both exited 0."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="spec-accept-f4-")
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "docs", "plan", "specs"))
        os.makedirs(os.path.join(self.d, "home", ".claude", "evidence"))   # spec_score writes its scores here
        with open(os.path.join(self.d, "home", ".brothersbe-private-names"), "w", encoding="utf-8") as fh:
            fh.write("zz-fixture-private-token\n")
        plan = {"units": [{"id": "X", "spec": "docs/plan/specs/X.md", "sub_units": ["X.1"]}]}
        with open(os.path.join(self.d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        with open(os.path.join(self.d, "docs", "plan", "specs", "X.md"), "w", encoding="utf-8") as fh:
            fh.write("# X\n\n## X.1 first\n\nExisting `docs/plan/specs/X.md` is the file.\n")
        for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c",
                                                      "commit.gpgsign=false", "commit", "-q", "-m", "base"]):
            subprocess.run(["git"] + argv, cwd=self.d, capture_output=True, check=True)
        self.drafts = os.path.join(self.d, "drafts")

    def accept(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER_"))}
        env.update(HOME=os.path.join(self.d, "home"), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, "-B", os.path.join(HERE, "spec_accept.py")] + list(args), cwd=self.d,
                              capture_output=True, text=True, env=env, timeout=120)

    def draft(self, text):
        os.makedirs(self.drafts, exist_ok=True)
        with open(os.path.join(self.drafts, "X-fixture.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_missing_drafts_folder_is_no_data(self):
        r = self.accept(self.drafts, "X")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("NO-DATA: the drafts folder", r.stdout)
        self.assertIn("does not exist", r.stdout)

    def test_a_folder_with_no_draft_of_the_unit_asked_for_is_no_data(self):
        os.makedirs(self.drafts)
        r = self.accept(self.drafts, "X")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_a_refused_draft_exits_1(self):
        self.draft("# X\n\nno section at all\n")
        r = self.accept(self.drafts, "X")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("a sub unit section is missing", r.stdout)

    def test_control_an_acceptable_draft_exits_0(self):
        self.draft("# X\n\n## X.1 first\n\nExisting `docs/plan/specs/X.md` is the file.\n")
        r = self.accept(self.drafts, "X", "--floor", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACCEPTABLE", r.stdout)

    def test_no_arguments_is_a_usage_refusal(self):
        self.assertEqual(self.accept().returncode, 2)

    def test_a_floor_outside_1_to_10_is_refused_before_any_draft_is_read(self):
        self.draft("# X\n\n## X.1 first\n\nExisting `docs/plan/specs/X.md` is the file.\n")
        for floor in ("0", "11"):
            r = self.accept(self.drafts, "X", "--floor", floor)
            self.assertEqual(r.returncode, 2, floor + r.stdout)
            self.assertIn("a floor must be between 1 and 10", r.stdout)


if __name__ == "__main__":
    unittest.main()
