#!/usr/bin/env python3
"""Suite for scripts/demo_torture.py.

A demo is a claim about the product, so it needs the same discipline as
anything else that makes one. What is checked here:

  - the verdict CANNOT say PASS while any behaviour did not run. That is the
    one property the whole demo exists for, so it is driven directly, in
    every combination of states, rather than inferred from one live run.
  - the parallel behaviour reaches BOTH its halves. On a machine under the
    disk floor the PROVEN half is unreachable through the real capacity
    reading, which is exactly why demo_torture.behaviour_parallel takes an
    injectable capacity: an untested branch in a demo is a branch that will
    be broken the first day somebody has enough disk to reach it.
  - the two behaviours that do run here really run, against the real
    claim_store and the real receipt_door, killing a real process.

Python 3.9, standard library only. No em or en dashes anywhere.
"""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import demo_torture as DT  # noqa: E402

DEMO = os.path.join(HERE, "demo_torture.py")


def _outcome(name, state, summary="s"):
    return DT.Outcome(name, name, state, ["a line"], summary)


class TheVerdictRefusesToRoundUp(unittest.TestCase):
    """The honesty requirement, driven directly. A behaviour that never ran
    is not evidence, and no combination of the other two may buy it a pass."""

    def test_all_proven_is_the_only_pass(self):
        lines, code = DT.verdict_lines([_outcome("a", DT.PROVEN),
                                       _outcome("b", DT.PROVEN),
                                       _outcome("c", DT.PROVEN)])
        self.assertEqual(code, DT.EXIT_ALL_PROVEN)
        self.assertIn("VERDICT: PASS", lines[0])

    def test_one_no_data_is_never_a_pass(self):
        lines, code = DT.verdict_lines([_outcome("a", DT.PROVEN),
                                       _outcome("b", DT.PROVEN),
                                       _outcome("c", DT.NODATA,
                                                "the disk floor")])
        self.assertNotEqual(code, DT.EXIT_ALL_PROVEN)
        self.assertEqual(code, DT.EXIT_NOT_PROVEN)
        # The VERDICT LINE, not the whole block: the closing sentence
        # legitimately contains the word ("will not say PASS while one is
        # missing"), and forbidding the substring outright would forbid the
        # demo from explaining itself.
        self.assertNotIn("VERDICT: PASS", "\n".join(lines))
        self.assertIn("NOT PROVEN", lines[0])
        self.assertIn("the disk floor", "\n".join(lines))

    def test_broken_outranks_no_data(self):
        lines, code = DT.verdict_lines([_outcome("a", DT.BROKEN),
                                       _outcome("b", DT.NODATA)])
        self.assertEqual(code, DT.EXIT_BROKEN)
        self.assertIn("VERDICT: FAIL", lines[0])

    def test_broken_and_not_proven_do_not_share_an_exit_code(self):
        """A caller reading only the exit code must be able to tell "the
        product broke" from "one behaviour could not run here"."""
        self.assertNotEqual(DT.EXIT_BROKEN, DT.EXIT_NOT_PROVEN)
        self.assertNotEqual(DT.EXIT_ALL_PROVEN, DT.EXIT_NOT_PROVEN)


class TheParallelBehaviourReachesBothHalves(unittest.TestCase):

    def test_a_refusing_machine_is_no_data_naming_the_floor(self):
        out = DT.behaviour_parallel(
            capacity=(0, ["8 core(s), reserving 2, so 6 slot(s)",
                          "REFUSE: 6.6 GiB free is under the 8 GiB floor"]))
        self.assertEqual(out.state, DT.NODATA)
        self.assertIn("8 GiB floor", out.summary)
        self.assertIn("NEVER a pass".lower(), "\n".join(out.lines).lower())

    def test_a_machine_with_slots_actually_runs_two_workers(self):
        out = DT.behaviour_parallel(capacity=(4, ["4 slot(s), plenty free"]))
        self.assertEqual(out.state, DT.PROVEN,
                        "\n".join(out.lines))
        joined = "\n".join(out.lines)
        self.assertIn("P1", joined)
        self.assertIn("P2", joined)
        self.assertIn("at the same time", joined)


class TheKilledWorkerComesBack(unittest.TestCase):
    """Runs for real: a real subprocess takes a real claim and is really
    SIGKILLed."""

    def test_both_halves_hold(self):
        out = DT.behaviour_kill_recover()
        self.assertEqual(out.state, DT.PROVEN, "\n".join(out.lines))
        joined = "\n".join(out.lines)
        self.assertIn("a second one is refused", joined)
        self.assertIn("reclaimed_from=worker-doomed", joined)


class TheMisleadingGreenIsRefused(unittest.TestCase):

    def test_identical_exit_codes_get_different_verdicts(self):
        out = DT.behaviour_fake_green()
        self.assertEqual(out.state, DT.PROVEN, "\n".join(out.lines))
        joined = "\n".join(out.lines)
        self.assertIn("verdict on HONEST: verified", joined)
        self.assertIn("no-data", joined)
        self.assertIn("already passed before the work began", joined)


class TheCommandLine(unittest.TestCase):

    def test_torture_is_required(self):
        r = subprocess.run([sys.executable, "-B", DEMO],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, DT.EXIT_USAGE, r.stdout)
        self.assertIn("--torture", r.stdout)

    def test_a_real_run_never_exits_zero_while_a_behaviour_is_no_data(self):
        """The end-to-end version of the honesty requirement. This asserts
        a RELATIONSHIP, not a fixed code, because the right answer depends
        on the machine: with enough free disk all three run and 0 is
        correct; under the floor one is NO-DATA and 0 would be a lie."""
        r = subprocess.run([sys.executable, "-B", DEMO, "--torture"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=300)
        said_no_data = "NO-DATA" in r.stdout
        if said_no_data:
            self.assertNotEqual(r.returncode, DT.EXIT_ALL_PROVEN, r.stdout)
            self.assertNotIn("VERDICT: PASS", r.stdout)
        else:
            self.assertEqual(r.returncode, DT.EXIT_ALL_PROVEN, r.stdout)
            self.assertIn("VERDICT: PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()
