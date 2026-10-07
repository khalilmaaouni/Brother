"""proof_pair.sh pins the launch worktree's upstream and refuses a different expected one before anything runs.

Astra review of the practice proof design (2026-10-05): the lander pushes closures and reconciles to whatever upstream
the tree carries, so a practice pair launched from a tree that tracks the release line would push there. Each case
builds a temp git repository with a known upstream and a throwaway HOME holding a minimal launch-env.sh, runs the
real proof_pair.sh with --rehearsal-free arguments, and reads its first refusal. Nothing reaches a loop: every case
stops at the pin or at the next refusal after it, which these tests name.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PAIR = os.path.join(HERE, "loop", "proof_pair.sh")


class PairPinsItsDestination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pair-upstream-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        intake = os.path.join(self.home, ".claude", "evidence", "loop-intake")
        os.makedirs(intake)
        with open(os.path.join(intake, "launch-env.sh"), "w") as fh:
            fh.write("export BROTHER_CHECKER=sentinel\n")   # the next refusal after the pin, so nothing ever launches
        self.wt = os.path.join(self.tmp, "wt")
        remote = os.path.join(self.tmp, "remote.git")
        git = lambda *a, cwd=None: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                                                   "-c", "commit.gpgsign=false", *a], cwd=cwd, capture_output=True,
                                                  text=True, check=True)
        git("init", "-q", "--bare", remote)
        git("init", "-q", "-b", "practice/p", self.wt)
        git("commit", "-q", "--allow-empty", "-m", "a", cwd=self.wt)
        git("remote", "add", "hub", remote, cwd=self.wt)
        git("push", "-q", "hub", "practice/p:practice/p", cwd=self.wt)
        git("branch", "--set-upstream-to=hub/practice/p", cwd=self.wt)

    def run_pair(self, expected=None):
        env = {"HOME": self.home, "PATH": os.environ["PATH"], "USER": os.environ.get("USER", "u"),
               "BROTHER_LAUNCH_WORKTREE": self.wt}
        if expected is not None:
            env["BROTHER_EXPECTED_UPSTREAM"] = expected
        r = subprocess.run(["bash", PAIR], cwd=self.wt, env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_a_different_expected_upstream_is_refused_first(self):
        rc, out = self.run_pair("hub/loop/run-2026-09-30")
        self.assertEqual(rc, 2, out)
        self.assertIn("tracks hub/practice/p, not the expected hub/loop/run-2026-09-30", out)

    def test_the_matching_upstream_passes_the_pin(self):
        rc, out = self.run_pair("hub/practice/p")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("not the expected", out)
        self.assertIn("BROTHER_CHECKER is set", out)   # it reached the next refusal, past the pin

    def test_no_upstream_is_refused(self):
        subprocess.run(["git", "branch", "--unset-upstream"], cwd=self.wt, capture_output=True, check=True)
        rc, out = self.run_pair()
        self.assertEqual(rc, 2, out)
        self.assertIn("has no upstream", out)

    def test_the_pin_is_put_into_the_pairs_environment(self):
        # review 2026-10-05: E is built with env -i, so this put is the only wire from the pair to the lander;
        # deleting it left every other case green
        with open(PAIR, encoding="utf-8") as fh:
            self.assertIn('put BROTHER_EXPECTED_UPSTREAM "$UP"', fh.read())

    def test_a_test_environment_never_inherits_the_pin(self):
        # review 2026-10-05: suites run under suite_env inside a pair; a pinned upstream broke a lander fixture there
        import sys
        sys.path.insert(0, os.path.join(HERE, "loop"))
        import loop_switches
        self.assertNotIn("BROTHER_EXPECTED_UPSTREAM", loop_switches.drop_run_knobs({"BROTHER_EXPECTED_UPSTREAM": "hub/x"}))

    def test_the_reconcile_reads_the_refusal_as_a_gate_verdict(self):
        # review 2026-10-05: without the pattern, the reconcile push retried the refusal three times as transport
        with open(os.path.join(HERE, "loop", "loop_pass.sh"), encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if "pre-push: REFUSED|^BLOCK |rejected" in l]
        self.assertEqual(len(lines), 2, lines)
        for l in lines:
            self.assertIn("^REFUSED: the upstream", l)


if __name__ == "__main__":
    unittest.main()
