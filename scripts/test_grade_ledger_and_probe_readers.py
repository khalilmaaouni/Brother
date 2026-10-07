#!/usr/bin/env python3
"""Two readers of a grade left behind by the grader lane (F4, 2026-09-27), each driven through its real script.

1. grade_lane.sh's ledger append. `unit_ledger.py --append-run ... 2>&1 | tail -1 || echo "LEDGER NO-DATA..."` took
   the pipe's status, which is tail's, so a failed append printed its last line and never said NO-DATA: the ledger
   readers went blind to that grade with nothing on the page. The last line is kept and unit_ledger's OWN exit decides.
2. probe_wave.py's choice of which lanes to probe. It counted a lane as graded PASS when ANY line of a grade file
   started with PASS, so a worker's own PASS above the grader's real FAIL, a PASS with a nonzero exit, and a line
   reading PASSED all bought paid adversary probes. It now asks unit_ledger.grade_passed, which reads the rule
   grade_lane.sh reads (last line exactly exit=0, last line starting PASS or FAIL exactly PASS). The parity class
   runs grade_lane.sh and grade_passed over the same grade files, so the two readers cannot drift apart unseen.

Every fixture isolates one condition. The installed tools grade_lane.sh calls live under a scratch HOME.
Run: python3 -B scripts/test_grade_ledger_and_probe_readers.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
import unit_ledger  # noqa: E402

# (grade file text, does it pass?) Each failing row differs from a passing row in one condition.
GRADES = [
    ("PASS\nexit=0\n", True),
    ("RED-WITHOUT-CODE   yes\nPASS\nexit=0\n", True),
    ("FAIL: 2 patch problems\nrepaired\nPASS\nexit=0\n", True),
    ("PASS\nexit=0", True),                                       # no final newline: tail still reads exit=0
    ("PASS\nFAIL: the last word\nexit=0\n", False),               # a later FAIL is the verdict
    ("PASS\nexit=1\n", False),                                    # the grader refused
    ("PASSED by the worker\nexit=0\n", False),                    # a verdict line that is not exactly PASS
    ("PASS \nexit=0\n", False),                                   # trailing space: not exactly PASS
    ("exit=0\n", False),                                          # no verdict line at all
    ("PASS\nexit=0\n\n", False),                                  # the last line is blank, not exit=0
    ("PASS\r\nexit=0\r\n", False),                                # carriage returns: bash reads "exit=0\r"
    ("PASS\nexit=0\nnote\n", False),                              # exit=0 is not the LAST line
    ("PASS\n\x00\nexit=0\n", False),                              # a NUL makes grep answer "Binary file ... matches"
]


def bash(argv, home, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
    env["HOME"] = home
    return subprocess.run(["bash"] + argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


class Box(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="grade-readers-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = os.path.join(self.d, "home")
        self.bin = os.path.join(self.home, ".claude", "bin")
        os.makedirs(self.bin)
        self.w = os.path.join(self.d, "wave")
        os.makedirs(os.path.join(self.w, "out"))
        os.makedirs(os.path.join(self.w, "grades"))

    def grade(self, name, text):
        with open(os.path.join(self.w, "out", name + "-build.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(self.w, "grades", name + ".txt"), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return os.path.join(self.w, "grades", name + ".txt")

    def lane(self, lane="X"):
        return bash([os.path.join(LOOP, "grade_lane.sh"), self.w, lane], self.home)


class GradeLaneLedgerExit(Box):
    def ledger_stub(self, out, code):
        with open(os.path.join(self.bin, "unit_ledger.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stdout.write(%r)\nsys.exit(%d)\n" % (out, code))

    def test_a_failed_append_says_no_data(self):
        self.grade("X-r0", "PASS\nexit=0\n")
        self.ledger_stub("unit_ledger: the runs folder cannot be read\n", 3)
        r = self.lane()
        self.assertIn("LEDGER NO-DATA", r.stdout, r.stdout + r.stderr)

    def test_a_failed_append_keeps_its_last_line_and_the_grade_exit(self):
        self.grade("X-r0", "PASS\nexit=0\n")
        self.ledger_stub("first\nunit_ledger: the runs folder cannot be read\n", 3)
        r = self.lane()
        self.assertEqual((r.returncode, "X PASS X-r0" in r.stdout), (0, True), r.stdout + r.stderr)
        self.assertIn("unit_ledger: the runs folder cannot be read", r.stdout)
        self.assertNotIn("first", r.stdout)

    def test_a_failed_append_on_a_lane_with_no_pass_says_no_data_and_keeps_exit_1(self):
        self.grade("X-r0", "FAIL: refused\nexit=1\n")
        self.ledger_stub("unit_ledger: the runs folder cannot be read\n", 3)
        r = self.lane()
        self.assertEqual((r.returncode, "X NO-PASS" in r.stdout, "LEDGER NO-DATA" in r.stdout), (1, True, True), r.stdout)

    def test_control_a_good_append_prints_only_its_last_line(self):
        self.grade("X-r0", "PASS\nexit=0\n")
        self.ledger_stub("first\nLEDGER appended 1 row\n", 0)
        r = self.lane()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LEDGER appended 1 row", r.stdout)
        self.assertNotIn("NO-DATA", r.stdout)
        self.assertNotIn("first", r.stdout)

    def test_control_no_installed_ledger_still_says_no_data(self):
        self.grade("X-r0", "PASS\nexit=0\n")
        self.assertIn("LEDGER NO-DATA: unit_ledger.py not installed", self.lane().stdout)


class TheTwoReadersAgree(Box):
    """grade_lane.sh (the rule of record) and unit_ledger.grade_passed read every grade file the same way."""

    def test_grade_passed_answers_the_table(self):
        for i, (text, want) in enumerate(GRADES):
            with self.subTest(text=text):
                self.assertIs(unit_ledger.grade_passed(self.grade("T%d-r0" % i, text)), want)

    def test_grade_lane_answers_the_same_table(self):
        for i, (text, want) in enumerate(GRADES):
            with self.subTest(text=text):
                self.grade("T%d-r0" % i, text)
                self.assertEqual(self.lane("T%d" % i).returncode == 0, want)

    def test_an_unreadable_grade_file_is_not_a_pass(self):
        self.assertIs(unit_ledger.grade_passed(os.path.join(self.w, "grades", "absent.txt")), False)

    def test_a_grade_file_that_is_not_utf8_is_not_a_pass(self):
        g = self.grade("U-r0", "")
        with open(g, "wb") as fh:
            fh.write(b"\xff\xfe\nPASS\nexit=0\n")
        self.assertIs(unit_ledger.grade_passed(g), False)


class TheLedgerCountsOnlyTheSamePass(Box):
    """X3 finding 4 (Codex cross lane review, 2026-09-27): ledger ingestion read a grade with grade_of(), which ignores
    the exit record, so the transcript grade_lane.sh refuses (PASS, then exit=1) became a ledger PASS and a worker
    reward. Each grade file of the table goes through `unit_ledger.py --append-run`, the call grade_lane.sh makes, and
    worker_mix.arm_stats reads the ledger it wrote: a ledger PASS and a reward exactly where the grader's rule passes."""

    def ledger(self, text, n=0):
        run = os.path.join(self.d, "runs%d" % n, "L1.1-000001")
        rd = os.path.join(run, "round0")
        os.makedirs(os.path.join(rd, "grades"))
        with open(os.path.join(rd, "jobs.json"), "w", encoding="utf-8") as fh:
            json.dump([{"id": "L1.1-r0", "model": "deepseek"}], fh)
        with open(os.path.join(rd, "results.json"), "w", encoding="utf-8") as fh:
            json.dump([{"id": "L1.1-r0", "model": "deepseek", "actual_model": "deepseek/v", "ok": True}], fh)
        with open(os.path.join(rd, "grades", "L1.1-r0.txt"), "w", encoding="latin-1" if "\xff" in text else "utf-8",
                  newline="") as fh:
            fh.write(text)
        out = os.path.join(self.d, "unit-ledger-%d.jsonl" % n)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        env["HOME"] = self.home
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "unit_ledger.py"), "--append-run", run, "--out", out,
                            "--ledger", os.path.join(self.d, "no-payments.jsonl"), "--plan", os.path.join(self.d, "no-plan.json")],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        import worker_mix
        return [row["grade"] for row in unit_ledger.last_rows(out)], worker_mix.arm_stats(out)

    def test_the_ledger_passes_and_rewards_exactly_what_the_grader_passes(self):
        for i, (text, want) in enumerate(GRADES):
            with self.subTest(text=text):
                grades, rewards = self.ledger(text, i)
                self.assertEqual(grades[0] == "PASS", want, grades)
                self.assertEqual(rewards.get("deepseek", [0, 0])[0], 1 if want else 0, rewards)

    def test_a_pass_the_grader_refused_is_no_data_never_a_reward_or_a_fail(self):
        grades, rewards = self.ledger("PASS\nexit=1\n")
        self.assertEqual((grades, rewards), (["NO-DATA"], {}))

    def test_a_grade_file_that_is_not_utf8_is_no_data_and_the_append_still_lands(self):
        # the read raised UnicodeDecodeError out of the append, so the whole run's rows were lost, not only this grade
        grades, rewards = self.ledger(b"\xff\xfe\nPASS\nexit=0\n".decode("latin-1"))
        self.assertEqual((grades, rewards), (["NO-DATA"], {}))


