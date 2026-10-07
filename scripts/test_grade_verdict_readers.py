"""The loop's grade readers decide on the grade command's EXIT CODE and its LAST verdict line, nothing else.

WHAT IT PROTECTS (RR lane C, 2026-09-27, the rule lane F4 gave grade_lane.sh after an audit): a grade file is the
grader's own lines then `exit=N` (grade_one.sh), and any line starting PASS used to count, so a worker's printed PASS
above the real FAIL of a grade that exited 1 read as a pass. Two readers in lane C's files kept that shape:
  - unit_runner.py searched its round's lane.log for "PASS <build>" anywhere and ignored grade_lane.sh's exit code;
  - repair_wave.py took a build wave lane's first grade file holding any line starting PASS as its passing build.
Now: the runner takes a build only when grade_lane.sh exited 0 AND the log's last verdict line is exactly
"<sub> PASS <sub>-rN"; the repair wave takes a grade file only when its last line is exit=0 AND its last line starting
PASS or FAIL is exactly PASS. Entry points: unit_runner.py as a subprocess (stub bin, fake fan out, stub grader) and
repair_wave.py as __main__ through the contract suite's offline harness. Run: python3 -B scripts/test_grade_verdict_readers.py
"""
import json, os, shutil, sys, tempfile, unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_run_folder_identity as RF  # noqa: E402  the runner's stub bin fixture
import test_repair_wave_contract as C  # noqa: E402  the offline repair wave harness

QUICK_FANOUT = ("import json, sys\nargs = sys.argv[1:]\njobs = json.load(open(args[0]))\n"
                "for j in jobs:\n    open(j['out'], 'w').write('{\"edits\": [], \"tests\": []}')\n"
                "json.dump([{'id': j['id'], 'ok': True} for j in jobs], open(args[args.index('--results') + 1], 'w'))\n")


def runner_with_grader(test, grader_lines, grader_exit):
    """One round of the REAL runner whose grade stage prints grader_lines and exits grader_exit. Returns its STATUS."""
    f = RF.Fixture()
    test.addCleanup(f.cleanup)
    with open(os.path.join(f.code, "plugin", "runtime", "brother", "core", "or_fanout.py"), "w") as fh:
        fh.write(QUICK_FANOUT)
    with open(os.path.join(f.bin, "grade_lane.sh"), "w") as fh:
        # every build gets its COMPLETED grade file, as the real lane's grade_one.sh writes it (unit_runner.judged reads
        # it since 2026-10-03); these cases are about the lane's exit and last verdict, never an unjudged round
        fh.write("#!/bin/sh\nmkdir -p \"$1/grades\"\nfor b in \"$1\"/out/*-build.json; do [ -e \"$b\" ] || continue; "
                 "printf 'FAIL: stub\\nexit=1\\n' > \"$1/grades/$(basename \"$b\" -build.json).txt\"; done\n"
                 + "".join("echo '%s'\n" % l for l in grader_lines) + "exit %d\n" % grader_exit)
    os.chmod(os.path.join(f.bin, "grade_lane.sh"), 0o755)
    f.env.pop("FAKE_EV_STOP", None)
    f.env.update(FANOUT_TIMEOUT_S="1")
    p = f.start()
    out, _ = p.communicate(timeout=120)
    folders = f.folders()
    status = RF._read(os.path.join(folders[0], "STATUS")) if folders else None
    return status or "", out


class TheRunnerReadsTheGradersExitAndLastVerdict(unittest.TestCase):
    def test_control_a_real_pass_is_taken(self):
        status, out = runner_with_grader(self, ["Z9.1 PASS Z9.1-r0", "LEDGER appended"], 0)
        # passed the grader; no probe tool here, so READY and the landing gates decide (owner 2026-09-27: a silent probe
        # no longer blocks; READY-UNPROBED retired), and the build named is this sub unit's own
        self.assertEqual(status.split(" ", 1)[0], "READY", status + out[-600:])
        self.assertIn("/out/Z9.1-r0-build.json", status, status)

    def test_a_pass_line_from_a_grader_that_exited_one_is_not_a_pass(self):
        status, out = runner_with_grader(self, ["Z9.1 PASS Z9.1-r0"], 1)
        self.assertTrue(status.startswith("EXHAUSTED after 1 rounds; best grader pass: None"), status + out[-600:])

    def test_an_earlier_pass_line_under_a_later_verdict_is_not_a_pass(self):
        status, out = runner_with_grader(self, ["Z9.1 PASS Z9.1-r0", "Z9.1 NO-PASS"], 0)
        self.assertTrue(status.startswith("EXHAUSTED after 1 rounds; best grader pass: None"), status + out[-600:])

    def test_a_pass_that_is_not_this_sub_units_own_build_is_not_a_pass(self):
        status, out = runner_with_grader(self, ["Z9.1 PASS ../../elsewhere/Z9.1-r0"], 0)
        self.assertTrue(status.startswith("EXHAUSTED after 1 rounds; best grader pass: None"), status + out[-600:])


class TheRepairWaveReadsAGradeFilesExitAndLastVerdict(unittest.TestCase):
    def wave(self, grade_text):
        root = Path(tempfile.mkdtemp(prefix="grade-verdict-"))
        self.addCleanup(shutil.rmtree, str(root), True)
        bw, pw, nw = C.waves(root)
        (bw / "grades/F1-r0.txt").write_text(grade_text)
        code, calls, out = C.run_wave([str(bw), str(pw), str(nw), "1"])
        return code, ("fanout", 0) in calls, out

    def test_control_a_real_pass_is_repaired(self):
        code, dispatched, out = self.wave("GREEN tests\nPASS\nexit=0\n")
        self.assertTrue(dispatched, out[-600:])

    def test_a_pass_line_in_a_grade_that_exited_one_is_not_a_passing_build(self):
        # the last verdict IS PASS, so only the exit code can refuse it (the grader crashed after printing its verdict)
        code, dispatched, out = self.wave("GREEN tests\nPASS\nexit=1\n")
        self.assertFalse(dispatched, "a grade that exited 1 was taken as the lane's passing build: " + out[-600:])

    def test_an_earlier_pass_line_under_a_later_fail_is_not_a_passing_build(self):
        # a line exactly PASS (a worker's own test output) above the grader's real FAIL; exit 0, so only the last verdict refuses
        code, dispatched, out = self.wave("PASS\nFAIL: tests red\nexit=0\n")
        self.assertFalse(dispatched, "an earlier PASS line outranked the grade's last verdict: " + out[-600:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
