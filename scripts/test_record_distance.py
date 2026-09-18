#!/usr/bin/env python3
"""Tests for record_distance.py. Git is always a fake `run`, never a real
subprocess: no network, no real repository, temp dirs only for the argparse
plumbing that needs a path string."""
import tempfile
import unittest

import record_distance as rd


class Proc:
    """Stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def rev_list_run(behind, ahead):
    """A fake `run` for a single rev-list call returning behind/ahead."""
    def run(cmd):
        assert cmd[3] == "rev-list"
        return Proc(0, "%d\t%d\n" % (behind, ahead))
    return run


class MeasureDistance(unittest.TestCase):
    def test_zero_behind_zero_ahead(self):
        self.assertEqual(rd.measure_distance("R", "hub/main", "HEAD",
                                              run=rev_list_run(0, 0)), (0, 0))

    def test_zero_behind_nonzero_ahead(self):
        # Named explicitly by the brief: ahead-only must not read as safe.
        self.assertEqual(rd.measure_distance("R", "hub/main", "HEAD",
                                              run=rev_list_run(0, 4)), (0, 4))

    def test_686_behind_from_the_real_record(self):
        self.assertEqual(rd.measure_distance("R", "hub/main", "HEAD",
                                              run=rev_list_run(686, 0)), (686, 0))

    def test_5266_behind_from_the_real_record(self):
        self.assertEqual(rd.measure_distance("R", "hub/main", "HEAD",
                                              run=rev_list_run(5266, 0)), (5266, 0))

    def test_diverged_both_nonzero(self):
        self.assertEqual(rd.measure_distance("R", "hub/main", "HEAD",
                                              run=rev_list_run(12, 3)), (12, 3))

    def test_nonzero_exit_is_distance_unknown(self):
        run = lambda cmd: Proc(128, "", "unknown revision or path not in the working tree")
        with self.assertRaises(rd.DistanceUnknown):
            rd.measure_distance("R", "no/such-remote", "HEAD", run=run)

    def test_success_with_empty_output_is_distance_unknown(self):
        run = lambda cmd: Proc(0, "", "")
        with self.assertRaises(rd.DistanceUnknown):
            rd.measure_distance("R", "hub/main", "HEAD", run=run)

    def test_success_with_non_numeric_output_is_distance_unknown(self):
        run = lambda cmd: Proc(0, "not\tnumbers\n", "")
        with self.assertRaises(rd.DistanceUnknown):
            rd.measure_distance("R", "hub/main", "HEAD", run=run)

    def test_subprocess_raising_is_distance_unknown(self):
        def run(cmd):
            raise OSError("git not found")
        with self.assertRaises(rd.DistanceUnknown):
            rd.measure_distance("R", "hub/main", "HEAD", run=run)


class CurrentBranch(unittest.TestCase):
    def test_resolves_the_branch_name(self):
        run = lambda cmd: Proc(0, "feat/1.0.20-orchestration-control-plane\n", "")
        self.assertEqual(rd.current_branch("R", run=run),
                          "feat/1.0.20-orchestration-control-plane")

    def test_detached_head_is_distance_unknown(self):
        run = lambda cmd: Proc(128, "", "fatal: ref HEAD is not a symbolic ref")
        with self.assertRaises(rd.DistanceUnknown):
            rd.current_branch("R", run=run)

    def test_empty_name_is_distance_unknown(self):
        run = lambda cmd: Proc(0, "\n", "")
        with self.assertRaises(rd.DistanceUnknown):
            rd.current_branch("R", run=run)


class Evaluate(unittest.TestCase):
    def test_missing_disposition_is_refused(self):
        verdict, msg = rd.evaluate(0, 0, None)
        self.assertEqual(verdict, "REFUSED")

    def test_unknown_disposition_is_refused(self):
        verdict, msg = rd.evaluate(0, 0, "MAYBE_LATER")
        self.assertEqual(verdict, "REFUSED")

    def test_zero_behind_zero_ahead_still_needs_a_disposition_to_accept(self):
        # The brief's named edge: zero distance is not a free pass.
        refused, _ = rd.evaluate(0, 0, None)
        accepted, _ = rd.evaluate(0, 0, "UPDATE")
        self.assertEqual(refused, "REFUSED")
        self.assertEqual(accepted, "ACCEPTED")

    def test_update_disposition_accepted(self):
        verdict, msg = rd.evaluate(686, 0, "UPDATE")
        self.assertEqual(verdict, "ACCEPTED")
        self.assertIn("686", msg)

    def test_reproduce_disposition_accepted(self):
        verdict, _ = rd.evaluate(5266, 0, "REPRODUCE")
        self.assertEqual(verdict, "ACCEPTED")

    def test_historical_with_no_reason_is_refused(self):
        verdict, msg = rd.evaluate(40, 0, "HISTORICAL")
        self.assertEqual(verdict, "REFUSED")

    def test_historical_with_blank_reason_is_refused(self):
        verdict, _ = rd.evaluate(40, 0, "HISTORICAL", reason="   ")
        self.assertEqual(verdict, "REFUSED")

    def test_historical_with_reason_is_accepted(self):
        verdict, msg = rd.evaluate(40, 0, "HISTORICAL", reason="pinned for a benchmark fixture")
        self.assertEqual(verdict, "ACCEPTED")
        self.assertIn("pinned for a benchmark fixture", msg)

    def test_diverged_names_diverged_in_the_message(self):
        _, msg = rd.evaluate(12, 3, "UPDATE")
        self.assertIn("diverged", msg)

    def test_not_diverged_omits_the_word(self):
        _, msg = rd.evaluate(12, 0, "UPDATE")
        self.assertNotIn("diverged", msg)


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_answered_exit_zero(self):
        def run(cmd):
            if "symbolic-ref" in cmd:
                return Proc(0, "main\n", "")
            return Proc(0, "686\t0\n", "")
        rc = rd.main(["--repo", self.tmp, "--remote", "hub/main",
                      "--disposition", "UPDATE"], run=run)
        self.assertEqual(rc, 0)

    def test_refused_missing_disposition_exit_one(self):
        def run(cmd):
            if "symbolic-ref" in cmd:
                return Proc(0, "main\n", "")
            return Proc(0, "5266\t0\n", "")
        rc = rd.main(["--repo", self.tmp, "--remote", "hub/main"], run=run)
        self.assertEqual(rc, 1)

    def test_refused_historical_with_no_reason_exit_one(self):
        def run(cmd):
            if "symbolic-ref" in cmd:
                return Proc(0, "main\n", "")
            return Proc(0, "40\t0\n", "")
        rc = rd.main(["--repo", self.tmp, "--remote", "hub/main",
                      "--disposition", "HISTORICAL"], run=run)
        self.assertEqual(rc, 1)

    def test_no_remote_is_no_data_exit_two(self):
        def run(cmd):
            if "symbolic-ref" in cmd:
                return Proc(0, "main\n", "")
            return Proc(128, "", "fatal: no such remote")
        rc = rd.main(["--repo", self.tmp, "--remote", "ghost/main",
                      "--disposition", "UPDATE"], run=run)
        self.assertEqual(rc, 2)

    def test_detached_head_with_no_explicit_local_is_no_data_exit_two(self):
        def run(cmd):
            if "symbolic-ref" in cmd:
                return Proc(128, "", "fatal: ref HEAD is not a symbolic ref")
            return Proc(0, "0\t0\n", "")
        rc = rd.main(["--repo", self.tmp, "--remote", "hub/main",
                      "--disposition", "UPDATE"], run=run)
        self.assertEqual(rc, 2)

    def test_detached_head_with_explicit_local_still_answers(self):
        def run(cmd):
            self.assertNotIn("symbolic-ref", cmd)
            return Proc(0, "3\t0\n", "")
        rc = rd.main(["--repo", self.tmp, "--remote", "hub/main",
                      "--local", "a1b2c3d", "--disposition", "REPRODUCE"], run=run)
        self.assertEqual(rc, 0)

    def test_git_success_empty_output_is_no_data_never_zero_behind(self):
        def run(cmd):
            if "symbolic-ref" in cmd:
                return Proc(0, "main\n", "")
            return Proc(0, "", "")
        rc = rd.main(["--repo", self.tmp, "--remote", "hub/main",
                      "--disposition", "UPDATE"], run=run)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