class ProbeWaveProbesOnlyAGradedPass(Box):
    """probe_wave.py at its entry point, in a temp root whose plan names no spec, so no job is written or sent: the
    one row per lane it prints is the set of lanes it chose to probe."""

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.d, "root")
        os.makedirs(os.path.join(self.root, "docs", "plan"))
        with open(os.path.join(self.root, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "X", "sub_units": ["X.1"]}]}, fh)
        with open(os.path.join(self.home, ".brothersbe-private-names"), "w", encoding="utf-8") as fh:
            fh.write("zz-fixture-private-token\n")

    def probed(self, text):
        self.grade("X.1-r0", text)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        env["HOME"] = self.home
        env["BROTHER_PROBES"] = "on"   # the probe machinery is tested here; it is off by default since plan E step 2c
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "probe_wave.py"), self.w, os.path.join(self.d, "pw")],
                           cwd=self.root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return any(line.split()[:2] == ["X.1", "X.1-r0"] for line in r.stdout.splitlines())

    def test_an_earlier_pass_above_a_final_fail_is_not_probed(self):
        self.assertFalse(self.probed("PASS\nFAIL: the last word\nexit=0\n"))

    def test_a_pass_with_a_nonzero_exit_is_not_probed(self):
        self.assertFalse(self.probed("PASS\nexit=1\n"))

    def test_a_line_reading_passed_is_not_probed(self):
        self.assertFalse(self.probed("PASSED by the worker\nexit=0\n"))

    def test_control_the_graders_own_pass_at_exit_0_is_probed(self):
        self.assertTrue(self.probed("RED-WITHOUT-CODE   yes\nPASS\nexit=0\n"))


if __name__ == "__main__":
    unittest.main()
