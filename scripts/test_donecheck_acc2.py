#!/usr/bin/env python3
"""donecheck_acc2's two modes through main(), with run() stubbed (no gate is run): the plain mode is the original
benchmark (verdicts identical AND under half the wall time); --correctness judges only identical verdicts and never
claims the benchmark. One condition per case. Run: python3 -B scripts/test_donecheck_acc2.py"""
import io, os, sys, unittest
from contextlib import redirect_stdout
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donecheck_acc2 as D


def stub(serial, parallel, done=(True, True), widths=(1, 4)):
    runs = {1: serial, 4: parallel}
    def run(jobs):
        wall, verdicts = runs[jobs]
        w = widths[0] if jobs == 1 else widths[1]
        out = "pass 2   fail 0   no-data 0\n" if w is None else "width %d\n\npass 2   fail 0   no-data 0\n" % w
        return wall, verdicts, done[0] if jobs == 1 else done[1], out
    return run


class Modes(unittest.TestCase):
    def go(self, run, argv):
        saved = D.run; D.run = run
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                rc = D.main(argv)
        finally:
            D.run = saved
        return rc, out.getvalue()

    SAME = {"a": ("PASS", 0), "b": ("FAIL", 1)}

    def test_correctness_passes_identical_verdicts_whatever_the_wall_time(self):
        rc, out = self.go(stub((100, self.SAME), (90, dict(self.SAME))), ["--correctness"])
        self.assertEqual(rc, 0, out)
        self.assertIn("no benchmark claim", out)
        self.assertIn("recorded, not judged", out)

    def test_correctness_fails_a_differing_verdict(self):
        rc, out = self.go(stub((100, self.SAME), (40, {"a": ("PASS", 0), "b": ("PASS", 0)})), ["--correctness"])
        self.assertEqual(rc, 1, out)

    def test_correctness_reads_no_data_without_a_summary(self):
        rc, out = self.go(stub((100, self.SAME), (40, dict(self.SAME)), done=(True, False)), ["--correctness"])
        self.assertEqual(rc, 2, out)

    def test_a_side_by_side_run_forced_to_width_one_is_no_data_in_both_modes(self):
        # 2026-10-04: suite_env's BROTHER_JEV_STATE_DIR made the gate run width 1, so the "side by side" run was serial
        for argv in (["--correctness"], []):
            rc, out = self.go(stub((100, self.SAME), (40, dict(self.SAME)), widths=(1, 1)), argv)
            self.assertEqual(rc, 2, out)
            self.assertIn("NO-DATA: the runs did not run at width 1 and above 1", out)

    def test_the_refusal_names_what_forced_the_width(self):
        def run(jobs):
            out = "width 1%s\n\npass 2   fail 0   no-data 0\n" % ("" if jobs == 1 else " (forced by BROTHER_JEV_STATE_DIR)")
            return 100, dict(self.SAME), True, out
        rc, out = self.go(run, ["--correctness"])
        self.assertEqual(rc, 2, out)
        self.assertIn("forced by BROTHER_JEV_STATE_DIR", out)

    def test_a_gate_that_reports_no_width_is_no_data(self):
        rc, out = self.go(stub((100, self.SAME), (40, dict(self.SAME)), widths=(1, None)), ["--correctness"])
        self.assertEqual(rc, 2, out)

    def test_a_serial_run_not_at_width_one_is_no_data(self):
        rc, out = self.go(stub((100, self.SAME), (40, dict(self.SAME)), widths=(4, 4)), ["--correctness"])
        self.assertEqual(rc, 2, out)

    def test_the_jev_state_variable_never_reaches_the_gate(self):
        import subprocess as sp
        seen = {}
        def fake(argv, cwd=None, env=None, capture_output=None, text=None):
            seen.update(env)
            return sp.CompletedProcess(argv, 0, "width 1\n\npass 1   fail 0   no-data 0\n", "")
        saved = D.subprocess.run; D.subprocess.run = fake
        os.environ["BROTHER_JEV_STATE_DIR"] = "/tmp/jev-x"
        try:
            D.run(1)
        finally:
            D.subprocess.run = saved; os.environ.pop("BROTHER_JEV_STATE_DIR", None)
        self.assertNotIn("BROTHER_JEV_STATE_DIR", seen)
        self.assertEqual(seen.get("REQUIRED_FAST_JOBS"), "1")

    def test_the_plain_mode_still_fails_on_wall_time_alone(self):
        rc, out = self.go(stub((100, self.SAME), (90, dict(self.SAME))), [])
        self.assertEqual(rc, 1, out)

    def test_the_plain_mode_passes_only_under_half(self):
        rc, out = self.go(stub((100, self.SAME), (40, dict(self.SAME))), [])
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
