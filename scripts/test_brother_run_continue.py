"""brother_run.py's --continue: automatic unfinished-run discovery.

A user who types the door command with no outcome should resume their
crashed work without ever knowing run directories exist. This drives that
through the real command line, exactly as test_brother_run.py drives
--resume: no network, no real claude, the decomposer and worker are the
same tiny stub scripts DOOR_MODEL_CMD/MODEL_WORKER_CMD already exist for.

The primary scenario reuses scripts/test_product_acceptance.py's own Area 3
crash rig (kill brother_run right after the first unit integrates and the
second is durably claimed) rather than re-implementing it: that rig is
already the proven way to land a live claim under a dead owner.
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_brother_run as tbr  # noqa: E402
import product_acceptance as pa  # noqa: E402

BROTHER_RUN = tbr.BROTHER_RUN


def _leave_unfinished(scratch, runs_root, cwd, outcome, unit_id, filename):
    """Run brother_run.py against `cwd` with a model that always fails, so
    the door succeeds (the run directory and its target.json get written)
    but the one unit never reaches DONE. A cheap, hermetic stand-in for a
    genuine crash when the test only needs an "unfinished run" fixture,
    not the crash mechanics themselves."""
    decomposer_body = """
        import json, sys
        sys.stdin.read()
        print(json.dumps([
            {"id": %r, "objective": "create a file",
             "done_check": "test -f %s", "writes": [%r], "deps": []},
        ]))
    """ % (unit_id, filename, filename)
    env = dict(os.environ)
    env["DOOR_MODEL_CMD"] = "%s %s" % (
        sys.executable, tbr.write_stub(scratch, "decomposer.py", decomposer_body))
    env["MODEL_WORKER_CMD"] = "%s %s" % (
        sys.executable, tbr.write_stub(scratch, "failing_model.py", tbr.FAILING_MODEL))
    return tbr.sh([sys.executable, BROTHER_RUN, outcome,
                  "--cwd", cwd, "--runs-root", runs_root], env=env)


class ContinueResumesACrashedRun(unittest.TestCase):
    """The spec scenario: kill brother_run after the first unit integrates
    and the second is durably claimed, simulate the dead owner's lease
    elapsing, then --continue with NO run directory named finds it by
    target repo, resumes, and completes."""

    def test_continue_with_no_argument_finds_and_resumes_by_target_repo(self):
        rig, fail_verdict, fail_evidence = pa._run_and_kill_mid_second_unit(
            "test-continue-crash-")
        self.assertIsNotNone(rig, fail_evidence)

        # LEGITIMATE TEST SURGERY, same as area_2/area_3: simulate the
        # lease's TTL having elapsed rather than waiting out the real 20
        # minutes.
        pa._edit_expires_at(rig["claims_path"], "A2", time.time() - 5)

        proc = tbr.sh([sys.executable, BROTHER_RUN, "--continue",
                      "--cwd", rig["repo"], "--runs-root", rig["tmp"]],
                     env=rig["env"])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("brother_run: resuming", out, out)
        self.assertIn("two files exist, the second after the first", out, out)

        self.assertTrue(os.path.exists(os.path.join(rig["repo"], "one.txt")), out)
        self.assertTrue(os.path.exists(os.path.join(rig["repo"], "two.txt")), out)
        self.assertEqual(pa._merge_ids(rig["repo"]), ["A1", "A2"])


class ContinueIgnoresOtherRepos(unittest.TestCase):
    def test_second_unfinished_run_in_different_target_repo_is_not_offered(self):
        runs_root = tempfile.mkdtemp(prefix="continue-cross-repo-runs-")
        scratch = tempfile.mkdtemp(prefix="continue-cross-repo-scratch-")
        repo1 = tbr.make_repo(tempfile.mkdtemp(prefix="continue-cross-repo-r1-"))
        repo2 = tbr.make_repo(tempfile.mkdtemp(prefix="continue-cross-repo-r2-"))

        proc1 = _leave_unfinished(scratch, runs_root, repo1,
                                  "repo one's file exists", "F1", "f1.txt")
        self.assertNotEqual(proc1.returncode, 0, proc1.stdout + proc1.stderr)
        proc2 = _leave_unfinished(scratch, runs_root, repo2,
                                  "repo two's file exists", "F1", "f1.txt")
        self.assertNotEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)

        # Only the discovery routing is under test here (full completion on
        # a genuine crash is ContinueResumesACrashedRun's job), so the
        # resume attempt is left on the same deterministic failing model:
        # cheap, and still fully hermetic (no network).
        env = dict(os.environ)
        env["MODEL_WORKER_CMD"] = "%s %s" % (
            sys.executable, tbr.write_stub(scratch, "failing2.py", tbr.FAILING_MODEL))
        proc = tbr.sh([sys.executable, BROTHER_RUN, "--continue",
                      "--cwd", repo1, "--runs-root", runs_root], env=env)
        out = proc.stdout + proc.stderr
        self.assertIn("brother_run: resuming \"repo one's file exists\"",
                     out, out)
        self.assertNotIn("repo two's file exists", out, out)


class ContinueWithMultipleUnfinishedRuns(unittest.TestCase):
    def test_multiple_unfinished_runs_produce_a_numbered_choice(self):
        runs_root = tempfile.mkdtemp(prefix="continue-multi-runs-")
        scratch = tempfile.mkdtemp(prefix="continue-multi-scratch-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="continue-multi-repo-"))

        # Distinct unit ids: both runs target the SAME repo, and loop_bridge
        # names a lane branch after the unit id alone (lane/<id>), so two
        # runs sharing an id in one repo would collide on that branch.
        proc1 = _leave_unfinished(scratch, runs_root, repo,
                                  "the first unfinished outcome", "F1", "one-a.txt")
        self.assertNotEqual(proc1.returncode, 0, proc1.stdout + proc1.stderr)
        proc2 = _leave_unfinished(scratch, runs_root, repo,
                                  "the second unfinished outcome", "F2", "one-b.txt")
        self.assertNotEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)

        # The bare listing never touches loop_bridge (it returns before
        # that), so no MODEL_WORKER_CMD is needed for this call.
        bare = tbr.sh([sys.executable, BROTHER_RUN, "--continue",
                       "--cwd", repo, "--runs-root", runs_root])
        out = bare.stdout + bare.stderr
        self.assertNotEqual(bare.returncode, 0, out)
        self.assertIn("1. the first unfinished outcome", out, out)
        self.assertIn("2. the second unfinished outcome", out, out)
        self.assertIn("--continue N", out, out)

        # --continue 2 must pick the SECOND one by outcome text, never the
        # first; full completion on a genuine crash is already covered by
        # ContinueResumesACrashedRun, so this stays on the deterministic
        # failing model (hermetic, no network).
        env = dict(os.environ)
        env["MODEL_WORKER_CMD"] = "%s %s" % (
            sys.executable, tbr.write_stub(scratch, "failing2.py", tbr.FAILING_MODEL))
        picked = tbr.sh([sys.executable, BROTHER_RUN, "--continue", "2",
                        "--cwd", repo, "--runs-root", runs_root], env=env)
        pout = picked.stdout + picked.stderr
        self.assertIn("brother_run: resuming 'the second unfinished outcome'",
                     pout, pout)
        self.assertNotIn("the first unfinished outcome", pout, pout)


class ContinueIgnoresATerminalRun(unittest.TestCase):
    def test_a_completed_run_is_ignored(self):
        runs_root = tempfile.mkdtemp(prefix="continue-terminal-runs-")
        scratch = tempfile.mkdtemp(prefix="continue-terminal-scratch-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="continue-terminal-repo-"))

        decomposer = tbr.write_stub(scratch, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "T1", "objective": "create the file",
                 "done_check": "test -f t1.txt", "writes": ["t1.txt"],
                 "deps": []},
            ]))
        """)
        model = tbr.write_stub(scratch, "writer.py", tbr.WRITER_MODEL)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        proc = tbr.sh([sys.executable, BROTHER_RUN, "a file that fully lands",
                      "--cwd", repo, "--runs-root", runs_root], env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        cont = tbr.sh([sys.executable, BROTHER_RUN, "--continue",
                       "--cwd", repo, "--runs-root", runs_root])
        out = cont.stdout + cont.stderr
        self.assertEqual(cont.returncode, 0, out)
        self.assertIn("no unfinished run found", out, out)


if __name__ == "__main__":
    unittest.main()
