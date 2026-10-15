"""CF1.b: a network read of the cut path dies with its process group, runs under a low speed limit, and a timeout is
retried once. Controls: cut_preflight._run / network_bounds / check_public_remote, plugin_bump_gate._git_env /
previous_release_tag, cut_driver.network_runner and its two call sites. No network, no ~/.claude: fake runners, a local
git repository as the "remote", and one real child that sleeps. Run: python3 -B scripts/test_cut_preflight_run.py"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402
import cut_preflight as P  # noqa: E402
import plugin_bump_gate as G  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
SHA = "a" * 40


def done(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Recorder(object):
    """An injected runner: records every call (cmd and keywords), answers from a list, repeating the last."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cf1b-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.tmp]))
        patcher = mock.patch.dict(os.environ, dict(GIT_ENV, BROTHER_CUT_TEST="1",
                                                   BROTHER_CUT_EVIDENCE_DIR=os.path.join(self.tmp, "ev")))
        patcher.start()
        self.addCleanup(patcher.stop)


class TheGroupKill(Scratch):
    # REQ-CF1-05
    def test_a_timeout_kills_the_grandchild_too(self):
        pidfile = os.path.join(self.tmp, "pid")
        began = time.time()
        proc = P._run(["sh", "-c", "sleep 20 & echo $! > %s; wait" % pidfile], self.tmp, timeout=1.5)
        took = time.time() - began
        self.assertEqual(proc.returncode, 124)
        self.assertIn("timed out after 1.5s", proc.stderr)
        self.assertLess(took, 15)
        with open(pidfile) as fh:
            pid = int(fh.read().strip())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        for _ in range(50):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid), "the sleep the child started survived the bound")

    def test_the_same_kill_holds_through_the_drivers_network_runner(self):
        pidfile = os.path.join(self.tmp, "pid2")
        with mock.patch.object(P, "NETWORK_TIMEOUT_S", 1.5):
            rc, text = D.network_runner(["sh", "-c", "sleep 20 & echo $! > %s; wait" % pidfile], self.tmp)
        self.assertEqual(rc, 124)
        self.assertIn("timed out", text)
        with open(pidfile) as fh:
            pid = int(fh.read().strip())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        for _ in range(50):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid))


class TheResultShape(Scratch):
    # REQ-CF1-06
    def test_the_result_shape_and_the_injected_runner_call_are_unchanged(self):
        runner = Recorder(done(0, "out\n", "err\n"))
        env = {"A": "1"}
        proc = P._run(["git", "status"], self.tmp, runner, env=env, timeout=7)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "out\n", "err\n"))
        self.assertEqual(runner.calls, [(["git", "status"], dict(cwd=self.tmp, capture_output=True, text=True,
                                                                 env=env, timeout=7))])

    def test_an_injected_runner_that_raises_reads_127_and_124(self):
        def missing(cmd, **kw):
            raise OSError("no such binary")

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 3)
        proc = P._run(["git"], self.tmp, missing)
        self.assertEqual((proc.returncode, "no such binary" in proc.stderr), (127, True))
        proc = P._run(["git"], self.tmp, slow, timeout=3)
        self.assertEqual((proc.returncode, proc.stderr), (124, "timed out after 3s"))

    def test_the_real_path_keeps_text_output_and_127_and_exit_codes(self):
        proc = P._run(["sh", "-c", "echo hi; echo bad >&2; exit 3"], self.tmp, timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (3, "hi\n", "bad\n"))
        self.assertEqual(P._run(["cf1b-no-such-binary"], self.tmp).returncode, 127)
        self.assertEqual(P._run(["sh", "-c", "true"], os.path.join(self.tmp, "nope")).returncode, 127)

    def test_the_real_path_closes_standard_input(self):
        proc = P._run(["sh", "-c", "cat"], self.tmp, timeout=20)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))


class TheDoor(Scratch):
    # REQ-CF1-10
    def test_a_hostile_command_is_refused_at_the_door(self):
        for bad in ([], (), "", "git status", None, [""], ["git", ""], ["git", 1], [None], ["git", b"x"], 7):
            runner = Recorder(done())
            with self.assertRaises(ValueError, msg=repr(bad)):
                P._run(bad, self.tmp, runner)
            with self.assertRaises(ValueError, msg=repr(bad)):
                P._run(bad, self.tmp)
            self.assertEqual(runner.calls, [], repr(bad))

    def test_a_tuple_command_is_accepted_as_the_gate_builds_it(self):
        self.assertEqual(P._run(("sh", "-c", "exit 0"), self.tmp, timeout=20).returncode, 0)


