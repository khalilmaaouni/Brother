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
import shlex
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_brother_run as tbr  # noqa: E402
import product_acceptance as pa  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

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
        shlex.quote(sys.executable), shlex.quote(tbr.write_stub(scratch, "decomposer.py", decomposer_body)))
    env["MODEL_WORKER_CMD"] = "%s %s" % (
        shlex.quote(sys.executable), shlex.quote(tbr.write_stub(scratch, "failing_model.py", tbr.FAILING_MODEL)))
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
            shlex.quote(sys.executable), shlex.quote(tbr.write_stub(scratch, "failing2.py", tbr.FAILING_MODEL)))
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
            shlex.quote(sys.executable), shlex.quote(tbr.write_stub(scratch, "failing2.py", tbr.FAILING_MODEL)))
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
        env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(decomposer))
        env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(model))
        proc = tbr.sh([sys.executable, BROTHER_RUN, "a file that fully lands",
                      "--cwd", repo, "--runs-root", runs_root], env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        cont = tbr.sh([sys.executable, BROTHER_RUN, "--continue",
                       "--cwd", repo, "--runs-root", runs_root])
        out = cont.stdout + cont.stderr
        self.assertEqual(cont.returncode, 0, out)
        self.assertIn("no unfinished run found", out, out)


def _runs_in(runs_root):
    """Every run directory under a runs root, by name; the count is what
    tells a resumed run from a second, competing one."""
    runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
    return sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []


def _command_after(out, marker):
    """The copyable command a refusal printed: the first non-empty line
    AFTER the line holding `marker`. Returns None when the marker never
    appeared, so a test can say which half is missing."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if marker in line:
            for later in lines[i + 1:]:
                if later.strip():
                    return later.strip()
            return None
    return None


class ARefusalNamesItsNextCommand(unittest.TestCase):
    """E81, the half the 2026-09-04 stranger trial found missing: a run that
    refuses says WHY and then stops, leaving the person who typed the first
    command with nothing to type second. Both refusal shapes are covered:
    the door refusing before anything is claimed, and a run that reached the
    end with its work refused."""

    def test_a_refused_run_prints_the_command_that_continues_it(self):
        runs_root = tempfile.mkdtemp(prefix="next-cmd-runs-")
        scratch = tempfile.mkdtemp(prefix="next-cmd-scratch-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="next-cmd-repo-"))

        proc = _leave_unfinished(scratch, runs_root, repo,
                                 "the refused outcome", "R1", "r1.txt")
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("this run is not finished. Continue it with:", out, out)

        cmd = _command_after(out, "Continue it with:")
        self.assertIsNotNone(cmd, out)
        argv = shlex.split(cmd)
        self.assertIn("--continue", argv, cmd)
        self.assertIn(repo, argv, cmd)
        self.assertIn(runs_root, argv, cmd)

    def test_a_door_refusal_prints_the_ask_again_command(self):
        runs_root = tempfile.mkdtemp(prefix="next-cmd-door-runs-")
        scratch = tempfile.mkdtemp(prefix="next-cmd-door-scratch-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="next-cmd-door-repo-"))

        # The door's own refusal shape: the decomposer never answers, so no
        # store is written and there is nothing to continue. The next
        # command is therefore the same ask, not --continue.
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (
            shlex.quote(sys.executable),
            shlex.quote(tbr.write_stub(scratch, "no_model.py", tbr.FAILING_MODEL)))
        outcome = "an outcome the door cannot read a plan for"
        proc = tbr.sh([sys.executable, BROTHER_RUN, outcome,
                       "--cwd", repo, "--runs-root", runs_root], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("the door refused this outcome", out, out)
        self.assertIn("nothing to continue: the store is untouched", out, out)

        cmd = _command_after(out, "Ask again")
        self.assertIsNotNone(cmd, out)
        argv = shlex.split(cmd)
        self.assertIn(outcome, argv, cmd)
        self.assertIn(repo, argv, cmd)
        self.assertNotIn("--continue", argv, cmd)


class ThePrintedCommandContinuesTheSameRun(unittest.TestCase):
    """The command a refusal prints is worth nothing unless it works. This
    takes the line verbatim off the refusal's own output, runs it, and
    checks that it resumed THE SAME run rather than starting a second one
    against the same repository."""

    def test_running_the_printed_command_resumes_rather_than_restarts(self):
        runs_root = tempfile.mkdtemp(prefix="next-cmd-works-runs-")
        scratch = tempfile.mkdtemp(prefix="next-cmd-works-scratch-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="next-cmd-works-repo-"))

        outcome = "the file the first attempt never wrote"
        proc = _leave_unfinished(scratch, runs_root, repo, outcome,
                                 "W1", "w1.txt")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        before = _runs_in(runs_root)
        self.assertEqual(len(before), 1, before)

        cmd = _command_after(proc.stdout + proc.stderr, "Continue it with:")
        self.assertIsNotNone(cmd, proc.stdout + proc.stderr)

        # The second attempt gets a model that actually writes, which is the
        # only thing that changed between the two runs: same repository,
        # same runs root, same run directory.
        env = dict(os.environ)
        env["MODEL_WORKER_CMD"] = "%s %s" % (
            shlex.quote(sys.executable), shlex.quote(tbr.write_stub(scratch, "writer.py",
                                           tbr.WRITER_MODEL)))
        again = tbr.sh(shlex.split(cmd), env=env)
        out = again.stdout + again.stderr
        self.assertIn("brother_run: resuming", out, out)
        self.assertIn(outcome, out, out)
        self.assertEqual(_runs_in(runs_root), before, out)


class ContinuingWithNothingToContinueIsNoData(unittest.TestCase):
    """An absence is neither a pass nor a failure. A repository that never
    ran anything must say NO-DATA in the estate's own vocabulary, and must
    never hand a stranger a traceback."""

    def test_no_unfinished_run_reads_no_data_and_never_a_traceback(self):
        runs_root = tempfile.mkdtemp(prefix="nodata-runs-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="nodata-repo-"))

        proc = tbr.sh([sys.executable, BROTHER_RUN, "--continue",
                       "--cwd", repo, "--runs-root", runs_root])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("NO-DATA", out, out)
        self.assertIn("no unfinished run found", out, out)
        self.assertNotIn("Traceback", out, out)

def setUpModule():
    # Test-owned disk premise: worker admission reads this host's free
    # disk, so a full disk would otherwise read as a failing suite.
    from hermetic_worker_env import worker_environment
    _disk = worker_environment()
    _disk.__enter__()
    unittest.addModuleCleanup(_disk.__exit__, None, None, None)
    # Finding 31 (loop audit 2026-09-26), same fixture as test_board_status.py:
    # unmocked production calls read the tracked data/jev-seams.json, where
    # J030, J063, J064, J102 and J117 sit in shadow, and jev_decide resolves
    # the machine's real bridge (~/.claude/bin/or_ask.py) at import. A spy on
    # jev_seam.consult counted 21 calls (J063 9, J102 9, J117 3), all in child
    # processes, reaching a live shadow entry in one run of this module on hub
    # main under an empty HOME. Pin an all-off seams config and a bridge path
    # that does not exist, so only a case that patches its own config and
    # runner calls out.
    import json
    import shutil
    from unittest import mock
    import jev_decide
    import jev_seam
    root = tempfile.mkdtemp(prefix="brother-jev-seams-test-")
    unittest.addModuleCleanup(shutil.rmtree, root, ignore_errors=True)
    seams = os.path.join(root, "jev-seams.json")
    with open(seams, "w", encoding="utf-8") as fh:
        json.dump({"modes": {}}, fh)
    no_bridge = os.path.join(root, "no-bridge-in-tests")
    for patcher in (mock.patch.object(jev_seam, "DEFAULT_SEAMS_CONFIG_PATH", seams),
                    mock.patch.object(jev_decide, "DEFAULT_BRIDGE_PATH", no_bridge)):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    # The processes these tests launch re-import jev_seam and read the
    # tracked config afresh, out of reach of the patches above, and every
    # consult, even an off one, writes attempt rows. So the children get
    # BROTHER_JEV_SEAMS_OFF (the tracked file reads as every entry off), a
    # temporary BROTHER_JEV_STATE_DIR (ledger and daily call budget) and a
    # bridge command that cannot launch, quoted because the variable is
    # parsed as a command line; this process gets the same ledger and
    # budget paths, since its constants were fixed at import.
    import shlex
    import jev_checks
    state = tempfile.mkdtemp(prefix="brother-jev-state-test-")
    unittest.addModuleCleanup(shutil.rmtree, state, ignore_errors=True)
    ledger = os.path.join(state, "ledger")
    for patcher in (mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger),
                    mock.patch.object(jev_checks, "DEFAULT_LEDGER_DIR", ledger),
                    mock.patch.object(jev_seam, "DEFAULT_BUDGET_PATH",
                                      os.path.join(state, "jev-budget.json")),
                    mock.patch.dict(os.environ, {
                        "BROTHER_JEV_SEAMS_OFF": "1",
                        "BROTHER_JEV_STATE_DIR": state,
                        "BROTHER_DECISION_BRIDGE": shlex.quote(jev_decide.DEFAULT_BRIDGE_PATH)})):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def tearDownModule():
    # Python 3.9's unittest runs the module cleanups above only when this
    # hook exists (fixed in 3.10); without it the fixture roots leak per run.
    pass

if __name__ == "__main__":
    unittest.main()