class ThePublicRemote(Scratch):
    # REQ-CF1-07
    def test_the_public_remote_read_is_bounded_by_the_network_timeout(self):
        runner = Recorder(done(0, ""))
        got = P.check_public_remote(self.tmp, "1.1.4", "origin", runner)
        self.assertEqual(got[0], P.OK)
        cmd, kw = runner.calls[0]
        self.assertEqual(cmd[:3], ["git", "ls-remote", "origin"])
        self.assertEqual(kw["timeout"], P.NETWORK_TIMEOUT_S)
        self.assertEqual(kw["timeout"], 300)

    def test_a_timeout_there_reads_no_data_never_ok(self):
        got = P.check_public_remote(self.tmp, "1.1.4", "origin", Recorder(done(124, "", "timed out after 300s")))
        self.assertEqual(got[0], P.NODATA)

    def test_the_public_remote_read_carries_the_bounds_and_the_inherited_environment(self):
        runner = Recorder(done(0, ""))
        with mock.patch.dict(os.environ, {"CF1B_MARK": "kept"}):
            P.check_public_remote(self.tmp, "1.1.4", "origin", runner)
        env = runner.calls[0][1]["env"]
        for key, value in P.network_bounds().items():
            self.assertEqual(env[key], value)
        self.assertEqual(env["CF1B_MARK"], "kept")


class TheBounds(Scratch):
    # REQ-CF1-08
    def test_ls_remote_has_low_speed_and_no_prompt_env(self):
        self.assertEqual(P.network_bounds(), {"GIT_HTTP_LOW_SPEED_LIMIT": "1000", "GIT_HTTP_LOW_SPEED_TIME": "30",
                                              "GIT_TERMINAL_PROMPT": "0"})
        runner = Recorder(done(0, ""))
        G.previous_release_tag(self.tmp, "1.1.4", "origin", runner)
        cmd, kw = runner.calls[0]
        self.assertEqual(cmd, ["git", "ls-remote", "--tags", "origin"])
        for key, value in P.network_bounds().items():
            self.assertEqual(kw["env"][key], value)
        self.assertEqual(kw["timeout"], 300)

    def test_the_gate_environment_has_the_bounds_and_still_drops_git_location(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere", "GIT_WORK_TREE": "/elsewhere"}):
            env = G._git_env()
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)
        for key, value in P.network_bounds().items():
            self.assertEqual(env[key], value)

    def test_there_is_one_network_timeout(self):
        self.assertIs(G.NETWORK_TIMEOUT_S, P.NETWORK_TIMEOUT_S)

    def test_the_gate_fetch_carries_the_bounds_too(self):
        runner = Recorder(done(1, "", "fetch failed"))
        got = G._compare(self.tmp, "1.1.4", "origin", "v1.1.3", runner, False)
        self.assertEqual(got[0], P.NODATA)
        cmd, kw = runner.calls[0]
        self.assertEqual(cmd[:2], ("git", "fetch"))
        self.assertEqual(kw["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kw["timeout"], P.NETWORK_TIMEOUT_S)


class TheRetry(Scratch):
    # REQ-CF1-09
    TAGS = "%s\trefs/tags/v1.1.3\n%s\trefs/tags/v1.1.2\n" % (SHA, SHA)

    def test_exit_124_is_retried_exactly_once(self):
        runner = Recorder(done(124, "", "timed out after 300s"), done(0, self.TAGS))
        self.assertEqual(G.previous_release_tag(self.tmp, "1.1.4", "origin", runner), ("v1.1.3", ""))
        self.assertEqual(len(runner.calls), 2)

    def test_a_second_124_reads_no_data_after_exactly_two_calls(self):
        runner = Recorder(done(124, "", "timed out after 300s"))
        tag, why = G.previous_release_tag(self.tmp, "1.1.4", "origin", runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertIsNone(tag)
        self.assertIn("git ls-remote --tags origin failed", why)

    def test_no_other_failure_is_retried(self):
        for rc in (1, 2, 127, 128, 255, 123, 125):
            runner = Recorder(done(rc, "", "boom"))
            tag, why = G.previous_release_tag(self.tmp, "1.1.4", "origin", runner)
            self.assertEqual((len(runner.calls), tag), (1, None), rc)
            self.assertIn("failed", why)

    def test_a_success_is_read_once(self):
        runner = Recorder(done(0, self.TAGS))
        G.previous_release_tag(self.tmp, "1.1.4", "origin", runner)
        self.assertEqual(len(runner.calls), 1)

    def test_a_real_local_remote_is_read_through_the_real_path(self):
        remote = os.path.join(self.tmp, "remote")
        os.makedirs(remote)
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"], ["tag", "v1.0.9"],
                     ["tag", "v1.0.20"], ["tag", "v3.4.2"]):
            subprocess.run(["git"] + args, cwd=remote, check=True, capture_output=True)
        self.assertEqual(G.previous_release_tag(self.tmp, "1.1.0", remote), ("v1.0.20", ""))


class TheDriverCallSites(Scratch):
    def test_network_runner_reads_a_local_remote_and_reports_a_failure_with_its_text(self):
        remote = os.path.join(self.tmp, "r")
        os.makedirs(remote)
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"], ["tag", "v1.0.0"]):
            subprocess.run(["git"] + args, cwd=remote, check=True, capture_output=True)
        rc, text = D.network_runner(["git", "ls-remote", remote, "refs/tags/v1.0.0"], self.tmp)
        self.assertEqual(rc, 0)
        self.assertTrue(text.strip().endswith("refs/tags/v1.0.0"))
        rc, text = D.network_runner(["git", "ls-remote", os.path.join(self.tmp, "absent")], self.tmp)
        self.assertNotEqual(rc, 0)
        self.assertTrue(text.strip())
        self.assertEqual(D.network_runner(["cf1b-no-such-binary"], self.tmp)[0], 127)

    def test_network_runner_passes_the_bounds_and_the_timeout_to_the_one_seam(self):
        seen = []

        def spy(cmd, cwd, runner=None, env=None, timeout=None):
            seen.append((cmd, runner, env, timeout))
            return done(0, "ok\n", "")
        with mock.patch.object(P, "_run", spy), mock.patch.dict(os.environ, {"CF1B_MARK": "kept"}):
            self.assertEqual(D.network_runner(["git", "ls-remote", "x"], self.tmp), (0, "ok\n"))
        cmd, runner, env, timeout = seen[0]
        self.assertIsNone(runner)
        self.assertEqual(timeout, P.NETWORK_TIMEOUT_S)
        self.assertEqual(env["CF1B_MARK"], "kept")
        for key, value in P.network_bounds().items():
            self.assertEqual(env[key], value)

    def test_the_freeze_phase_tag_branch_read_is_the_bounded_one(self):
        seen = []

        def plain(cmd, cwd, stream=False):
            seen.append(("plain", list(cmd)))
            if cmd[:2] == ["git", "rev-parse"]:
                return 0, SHA + "\n"
            return 0, ""

        def bounded(cmd, cwd, stream=False):
            seen.append(("bounded", list(cmd)))
            return 0, SHA + "\trefs/heads/release/1.1.4\n"
        with mock.patch.object(D, "real_runner", plain), mock.patch.object(D, "network_runner", bounded), \
                mock.patch.object(D, "_ready", return_value=("", "PASS", "", "t" * 40)):
            D.freeze_phase(self.tmp, "1.1.4", None, SHA, "release/1.1.4", "hub", self.tmp)
        reads = [s for s in seen if "ls-remote" in s[1]]
        self.assertEqual([r[0] for r in reads], ["bounded"])

    def test_the_cut_phase_public_tag_read_is_the_bounded_one(self):
        seen = []

        def plain(cmd, cwd, stream=False):
            seen.append(("plain", list(cmd)))
            return 0, "DONE: cut 1.1.4 pushed\n"

        def bounded(cmd, cwd, stream=False):
            seen.append(("bounded", list(cmd)))
            return 0, SHA + "\trefs/tags/v1.1.4\n"
        with mock.patch.object(D, "real_runner", plain), mock.patch.object(D, "network_runner", bounded):
            got = D.cut_phase(self.tmp, "1.1.4", SHA, os.path.join(self.tmp, "body"), None, True)
        self.assertEqual(got[0], "PASS", got)
        reads = [s for s in seen if "ls-remote" in s[1]]
        self.assertEqual([r[0] for r in reads], ["bounded"])

    def test_an_injected_runner_still_answers_both_reads_itself(self):
        seen = []

        def mine(cmd, cwd, stream=False):
            seen.append(list(cmd))
            return 0, "DONE: cut 1.1.4 pushed\n" if "-u" in cmd else SHA + "\trefs/tags/v1.1.4\n"
        with mock.patch.object(D, "network_runner", side_effect=AssertionError("must not be used")):
            got = D.cut_phase(self.tmp, "1.1.4", SHA, os.path.join(self.tmp, "body"), mine, True)
        self.assertEqual(got[0], "PASS", got)
        self.assertTrue(any("ls-remote" in " ".join(c) for c in seen))


if __name__ == "__main__":
    unittest.main()
