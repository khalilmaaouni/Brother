#!/usr/bin/env python3
"""native_worker.py runs one Claude Code session per build in a reset seat worktree and hands the grader a build JSON or
nothing (owner order 2026-10-02, PLAN step 2 of the native worker handover).

THE ENTRY POINTS: every session case runs run() as the fan out calls it, and the fan out cases run main(argv) as
unit_runner launches it; the child process is replaced at the tool boundary only (runner=), so seats, the reset, the
config breaker check, the ledger rows and the adapter all run for real. ONE CONDITION PER FIXTURE: each refusal starts
from the good session and breaks one thing.
Run: python3 -B scripts/loop/test_native_worker.py"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, time, unittest, uuid
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import native_worker as NW  # noqa: E402
import fanout_verdict as FV  # noqa: E402
import loop_switches as NW_SW  # noqa: E402
import brother_login as BL  # noqa: E402

PREFIX = "sk-" + "ant-oat01-"   # joined so no file holds the key shape at rest; the push gate scans for it
CANARY = PREFIX + "CANARY" + "x7Qz" * 8 + "-_END"   # a synthetic login; the real Keychain is never touched

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
MODEL = "opus55"   # a registry name on the claude transport
DONE = json.dumps({"type": "result", "is_error": False, "result": "DONE", "total_cost_usd": 0.5,
                   "usage": {"input_tokens": 10, "output_tokens": 20}})
MUTS = [{"name": "m%d" % i, "path": "pkg/mod.py", "find": "return %d" % i, "replace": "return -1", "caught_by": "t"}
        for i in (1, 2, 3)]
SPEC = """# Unit X

## X.1 first sub unit
Do one.

### X.1 detail
Still part of X.1.

## X.10 another
Not X.1.
"""


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def git(wd, *args):
    return subprocess.run(["git"] + list(args), cwd=wd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout


def good_worker(argv, stdin, timeout, cwd, muts=MUTS, extra=None, stdout=DONE):
    """What a session leaves behind: an edit, a test and the three .brother files."""
    def w(rel, text):
        p = os.path.join(cwd, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    w("pkg/mod.py", "def a():\n    return 1\ndef b():\n    return 2\ndef c():\n    return 3\n")
    w("pkg/test_mod.py", "import unittest\n")
    w(".brother/done_check.txt", "python3 -B -m unittest pkg.test_mod\n")
    w(".brother/mutations.json", json.dumps(muts))
    for rel, text in (extra or {}).items():
        w(rel, text)
    return {"returncode": 0, "stdout": stdout, "stderr": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="native-worker-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(self.repo, "pkg"))
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "pkg/mod.py"), "w") as fh:
            fh.write("def a():\n    return 0\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        self.ledger = os.path.join(self.root, "claude-calls.jsonl")
        self.env = {"BROTHER_CLAUDE_CALLS_LEDGER": self.ledger, "BROTHER_NATIVE_SEAT_ROOT": os.path.join(self.root, "seats")}
        saved = {k: os.environ.get(k) for k in list(self.env) + ["BROTHER_NATIVE_SEATS", "BROTHER_RUN_DIR"]}
        os.environ.update(self.env)
        os.environ.pop("BROTHER_NATIVE_SEATS", None)
        os.environ.pop("BROTHER_RUN_DIR", None)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        self.held = ""
        patches = [(NW.MC, "config_admit", lambda transport, model_id: self.held),
                   (NW.R, "claude_bin", lambda: "/nonexistent/claude")]
        for mod, name, fake in patches:
            old = getattr(mod, name)
            setattr(mod, name, fake)
            self.addCleanup(setattr, mod, name, old)
        self.out = os.path.join(self.root, "out", "X.1-r0-build.json")
        os.makedirs(os.path.dirname(self.out))

    def go(self, runner=good_worker, model=MODEL, brief="do X.1", seats=1, timeout=60):
        return NW.run("X.1", brief, self.base, model, timeout, self.out, self.repo, os.path.join(self.root, "log"),
                      seat_count=seats, runner=runner)

    def rows(self):
        if not os.path.exists(self.ledger):
            return []
        with open(self.ledger, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def assertNothing(self, ok_why, word):
        ok, why = ok_why
        self.assertFalse(ok)
        self.assertIn(word, why)
        self.assertFalse(os.path.exists(self.out), "a refused session must write no build")


class Session(Base):
    def test_good_session_writes_the_build_and_two_ledger_rows(self):
        ok, why = self.go()
        self.assertTrue(ok, why)
        build = load(self.out)
        self.assertEqual([e["path"] for e in build["edits"]], ["pkg/mod.py"])
        self.assertTrue(all(e["path"] == "pkg/mod.py" for e in build["edits"]))
        rows = self.rows()
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual(rows[0]["kind"], "native")
        self.assertEqual(rows[1]["cost_usd"], 0.5)
        self.assertEqual(rows[0]["call"], rows[1]["call"])

    def test_a_model_off_the_claude_transport_is_refused(self):
        self.assertNothing(self.go(model="deepseek"), "not a Claude transport model")
        self.assertEqual(self.rows(), [])

    def test_an_empty_brief_is_refused(self):
        self.assertNothing(self.go(brief="  "), "empty brief")

    def test_a_held_configuration_is_config_wait_and_never_registered(self):
        self.held = "the program does not know the model"
        self.assertNothing(self.go(), "CONFIG_WAIT")
        self.assertEqual(self.rows(), [])

    def test_an_error_result_is_no_data(self):
        bad = json.dumps({"is_error": True, "result": "boom", "total_cost_usd": 0.1})
        self.assertNothing(self.go(runner=lambda a, s, t, c: good_worker(a, s, t, c, stdout=bad)), "error result")
        self.assertEqual(len(self.rows()), 2)

    def test_a_timeout_is_no_data_and_closes_its_ledger_row(self):
        def slow(a, s, t, c):
            raise subprocess.TimeoutExpired(a, t)
        self.assertNothing(self.go(runner=slow), "TimeoutExpired")
        rows = self.rows()
        self.assertEqual([r.get("outcome") for r in rows[1:]], ["timeout"])

    def test_a_session_that_cannot_start_is_no_data(self):
        def nostart(a, s, t, c):
            raise NW.MC.SpawnFailed(2, "missing")
        self.assertNothing(self.go(runner=nostart), "did not start")

    def test_an_adapter_refusal_writes_nothing(self):
        self.assertNothing(self.go(runner=lambda a, s, t, c: good_worker(a, s, t, c, muts=MUTS[:2])), "ADAPTER REFUSED")

    def test_the_seat_is_reset_between_sessions(self):
        ok, why = self.go(runner=lambda a, s, t, c: good_worker(a, s, t, c, extra={"pkg/leftover.py": "x = 1\n"}))
        self.assertTrue(ok, why)
        os.remove(self.out)
        ok, why = self.go()
        self.assertTrue(ok, why)
        paths = [e["path"] for e in load(self.out)["edits"]]
        self.assertNotIn("pkg/leftover.py", paths)

    def test_a_busy_seat_is_no_data(self):
        os.makedirs(NW.seat_root(), exist_ok=True)
        with NW.seat(1, 5):
            self.assertNothing(self.go(timeout=1), "no native seat free")

    def unlock_later(self):
        # a failed case must still let the fixture's rmtree run
        self.addCleanup(lambda: subprocess.run(["chflags", "-R", "nouchg", self.root], capture_output=True))
        self.addCleanup(lambda: subprocess.run(["chmod", "-R", "u+rwx", self.root], capture_output=True))

    def test_a_locked_tracked_file_left_by_a_session_is_rebuilt_not_wedged(self):
        # eighth review 2026-10-02: chflags uchg on one tracked file made every later reset fail
        self.unlock_later()
        def locker(a, s, t, c):
            out = good_worker(a, s, t, c)
            subprocess.run(["chflags", "uchg", os.path.join(c, "pkg", "mod.py")], check=True)
            return out
        self.go(runner=locker)
        os.path.exists(self.out) and os.remove(self.out)
        ok, why = self.go()
        self.assertTrue(ok, why)

    def test_an_unwritable_folder_left_in_the_seat_temp_is_rebuilt_not_wedged(self):
        self.unlock_later()
        def leaver(a, s, t, c):
            out = good_worker(a, s, t, c)
            d = os.path.join(c, ".tmp", "ro")
            os.makedirs(d)
            open(os.path.join(d, "f"), "w").close()
            os.chmod(d, 0o500)
            return out
        self.go(runner=leaver)
        os.path.exists(self.out) and os.remove(self.out)
        ok, why = self.go()
        self.assertTrue(ok, why)

    def plant_git_dir(self, hook_marker=None):
        """The review's escape: the seat's .git replaced by a real git directory, optionally with an fsmonitor hook."""
        wt = NW.seat_dir(self.repo, 0)
        os.makedirs(wt)
        git(wt, "init", "-q")
        if hook_marker:
            hook = os.path.join(self.root, "fsmon.sh")
            with open(hook, "w") as fh:
                fh.write("#!/bin/sh\ntouch '%s'\n" % hook_marker)
            os.chmod(hook, 0o755)
            git(wt, "config", "core.fsmonitor", hook)
        return wt

    def test_a_planted_git_dir_never_runs_its_hook_and_the_seat_is_rebuilt(self):
        marker = os.path.join(self.root, "hook-ran")
        self.plant_git_dir(marker)
        ok, why = self.go()
        self.assertTrue(ok, why)
        self.assertFalse(os.path.exists(marker), "a planted hook ran outside the sandbox")

    def test_a_seat_linked_to_another_repository_is_rebuilt(self):
        other = os.path.join(self.root, "other")
        os.makedirs(other)
        git(other, "init", "-q")
        wt = NW.seat_dir(self.repo, 0)
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as fh:
            fh.write("gitdir: %s\n" % os.path.join(other, ".git"))
        ok, why = self.go()
        self.assertTrue(ok, why)
        self.assertTrue(NW.seat_link_ok(self.repo, wt))

    def test_a_seat_inside_another_checkout_is_rebuilt_inside_the_seat_root_only(self):
        os.environ["BROTHER_NATIVE_SEAT_ROOT"] = os.path.join(self.repo, "seats")
        os.makedirs(os.path.join(NW.seat_dir(self.repo, 0), ".git"))
        with open(os.path.join(self.repo, "untracked.txt"), "w") as fh:
            fh.write("the enclosing checkout's own work\n")
        ok, why = self.go()
        self.assertTrue(ok, why)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "untracked.txt")))

    def test_a_session_that_rewrites_its_seats_git_is_refused(self):
        def rewriter(a, s, t, c):
            out = good_worker(a, s, t, c)
            os.remove(os.path.join(c, ".git"))
            os.makedirs(os.path.join(c, ".git"))
            return out
        self.assertNothing(self.go(runner=rewriter), "rewrote its seat's .git")

    def test_a_symlinked_seat_link_is_not_trusted_even_with_the_right_text(self):
        self.assertTrue(self.go()[0])
        wt = NW.seat_dir(self.repo, 0)
        link = os.path.join(wt, ".git")
        copy = os.path.join(self.root, "link-copy")
        shutil.copy(link, copy)
        os.remove(link)
        os.symlink(copy, link)
        self.assertFalse(NW.seat_link_ok(self.repo, wt))

    def test_a_link_to_another_worktrees_record_never_resets_that_worktree(self):
        other = os.path.join(self.root, "live-loop")
        git(self.repo, "branch", "-q", "liveline")
        git(self.repo, "worktree", "add", "-q", other, "liveline")
        record = git(other, "rev-parse", "--git-dir").strip()
        wt = NW.seat_dir(self.repo, 0)
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as fh:
            fh.write("gitdir: %s\n" % record)
        self.assertFalse(NW.seat_link_ok(self.repo, wt))
        ok, why = self.go()
        self.assertTrue(ok, why)
        self.assertEqual(git(other, "symbolic-ref", "--short", "HEAD").strip(), "liveline", "the other worktree kept its branch")

    def test_the_rounds_effort_arm_reaches_the_session(self):
        seen = {}
        def spy(a, s, t, c):
            seen["argv"] = a
            return good_worker(a, s, t, c)
        self.assertTrue(NW.run("X.1", "do X.1", self.base, MODEL, 60, self.out, self.repo, os.path.join(self.root, "log"),
                               seat_count=1, runner=spy, effort="xhigh")[0])
        self.assertEqual(seen["argv"][seen["argv"].index("--effort") + 1], "xhigh")

    def test_the_tool_folder_exists_before_the_session(self):
        seen = {}
        def spy(a, s, t, c):
            seen["ok"] = os.path.isdir(NW.tool_dir(c))
            return good_worker(a, s, t, c)
        self.assertTrue(self.go(runner=spy)[0])
        self.assertTrue(seen["ok"])
        shutil.rmtree(NW.tool_dir(NW.seat_dir(self.repo, 0)), ignore_errors=True)

    def test_an_arm_below_the_floor_is_lifted_to_it(self):
        seen = {}
        def spy(a, s, t, c):
            seen["argv"] = a
            return good_worker(a, s, t, c)
        NW.run("X.1", "do X.1", self.base, MODEL, 60, self.out, self.repo, os.path.join(self.root, "log"),
               seat_count=1, runner=spy, effort="low")
        self.assertEqual(seen["argv"][seen["argv"].index("--effort") + 1], "medium")

    def test_an_arm_that_names_no_level_parks_config_wait_and_runs_nothing(self):
        """ONE EFFORT RULE (FX-31.5 review gap 2): the adapter's. An arm that names no Claude level is refused for a
        native session exactly as for a one shot call, naming the variable; it is never read as the floor."""
        seen = {}
        def spy(a, s, t, c):
            seen["argv"] = a
            return good_worker(a, s, t, c)
        ok, why = NW.run("X.1", "do X.1", self.base, MODEL, 60, self.out, self.repo, os.path.join(self.root, "log"),
                         seat_count=1, runner=spy, effort="minimal")
        self.assertFalse(ok)
        self.assertTrue(why.startswith("CONFIG_WAIT"), why)
        self.assertIn("BROTHER_CLAUDE_EFFORT", why)
        self.assertEqual(seen, {}, "a session ran under an effort nobody can read")

    def test_a_logged_out_cli_parks_config_wait(self):
        doc = json.dumps({"is_error": True, "result": "Not logged in \u00b7 Please run /login"})
        ok, why = self.go(runner=lambda a, s, t, c: {"returncode": 1, "stdout": doc, "stderr": ""})
        self.assertFalse(ok)
        self.assertTrue(why.startswith("CONFIG_WAIT"), why)

    def test_a_refused_profile_parks_config_wait(self):
        ok, why = self.go(runner=lambda a, s, t, c: {"returncode": 65, "stdout": "", "stderr": "sandbox-exec: profile parse error\n"})
        self.assertTrue(why.startswith("CONFIG_WAIT"), why)

    def test_an_unexplained_silent_exit_stays_a_claude_error(self):
        ok, why = self.go(runner=lambda a, s, t, c: {"returncode": 1, "stdout": "", "stderr": "overloaded\n"})
        self.assertTrue(why.startswith(NW.CLAUDE_ERROR), why)

    def test_a_program_installed_in_a_closed_home_folder_is_refused_before_the_session(self):
        home = os.path.realpath(os.path.expanduser("~"))
        self.assertIn("does not open", NW.program_outside_sandbox(os.path.join(home, "weird", "claude")))
        self.assertEqual(NW.program_outside_sandbox(os.path.join(home, ".nvm", "bin", "claude")), "")
        self.assertEqual(NW.program_outside_sandbox("/opt/homebrew/bin/claude"), "")
        old = NW.R.claude_bin
        NW.R.claude_bin = lambda: os.path.join(home, "weird", "claude")
        self.addCleanup(setattr, NW.R, "claude_bin", old)
        ok, why = self.go()
        self.assertTrue(why.startswith("CONFIG_WAIT"), why)
        self.assertEqual(self.rows(), [], "no session was registered")

    def test_the_brief_names_the_read_only_index_and_home(self):
        b = NW.brief("X.1", "S", "C")
        self.assertIn("git apply -R", b)
        self.assertIn("builds its own HOME", b)

    def test_the_brief_writes_the_three_files_last(self):
        self.assertIn("as your LAST action", NW.brief("X.1", "S", "C"))

    def test_a_relative_worktree_record_is_accepted(self):
        wt = NW.seat_dir(self.repo, 0)
        r = subprocess.run(["git", "-c", "worktree.useRelativePaths=true", "worktree", "add", "-q", "--detach", wt, self.base],
                           cwd=self.repo, env=GIT_ENV, capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("this git has no worktree.useRelativePaths: %s" % r.stderr.strip()[:80])
        self.assertTrue(NW.seat_link_ok(self.repo, wt))

    def test_a_fifo_in_place_of_git_is_refused_without_blocking(self):
        wt = NW.seat_dir(self.repo, 0)
        os.makedirs(wt)
        os.mkfifo(os.path.join(wt, ".git"))
        self.assertFalse(NW.seat_link_ok(self.repo, wt))

    def test_the_operators_effort_holds_when_no_arm_is_set(self):
        seen = {}
        def spy(a, s, t, c):
            seen["argv"] = a
            return good_worker(a, s, t, c)
        os.environ["BROTHER_CLAUDE_EFFORT"] = "xhigh"
        self.addCleanup(os.environ.pop, "BROTHER_CLAUDE_EFFORT", None)
        self.assertTrue(self.go(runner=spy)[0])
        self.assertEqual(seen["argv"][seen["argv"].index("--effort") + 1], "xhigh")

    def test_an_old_cli_refusing_a_flag_parks_config_wait(self):
        ok, why = self.go(runner=lambda a, s, t, c: {"returncode": 1, "stdout": "", "stderr": "error: unknown option '--x'\n"})
        self.assertTrue(why.startswith("CONFIG_WAIT"), why)

    def test_a_missing_file_inside_an_error_result_is_not_a_setup_fault(self):
        doc = json.dumps({"is_error": True, "result": "Test failed: [Errno 2] No such file or directory: 'x.json'"})
        ok, why = self.go(runner=lambda a, s, t, c: {"returncode": 1, "stdout": doc, "stderr": ""})
        self.assertTrue(why.startswith(NW.CLAUDE_ERROR), why)

    def test_the_session_reads_its_repositorys_git_dir(self):
        seen = {}
        def spy(a, s, t, c):
            seen["argv"] = a
            return good_worker(a, s, t, c)
        self.assertTrue(self.go(runner=spy)[0])
        self.assertIn("COMMON=" + os.path.realpath(os.path.join(self.repo, ".git")), seen["argv"])

    def test_session_paths_are_resolved_through_symlinks(self):
        real = os.path.join(self.root, "real-seat")
        os.makedirs(real)
        link = os.path.join(self.root, "linked-seat")
        os.symlink(real, link)
        a = NW.session_argv("claude-opus-5-5", "medium", link, "/c")
        self.assertIn("WT=" + os.path.realpath(real), a)

    def test_seats_of_two_repositories_never_share_a_worktree(self):
        other = os.path.join(self.root, "repo2")
        os.makedirs(other)
        git(other, "init", "-q")
        self.assertNotEqual(NW.seat_dir(self.repo, 0), NW.seat_dir(other, 0))
        self.assertIsNone(NW.seat_dir(os.path.join(self.root, "not-a-repo"), 0))

    def test_a_session_cut_at_its_cap_with_finished_work_is_graded(self):
        def finished_then_cut(a, s, t, c):
            good_worker(a, s, t, c)
            raise subprocess.TimeoutExpired(a, t)
        ok, why = self.go(runner=finished_then_cut)
        self.assertTrue(ok, why)
        self.assertTrue(os.path.isfile(self.out))

    def test_an_exit_with_no_result_is_a_claude_error_and_keeps_stderr(self):
        def logged_out(a, s, t, c):
            return {"returncode": 1, "stdout": "", "stderr": "upstream connect error or disconnect\n"}
        ok, why = self.go(runner=logged_out)
        self.assertFalse(ok)
        self.assertTrue(why.startswith(NW.CLAUDE_ERROR), why)
        self.assertIn("stderr: upstream connect error", why)
        with open(os.path.join(self.root, "log", "claude.stderr")) as fh:
            self.assertIn("upstream connect error", fh.read())

    def test_the_session_runs_sandboxed_with_real_tools(self):
        seen = {}
        def spy(a, s, t, c):
            seen.update(argv=a, stdin=s, cwd=c)
            return good_worker(a, s, t, c)
        self.assertTrue(self.go(runner=spy)[0])
        a = seen["argv"]
        self.assertEqual(a[:3], ["sandbox-exec", "-p", NW.PROFILE])
        self.assertIn("(deny file-write*)", NW.PROFILE)
        self.assertIn(r"\.(ssh|aws|gnupg|netrc|docker|kube)", NW.PROFILE)
        self.assertIn("WT=" + os.path.realpath(seen["cwd"]), a)
        self.assertEqual(a[a.index("--tools") + 1], NW.TOOLS)
        self.assertEqual(a[a.index("--setting-sources") + 1], "")
        self.assertEqual(seen["stdin"], "do X.1")
        self.assertEqual(a[a.index("--model") + 1], "claude-opus-5-5")


@unittest.skipUnless(shutil.which("sandbox-exec"), "the native sandbox exists on macOS only")
class TokenLane(Base):
    """BROTHER_LOOP_TOKEN: off is today's launch byte for byte; on reads the login brother-login saved and hands it to
    the child alone, after the scrub, or parks the round. A fake security replaces the Keychain tool."""

    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in ("BROTHER_LOOP_TOKEN", BL.TOKEN_VAR)}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        os.environ.pop("BROTHER_LOOP_TOKEN", None)
        os.environ[BL.TOKEN_VAR] = PREFIX + "INHERITED" + "a" * 24   # a parent's token: never the child's
        old = BL.READER
        self.addCleanup(setattr, BL, "READER", old)
        self.calls = []

    def keychain(self, rc=0, value=CANARY):
        """A fake reader: records every argv, answers get with value at rc."""
        p = os.path.join(self.root, "brother-keychain")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\necho \"$@\" >> %s/security-argv.log\n[ %d -eq 0 ] && printf '%%s\\n' '%s'\nexit %d\n"
                     % (self.root, rc, value, rc))
        os.chmod(p, 0o700)
        BL.READER = p

    def spy(self, a, s, t, c, env=None):
        got = os.read(int(env[BL.FD_VAR]), 65536) if env and BL.FD_VAR in env else None   # what the CLI would read
        self.calls.append((a, env))
        self.piped = got
        return good_worker(a, s, t, c)

    def spy4(self, a, s, t, c):   # today's runner shape: no env keyword
        self.calls.append((a, None))
        return good_worker(a, s, t, c)

    def test_off_is_todays_launch_and_never_asks_the_keychain(self):
        self.keychain(rc=0)
        self.assertTrue(self.go(runner=self.spy4)[0])
        argv, env = self.calls[0]
        self.assertIsNone(env)
        self.assertIn(BL.TOKEN_VAR, argv, "the inherited token is unset by the scrub")
        self.assertEqual(argv[argv.index(BL.TOKEN_VAR) - 1], "-u")
        self.assertNotIn("deny process-exec (literal \"/usr/bin/security\")", argv[2])
        self.assertFalse(os.path.exists(os.path.join(self.root, "security-argv.log")), "no Keychain read when off")

    def test_a_misspelt_switch_is_off(self):
        self.keychain(rc=0)
        for v in ("yes", " ON ", "On", "on "):   # exact "on" only (review 2026-10-03)
            self.calls = []
            os.environ["BROTHER_LOOP_TOKEN"] = v
            self.assertTrue(self.go(runner=self.spy4)[0], v)
            self.assertIsNone(self.calls[0][1], v)
            self.assertNotIn("--allowedTools", self.calls[0][0], v)

    def test_on_hands_the_keychain_login_to_the_child_alone(self):
        self.keychain(rc=0)
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        parent = dict(os.environ)
        ok, why = self.go(runner=self.spy)
        self.assertTrue(ok, why)
        argv, env = self.calls[0]
        self.assertEqual(self.piped, CANARY.encode(), "the fresh Keychain value, not the inherited one, on the pipe")
        self.assertNotIn(BL.TOKEN_VAR, env, "the login never sits in the child's environment (F1)")
        self.assertNotIn(CANARY, json.dumps(env))
        self.assertNotIn(BL.FD_VAR, argv, "the descriptor's name is not unset by the scrub")
        self.assertNotIn(CANARY, " ".join(argv))
        self.assertFalse([k for k in env if k != BL.FD_VAR and NW.G.SECRET_ENV.search(k)], "every other credential name is gone")
        with self.assertRaises(OSError, msg="the parent closed its end of the pipe after the session"):
            os.fstat(int(env[BL.FD_VAR]))
        self.assertIn("deny process-exec (literal \"/usr/bin/security\")", argv[2], "the seat sandbox shuts the Keychain tool")
        self.assertEqual(env[NW.SCRUB_VAR], "1", "the CLI's own tool children never inherit the login")
        self.assertIn("--allowedTools", argv, "the scrub forces default mode; the declared tools still run")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], NW.TOOLS)
        self.assertEqual(dict(os.environ), parent, "the parent environment is untouched")
        for d, _, files in os.walk(self.root):
            for f in files:
                if f == "brother-keychain":   # the fake reader's own script carries the value by construction
                    continue
                with open(os.path.join(d, f), "rb") as fh:
                    self.assertNotIn(CANARY.encode(), fh.read(), f)
        self.assertEqual(len(self.rows()), 2)

    def test_on_the_cli_tool_children_cannot_see_the_login(self):
        """The REAL launch (sandbox-exec, env, the scrub) around a fake claude that behaves like the real one: its tool
        child inherits the whole environment unless CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is truthy, and prints the login
        into the session result. The session log must never carry it. The seat root is outside HOME, as the real
        seats are, so the sandbox can run the fake."""
        if not shutil.which("sandbox-exec"):
            self.skipTest("NO-DATA: no sandbox-exec on this machine")
        self.keychain(rc=0)
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        fake = os.path.join(self.root, "claude")
        with open(fake, "w") as fh:   # reads its login from the descriptor once, as the CLI does, then runs a tool child
            fh.write("#!/bin/bash\n"
                     "tok=$(cat <&\"$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR\")\n"
                     "[ \"$tok\" = '%s' ] && got=read-login || got=no-login\n"
                     "case \"$CLAUDE_CODE_SUBPROCESS_ENV_SCRUB\" in 1|true|yes|on) unset CLAUDE_CODE_OAUTH_TOKEN;; esac\n"
                     "leak=$(sh -c 'printenv CLAUDE_CODE_OAUTH_TOKEN; cat <&\"$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR\"' 2>/dev/null)\n"
                     "printf '{\"type\":\"result\",\"is_error\":false,\"result\":\"%%s tool said: %%s\"}' \"$got\" \"$leak\"\n"
                     % CANARY)
        os.chmod(fake, 0o700)
        NW.R.claude_bin = lambda: fake
        log = os.path.join(self.root, "log")
        ok, why = NW.run("X.1", "do X.1", self.base, MODEL, 60, self.out, self.repo, log, seat_count=1)
        with open(os.path.join(log, "claude.json")) as fh:
            said = fh.read()
        self.assertIn("read-login tool said: ", said, "the fake read its login from the descriptor under the real launch: " + why)
        self.assertNotIn(CANARY, said, "the tool child printed the login")
        self.assertIn("tool said: \"", said, "the child saw nothing, in its environment or on the drained pipe")

    def test_on_a_seat_reading_the_running_clis_process_arguments_finds_no_login(self):
        """F1 (review 2026-10-03, reproduced): sysctl KERN_PROCARGS2 on a running process returns its argv AND its
        environment to any process of the same user, from inside the seat sandbox too. The REAL launch around a fake
        claude whose tool child probes the claude process (its parent) exactly that way. The probe must READ the
        process (so a token in the environment would be seen) and must not find the login."""
        self.keychain(rc=0)
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        probe = ("import ctypes,ctypes.util,os,sys\n"
                 "c=ctypes.CDLL(ctypes.util.find_library('c'),use_errno=True)\n"
                 "m=(ctypes.c_int*3)(1,49,os.getppid());n=ctypes.c_size_t(0)\n"
                 "assert c.sysctl(m,3,None,ctypes.byref(n),None,0)==0\n"
                 "b=ctypes.create_string_buffer(n.value);assert c.sysctl(m,3,b,ctypes.byref(n),None,0)==0\n"
                 "d=b.raw[:n.value];t=os.environ['CANARY_SHAPE'].encode()\n"
                 "print('LEAK' if t in d else 'CLEAN', 'MARK' if b'BROTHER_PROBE_MARK=seen' in d else 'NOMARK', len(d), d.count(b'PATH='))\n")
        fake = os.path.join(self.root, "claude")
        # the fake is a Python program, not a shell script: macOS hides the environment of platform binaries (/bin/bash)
        # from this sysctl, measured, and the real CLI is not one; it holds the descriptor unread while its child probes
        with open(fake, "w") as fh:
            fh.write("#!%s\nimport json, os, subprocess, sys\n"
                     "env = dict(os.environ, CANARY_SHAPE=%r)\n"
                     "v = subprocess.run([sys.executable, '-c', os.environ['PROBE']], env=env, capture_output=True, text=True)\n"
                     "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'probe: ' + (v.stdout + v.stderr).strip()[-200:]}))\n"
                     % (os.path.realpath(sys.executable), CANARY[len(PREFIX):]))
        os.chmod(fake, 0o700)
        os.environ["PROBE"] = probe
        os.environ["BROTHER_PROBE_MARK"] = "seen"   # an ordinary variable the probe must see, proving it read the env
        self.addCleanup(os.environ.pop, "PROBE", None)
        self.addCleanup(os.environ.pop, "BROTHER_PROBE_MARK", None)
        NW.R.claude_bin = lambda: fake
        log = os.path.join(self.root, "log")
        ok, why = NW.run("X.1", "do X.1", self.base, MODEL, 60, self.out, self.repo, log, seat_count=1)
        with open(os.path.join(log, "claude.json")) as fh:
            said = fh.read()
        self.assertIn("probe: ", said, why)
        self.assertIn("MARK", said.replace("NOMARK", ""), "the probe read the claude process's environment: " + said)
        self.assertIn("probe: CLEAN", said, "the login is not in the claude process's environment: " + said)

    def test_on_an_inherited_token_never_passes_the_scrub(self):
        """F1, the second guard: whatever reaches session_argv on the token lane, a token NAME is unset before the CLI
        runs; only the descriptor's name passes."""
        env = {"PATH": "/usr/bin", BL.TOKEN_VAR: PREFIX + "INHERITED" + "a" * 24, BL.FD_VAR: "9"}
        a = NW.session_argv("m", "low", self.root, "/nonexistent/claude", env=env, token_on=True)
        self.assertEqual(a[a.index(BL.TOKEN_VAR) - 1], "-u")
        self.assertNotIn(BL.FD_VAR, a)

    def test_on_with_no_saved_login_parks_before_the_session(self):
        self.keychain(rc=44)
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        ok, why = self.go(runner=self.spy)
        self.assertNothing((ok, why), NW.MC.CONFIG_WAIT)
        self.assertIn("brother-login", why)
        self.assertEqual(self.calls, [], "no session starts without the login")
        self.assertEqual(self.rows(), [], "no ledger row for a session that never started")

    def test_on_with_an_unreadable_keychain_parks_before_the_session(self):
        self.keychain(rc=1)
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        self.assertNothing(self.go(runner=self.spy), NW.MC.CONFIG_WAIT)
        self.assertEqual(self.calls, [])

    def test_on_the_token_lane_is_the_only_lane(self):
        """Every headless Claude call passes _claude_run; with the switch on, one that is not a native seat is refused
        as a spawn failure (nothing sent) and a native one proceeds."""
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        path = self.ledger
        seen = []
        runner = lambda a, s, t: seen.append(a) or {"returncode": 0, "stdout": DONE, "stderr": ""}
        started = NW.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "native-t-1", "kind": "native"}, 10)
        with self.assertRaises(NW.MC.SpawnFailed) as cm:
            NW.MC._claude_run(path, started, ["/usr/local/bin/claude", "-p"], "x", 10, runner)
        self.assertIn("only native seats", str(cm.exception))
        self.assertEqual(seen, [])
        started = NW.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "native-t-2", "kind": "native"}, 10)
        NW.MC._claude_run(path, started, ["sandbox-exec", "-p", "x", "claude"], "x", 10, runner)
        self.assertEqual(len(seen), 1)
        os.environ["BROTHER_LOOP_TOKEN"] = "off"
        started = NW.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "native-t-3", "kind": "native"}, 10)
        NW.MC._claude_run(path, started, ["/usr/local/bin/claude", "-p"], "x", 10, runner)
        self.assertEqual(len(seen), 2, "off: every lane runs as today")

    def test_run_in_hands_the_given_environment_to_the_child(self):
        r = NW.MC._run_in([sys.executable, "-c", "import os; print(os.environ.get('BROTHER_T', 'absent'))"], "", 20, self.root,
                          env={"PATH": os.environ.get("PATH", ""), "BROTHER_T": "given"})
        self.assertEqual(r["stdout"].strip(), "given")
        r = NW.MC._run_in([sys.executable, "-c", "import os; print(os.environ.get('BROTHER_T', 'absent'))"], "", 20, self.root)
        self.assertEqual(r["stdout"].strip(), "absent", "no env: inherits, and this process has no BROTHER_T")

    def test_off_a_rejected_login_is_todays_claude_error(self):
        doc = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid."})
        ok, why = self.go(runner=lambda a, s, t, c: {"returncode": 1, "stdout": doc, "stderr": ""})
        self.assertNothing((ok, why), NW.CLAUDE_ERROR)
        self.assertNotIn("sign in again", why)

    def test_on_a_rejected_login_parks_and_names_brother_login(self):
        self.keychain(rc=0)
        os.environ["BROTHER_LOOP_TOKEN"] = "on"
        doc = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid."})
        ok, why = self.go(runner=lambda a, s, t, c, env=None: {"returncode": 1, "stdout": doc, "stderr": ""})
        self.assertNothing((ok, why), NW.MC.CONFIG_WAIT)
        self.assertIn("sign in again", why)
        self.assertNotIn(CANARY, why)


class TheSandboxGuardsWhatRunsIt(unittest.TestCase):
    """The REAL sandbox-exec with the module's own PROFILE, over a fake home. ONE PATH PER CASE."""

    def setUp(self):
        # under a real home path, as in production: the system temp folder is allowed by the profile, so a fake home
        # there would measure that allow instead of the home rules
        scratch = os.path.expanduser("~/.claude/brother-scratch")
        os.makedirs(scratch, exist_ok=True)
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="native-sandbox-", dir=scratch))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home, self.wt = os.path.join(self.root, "home"), os.path.join(self.root, "wt")
        for d in (".claude/hooks", ".claude/bin", ".claude/rules", ".claude/evidence", "Library/LaunchAgents", ".config/gh"):
            os.makedirs(os.path.join(self.home, d))
        os.makedirs(os.path.join(self.wt, ".tmp"))
        self.tool = os.path.join(self.root, "tooldir")
        os.makedirs(self.tool)
        with open(os.path.join(self.home, ".config/gh/hosts.yml"), "w") as fh:
            fh.write("token: x\n")

    def sh(self, script, common=None, token_on=False):
        return subprocess.run(["sandbox-exec", "-p", NW.profile(token_on), "-D", "WT=" + self.wt, "-D", "TMP=" + self.wt + "/.tmp",
                               "-D", "HOME=" + self.home, "-D", "TOOLDIR=" + self.tool, "-D", "COMMON=" + (common or self.wt),
                               "/bin/sh", "-c", script],
                              capture_output=True).returncode

    def write(self, rel):
        return self.sh("echo x > '%s'" % os.path.join(self.home, rel))

    def test_d10_the_token_lane_cannot_run_the_keychain_tool(self):
        """D10 (docs/decisions/native-run-2026-10-02.json) closed: on the loop token lane /usr/bin/security does not
        run in a seat, the Keychain daemon cannot be asked, and the keychain files cannot be read; ordinary build
        commands still run. The control proves the deny is load bearing: off, the same tool runs (a name that is not
        there, so the real Keychain is never asked for an item)."""
        self.assertNotEqual(self.sh("/usr/bin/security find-generic-password -s claude-loop-token", token_on=True), 0)
        self.assertNotEqual(self.sh("/usr/bin/security find-generic-password -s brother-no-such-item-canary", token_on=True), 0)
        self.assertEqual(self.sh("/usr/bin/security find-generic-password -s brother-no-such-item-canary", token_on=False), 44,
                         "control: off, the tool itself runs and reports the item missing")
        os.makedirs(os.path.join(self.home, "Library", "Keychains"))
        with open(os.path.join(self.home, "Library", "Keychains", "login.keychain-db"), "w") as fh:
            fh.write("x")
        self.assertNotEqual(self.sh("cat '%s/Library/Keychains/login.keychain-db'" % self.home, token_on=True), 0)
        self.assertEqual(self.sh("cat '%s/Library/Keychains/login.keychain-db'" % self.home, token_on=False), 0, "control: off, readable as today")
        self.assertEqual(self.sh("/usr/bin/true && python3 -c 'print(1)' && git --version", token_on=True), 0, "ordinary build commands still run")

    def exe(self, rel):
        """A real program (a copy of /usr/bin/true) at rel under the fake home or the seat."""
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile("/usr/bin/true", p)
        os.chmod(p, 0o700)
        return p

    def test_f2_a_seat_cannot_run_the_loops_keychain_reader_on_either_lane(self):
        """F2 (review 2026-10-03, reproduced): a read deny does not stop exec, so a seat on the off lane ran the trusted
        reader and read the login silently. ~/.claude/bin is not runnable on either lane."""
        p = self.exe("home/.claude/bin/brother-keychain")
        for on in (False, True):
            self.assertNotEqual(self.sh("'%s'" % p, token_on=on), 0, "lane on=%s" % on)

    def test_f2_on_the_token_lane_a_seat_cannot_run_a_program_from_a_closed_home_folder(self):
        """The reviewer's variant: the reader built at a HOME path no rule names. On the token lane unreadable means
        unrunnable. (Off, review 4: the same deny broke seat tools under ~/.asdf, ~/.rbenv and the like, so it is the
        token lane's alone; the off lane test below holds that.)"""
        p = self.exe("home/Projects/elsewhere/brother-keychain")
        self.assertNotEqual(self.sh("'%s'" % p, token_on=True), 0)

    def test_review4_the_off_lane_runs_tools_installed_anywhere_in_home_as_before(self):
        """Review 4 (2026-10-03): the HOME exec deny reached the off lane and broke tools under ~/.asdf, ~/.rbenv,
        ~/.deno, ~/Library/Python and ~/bin. Off, each runs again; on, each is refused (the control that keeps the token
        lane's deny load bearing)."""
        for rel in (".asdf/shims/t", ".rbenv/shims/t", ".deno/bin/t", "Library/Python/3.9/bin/t", "bin/t"):
            p = self.exe("home/" + rel)
            self.assertEqual(self.sh("'%s'" % p, token_on=False), 0, "off: " + rel)
            self.assertNotEqual(self.sh("'%s'" % p, token_on=True), 0, "on: " + rel)

    def test_f2_programs_in_the_seat_and_the_tool_roots_still_run(self):
        """The control that keeps the two denies above load bearing: the same program runs from the seat, its temp
        folder and an opened tool root, on both lanes."""
        for rel in ("wt/bin/t", "wt/.tmp/t", "home/.local/bin/t", "home/.nvm/versions/node/bin/t"):
            p = self.exe(rel)
            for on in (False, True):
                self.assertEqual(self.sh("'%s'" % p, token_on=on), 0, "%s on=%s" % (rel, on))

    def app(self, marker):
        """A two file .app in the seat whose program touches marker, a path the seat cannot write: the marker appearing
        means the program ran OUTSIDE the sandbox (review 4, F-A, reproduced with the loop's reader as the payload)."""
        bundle = os.path.join(self.wt, "X.app")
        os.makedirs(os.path.join(bundle, "Contents", "MacOS"))
        with open(os.path.join(bundle, "Contents", "Info.plist"), "w") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleExecutable</key><string>x</string>'
                     '<key>CFBundleIdentifier</key><string>test.brother.escape.%s</string><key>CFBundlePackageType</key>'
                     '<string>APPL</string></dict></plist>' % uuid.uuid4().hex[:12])
        prog = os.path.join(bundle, "Contents", "MacOS", "x")
        with open(prog, "w") as fh:
            fh.write("#!/bin/sh\ntouch '%s'\n" % marker)
        os.chmod(prog, 0o700)
        return bundle

    def escaped(self, script, on):
        """Run script in the seat; True when the .app's program ran outside the sandbox within a few seconds."""
        marker = os.path.join(self.root, "escaped")
        if os.path.exists(marker):
            os.remove(marker)
        self.sh(script, token_on=on)
        for _ in range(30):
            if os.path.exists(marker):
                return True
            time.sleep(0.2)
        return False

    def test_fa_a_seat_cannot_launch_an_app_outside_the_sandbox_on_either_lane(self):
        """F-A (review 4, 2026-10-03, CRITICAL, reproduced): open -g -n on a .app the seat wrote had LaunchServices
        start it OUTSIDE the sandbox, where the loop's reader printed the login into the worktree. Three routes, each
        refused on both lanes: /usr/bin/open, a re-signed copy of it in the seat (so the exec deny alone is not the
        control: the LaunchServices services are), and osascript's launch. The marker is written by the app."""
        bundle = self.app(os.path.join(self.root, "escaped"))
        own = os.path.join(self.wt, "myopen")
        shutil.copyfile("/usr/bin/open", own)
        os.chmod(own, 0o700)
        self.assertEqual(subprocess.run(["codesign", "-f", "-s", "-", own], capture_output=True).returncode, 0)
        self.assertNotEqual(self.sh("touch '%s'" % os.path.join(self.root, "escaped")), 0, "fixture: the marker is unwritable in a seat")
        for on in (False, True):
            self.assertFalse(self.escaped("/usr/bin/open -g -n '%s'" % bundle, on), "open, on=%s" % on)
            self.assertFalse(self.escaped("'%s' -g -n '%s'" % (own, bundle), on), "a copy of open, on=%s" % on)
            self.assertFalse(self.escaped("/usr/bin/python3 -c \"import ctypes;cs=ctypes.CDLL('/System/Library/Frameworks/"
                                          "CoreServices.framework/CoreServices');cf=ctypes.CDLL('/System/Library/Frameworks/"
                                          "CoreFoundation.framework/CoreFoundation');f=cf.CFURLCreateFromFileSystemRepresentation;"
                                          "f.restype=ctypes.c_void_p;f.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_long,"
                                          "ctypes.c_bool];p=b'%s';cs.LSOpenCFURLRef.argtypes=[ctypes.c_void_p,ctypes.c_void_p];"
                                          "cs.LSOpenCFURLRef(f(None,p,len(p),True),None)\"" % bundle, on), "LaunchServices API, on=%s" % on)

    def test_fa_the_launchservices_front_ends_do_not_run_in_a_seat(self):
        """The second layer of F-A: open, osascript, lsappinfo, shortcuts and automator are refused at exec (126,
        Operation not permitted) on both lanes, whatever their arguments; the services above stay the control for copies."""
        for tool in ("/usr/bin/open", "/usr/bin/osascript", "/usr/bin/lsappinfo", "/usr/bin/shortcuts", "/usr/bin/automator"):
            if os.path.exists(tool):
                for on in (False, True):
                    self.assertEqual(self.sh("'%s' -h </dev/null >/dev/null 2>&1" % tool, token_on=on), 126, "%s on=%s" % (tool, on))

    def test_fa_ordinary_build_commands_still_run_under_the_launchservices_deny(self):
        """The control for the test above: python3, git and node (where installed) still run in a seat on both lanes."""
        node = shutil.which("node")
        for on in (False, True):
            self.assertEqual(self.sh("python3 -c 'print(1)' && git --version", token_on=on), 0, "on=%s" % on)
            if node and not node.startswith(os.path.expanduser("~")):
                self.assertEqual(self.sh("'%s' --version" % node, token_on=on), 0, "node on=%s" % on)

    def test_the_installed_loop_is_not_writable(self):
        self.assertNotEqual(self.write(".claude/bin/unit_runner.py"), 0)

    def test_the_hooks_are_not_writable(self):
        self.assertNotEqual(self.write(".claude/hooks/guard.py"), 0)

    def test_the_rules_are_not_writable(self):
        self.assertNotEqual(self.write(".claude/rules/law.md"), 0)

    def test_settings_are_not_writable(self):
        self.assertNotEqual(self.write(".claude/settings.json"), 0)

    def test_claude_md_is_not_writable(self):
        self.assertNotEqual(self.write(".claude/CLAUDE.md"), 0)

    def test_the_spend_guard_is_not_writable(self):
        self.assertNotEqual(self.write(".claude/spend-guard.json"), 0)

    def test_skills_plugins_agents_and_commands_are_not_writable(self):
        for d in ("skills", "plugins", "agents", "commands"):
            os.makedirs(os.path.join(self.home, ".claude", d), exist_ok=True)
            self.assertNotEqual(self.write(".claude/%s/x.md" % d), 0, d)

    def test_local_settings_are_not_writable(self):
        self.assertNotEqual(self.write(".claude/settings.local.json"), 0)

    def test_launch_agents_are_not_writable(self):
        self.assertNotEqual(self.write("Library/LaunchAgents/x.plist"), 0)

    def test_the_github_token_is_not_readable(self):
        self.assertNotEqual(self.sh("cat '%s'" % os.path.join(self.home, ".config/gh/hosts.yml")), 0)

    def test_the_loops_evidence_is_not_writable(self):
        self.assertNotEqual(self.write(".claude/evidence/claude-calls.jsonl"), 0)

    def test_library_is_not_writable(self):
        os.makedirs(os.path.join(self.home, "Library", "Caches"), exist_ok=True)
        self.assertNotEqual(self.write("Library/Caches/x"), 0)

    def test_home_secrets_are_not_readable(self):
        for rel in (".npmrc", ".fcc/.env", "Documents/client/notes.md"):
            p = os.path.join(self.home, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("token = x\n")
            self.assertNotEqual(self.sh("cat '%s'" % p), 0, rel)

    def test_session_transcripts_are_not_readable(self):
        p = os.path.join(self.home, ".claude", "projects", "x", "s.jsonl")
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as fh:
            fh.write("{}\n")
        self.assertNotEqual(self.sh("cat '%s'" % p), 0)

    def test_git_works_in_a_seat_only_through_its_own_git_dir(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        repo = os.path.join(self.home, "repo")   # under HOME, as ~/Brother is
        os.makedirs(repo)
        for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "b"], ["worktree", "add", "-q", "--detach", self.wt + "/seat"]):
            subprocess.run(["git"] + a, cwd=repo, env=env, check=True, capture_output=True)
        common = os.path.join(repo, ".git")
        cmd = "cd '%s/seat' && GIT_CONFIG_GLOBAL=/dev/null git status --short" % self.wt
        self.assertEqual(self.sh(cmd, common=common), 0)
        self.assertNotEqual(self.sh(cmd), 0, "control: without its git dir readable, git fails in the seat")

    def test_git_identity_and_tool_roots_are_open(self):
        with open(os.path.join(self.home, ".gitconfig"), "w") as fh:
            fh.write("[user]\n\tname = t\n")
        self.assertEqual(self.sh("cat '%s'" % os.path.join(self.home, ".gitconfig")), 0)
        # a script, like a node or python CLI, must be READ by its interpreter: that is what a closed home refuses
        # (a copied system binary such as ls is killed by macOS code signing anywhere, so it measures nothing here)
        for where, expect_ok in ((".nvm/versions/bin", True), ("weird/bin", False)):
            d = os.path.join(self.home, where)
            os.makedirs(d)
            with open(os.path.join(d, "tool"), "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(os.path.join(d, "tool"), 0o755)
            rc = self.sh("'%s/tool'" % d)
            self.assertEqual(rc == 0, expect_ok, (where, rc))

    def put(self, rel, text="token = x\n"):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_credential_stores_inside_opened_roots_stay_shut(self):
        # fifth review 2026-10-02: each store sits in a folder the profile opens; a sibling file proves the folder is open,
        # so a refusal here measures the store's own deny and nothing else
        pairs = {".cargo/credentials.toml": ".cargo/config.toml", ".cargo/credentials": ".cargo/config.toml",
                 ".npm-global/etc/npmrc": ".npm-global/lib/x.js", ".config/git/credentials": ".config/git/config",
                 ".conda/.condarc": ".conda/environments.txt", ".local/share/keyrings/login": ".local/share/x",
                 ".local/share/gh/hosts.yml": ".local/share/x"}
        stores = {s.lstrip("/") for s in NW.CREDENTIAL_STORES}
        self.assertEqual(stores, {k if k in stores else os.path.dirname(k) for k in pairs})  # every store has a case
        for store, control in pairs.items():
            self.assertEqual(self.sh("cat '%s'" % self.put(control)), 0, ("control", control))
            self.assertNotEqual(self.sh("cat '%s'" % self.put(store)), 0, store)

    def test_git_config_files_open_and_nothing_else_in_config_git(self):
        self.assertEqual(self.sh("cat '%s'" % self.put(".config/git/config", "[user]\n")), 0)
        self.assertEqual(self.sh("cat '%s'" % self.put(".config/git/ignore", "*.o\n")), 0)
        self.assertNotEqual(self.sh("cat '%s'" % self.put(".config/git/credentials")), 0)
        self.assertNotEqual(self.sh("cat '%s'" % self.put(".config/git/other")), 0)

    def test_the_bash_tools_cwd_file_is_writable_and_nothing_else_in_tmp(self):
        ok = "/private/tmp/claude-%x-cwd" % (0xab00 + os.getpid() % 0xff)
        self.addCleanup(lambda: os.path.exists(ok) and os.remove(ok))
        self.assertEqual(self.sh("echo /x > '%s'" % ok), 0)
        self.assertNotEqual(self.sh("echo /x > /private/tmp/claude-not-a-cwd-file-%d" % os.getpid()), 0)

    def test_the_clis_own_state_stays_readable(self):
        with open(os.path.join(self.home, ".claude.json"), "w") as fh:
            fh.write("{}\n")
        self.assertEqual(self.sh("cat '%s'" % os.path.join(self.home, ".claude.json")), 0)

    def test_only_the_seats_own_tool_folder_is_writable_in_tmp(self):
        self.assertEqual(self.sh("echo x > '%s/ok'" % self.tool), 0)
        other = "/private/tmp/claude-%d/native-test-other-session-%d" % (os.getuid(), os.getpid())
        self.assertNotEqual(self.sh("mkdir -p '%s'" % other), 0)
        self.assertFalse(os.path.exists(other))

    def test_the_ssh_agent_socket_is_unreachable(self):
        import socket
        d = "/private/tmp/com.apple.launchd.nativetest%d" % os.getpid()
        os.makedirs(d, exist_ok=True)
        self.addCleanup(shutil.rmtree, d, True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(os.path.join(d, "Listeners"))
        srv.listen(1)
        self.addCleanup(srv.close)
        probe = "import socket; s=socket.socket(socket.AF_UNIX); s.connect('%s/Listeners')" % d
        self.assertNotEqual(self.sh("/usr/bin/python3 -c \"%s\"" % probe), 0)
        self.assertEqual(subprocess.run(["/usr/bin/python3", "-c", probe]).returncode, 0, "control: reachable outside")

    def test_the_seat_stays_writable(self):
        self.assertEqual(self.sh("echo x > '%s'" % os.path.join(self.wt, "edited.py")), 0)

    def test_the_credential_helper_and_gh_cannot_run(self):
        b = os.path.join(self.root, "bin")
        os.makedirs(b)
        for name in ("git-credential-osxkeychain", "gh", "harmless"):
            shutil.copy("/usr/bin/true", os.path.join(b, name))
        self.assertNotEqual(self.sh("'%s/git-credential-osxkeychain'" % b), 0)
        self.assertNotEqual(self.sh("'%s/gh'" % b), 0)
        self.assertEqual(self.sh("'%s/harmless'" % b), 0, "control: an ordinary program still runs")


class NoPushFromASeat(unittest.TestCase):
    def test_every_push_url_is_rewritten_and_no_credential_helper_is_left(self):
        d = tempfile.mkdtemp(prefix="native-push-")
        self.addCleanup(shutil.rmtree, d, True)
        glob = os.path.join(d, "global.gitconfig")   # as on this machine: the keychain helper is configured globally
        with open(glob, "w") as fh:
            fh.write("[credential]\n\thelper = osxkeychain\n")
        env = dict(os.environ, GIT_CONFIG_GLOBAL=glob, GIT_CONFIG_NOSYSTEM="1")
        env.update(kv.split("=", 1) for kv in NW.PUSH_BLOCK)
        git = lambda *a: subprocess.run(["git"] + list(a), cwd=d, env=env, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        for name, url in (("a", "https://github.com/o/r.git"), ("b", "git@github.com:o/r.git"), ("c", "ssh://git@h/o/r.git")):
            git("remote", "add", name, url)
            self.assertTrue(git("remote", "get-url", "--push", name).startswith("file:///dev/null/push-blocked/"), name)
        raw = subprocess.run(["git", "config", "--get-all", "credential.helper"], cwd=d, env=env, capture_output=True,
                             text=True).stdout
        self.assertEqual(raw, "osxkeychain\n\n", "the empty value comes last, which resets the helper list")

    def test_the_session_carries_the_block(self):
        a = NW.session_argv("claude-opus-5-5", "medium", "/tmp/wt", "/c")
        for kv in NW.PUSH_BLOCK:
            self.assertIn(kv, a)
        self.assertEqual(a[a.index("env") + 1:a.index("env") + 7], ["-u", "GH_TOKEN", "-u", "GITHUB_TOKEN", "-u", "SSH_AUTH_SOCK"])
        self.assertIn("HOME=" + os.path.realpath(os.path.expanduser("~")), a)

    def test_no_budget_setting_means_no_budget_flag(self):
        # the delegated Sonnet trial, 2026-10-03: unset keeps the argv exactly as before the setting existed
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BROTHER_NATIVE_BUDGET_USD", None)
            a = NW.session_argv("claude-sonnet-5-5", "medium", "/tmp/wt", "/c")
        self.assertNotIn("--max-budget-usd", a)

    def test_a_budget_setting_rides_on_the_session(self):
        with mock.patch.dict(os.environ, {"BROTHER_NATIVE_BUDGET_USD": "3"}):
            a = NW.session_argv("claude-sonnet-5-5", "medium", "/tmp/wt", "/c")
        self.assertEqual(a[a.index("--max-budget-usd") + 1], "3")

    def test_an_unreadable_budget_refuses_and_never_runs_without_one(self):
        for raw in ("three", "0", "nan", "-1", "1000"):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"BROTHER_NATIVE_BUDGET_USD": raw}):
                with self.assertRaises(ValueError):
                    NW.session_argv("claude-sonnet-5-5", "medium", "/tmp/wt", "/c")

    def test_the_budget_is_a_run_knob_that_never_reaches_a_suite(self):
        import loop_switches as LS
        self.assertNotIn("BROTHER_NATIVE_BUDGET_USD", LS.suite_env({"BROTHER_NATIVE_BUDGET_USD": "3", "PATH": "/bin"}, "/tmp/h"))

    def test_credential_shaped_variables_are_stripped_by_name(self):
        # sixth review 2026-10-02: the session inherited the loop's whole environment, keys included
        with mock.patch.dict(os.environ, {"FAKE_REVIEW_API_KEY": "v1", "SOME_SERVICE_TOKEN": "v2", "PLAIN_SETTING": "v3"}):
            a = NW.session_argv("claude-opus-5-5", "medium", "/tmp/wt", "/c")
        env = a[a.index("env"):a.index(next(x for x in a if x.startswith("TMPDIR=")))]
        for name in ("FAKE_REVIEW_API_KEY", "SOME_SERVICE_TOKEN"):
            self.assertIn(name, env)
            self.assertEqual(env[env.index(name) - 1], "-u")
        self.assertNotIn("PLAIN_SETTING", a)
        self.assertFalse({"v1", "v2", "v3"} & set(a), "only names ride in the argv, never a value")


class Unseated(unittest.TestCase):
    """fanout_verdict.all_unseated, ONE CONDITION PER CASE."""

    def verdict(self, rows):
        d = tempfile.mkdtemp(prefix="unseated-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "results.json")
        with open(p, "w") as fh:
            json.dump(rows, fh)
        return FV.all_unseated(p)

    WAIT = {"ok": False, "seat_wait": True, "claude_error": False}
    CLAUDE = {"ok": False, "seat_wait": False, "claude_error": True}
    OTHER = {"ok": False, "seat_wait": False, "claude_error": False}

    def test_a_wait_beside_a_claude_error_is_unseated(self):
        self.assertIs(self.verdict([self.WAIT, self.CLAUDE]), True)

    def test_a_wait_beside_a_real_failure_is_a_round(self):
        self.assertIs(self.verdict([self.WAIT, self.OTHER]), False)

    def test_claude_errors_alone_are_not_unseated(self):
        self.assertIs(self.verdict([self.CLAUDE, self.CLAUDE]), False)

    def test_unreadable_is_none(self):
        self.assertIsNone(self.verdict([]))


class Seats(unittest.TestCase):
    def test_seats_knob(self):
        self.assertEqual(NW.seats({}), NW.DEFAULT_SEATS)
        self.assertEqual(NW.seats({"BROTHER_NATIVE_SEATS": "3"}), 3)
        for bad in ("0", "-1", "two", "1.5"):
            with self.assertRaises(ValueError):
                NW.seats({"BROTHER_NATIVE_SEATS": bad})


class SessionCap(unittest.TestCase):
    def test_session_cap_knob(self):
        self.assertEqual(NW.session_seconds({}), NW.DEFAULT_SESSION_S)
        self.assertEqual(NW.DEFAULT_SESSION_S, 2700)
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "3600"}), 3600)
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "600"}), 600)
        for bad in ("599", "7201", "x", "-1", "1.5"):
            with self.assertRaises(ValueError):
                NW.session_seconds({"BROTHER_NATIVE_SESSION_S": bad})


class NativeIsStandard(unittest.TestCase):
    """loop_switches.native_on: on unless exactly off, never without sandbox-exec. ONE CONDITION PER CASE."""
    have = staticmethod(lambda cmd, path=None: "/usr/bin/" + cmd)
    lack = staticmethod(lambda cmd, path=None: None)

    def test_unset_is_on(self):
        self.assertTrue(NW_SW.native_on({}, which=self.have))

    def test_off_is_off_whatever_its_case(self):
        for v in ("off", " OFF ", "Off"):
            self.assertFalse(NW_SW.native_on({"BROTHER_CLAUDE_NATIVE": v}, which=self.have))

    def test_any_other_value_keeps_the_standard(self):
        for v in ("on", "1", "no"):
            self.assertTrue(NW_SW.native_on({"BROTHER_CLAUDE_NATIVE": v}, which=self.have))

    def test_no_sandbox_exec_is_off(self):
        self.assertFalse(NW_SW.native_on({}, which=self.lack))

    def test_the_real_lookup_reads_the_path_given(self):
        self.assertFalse(NW_SW.native_on({"PATH": "/nonexistent"}))


class Brief(unittest.TestCase):
    def test_section_keeps_subheadings_and_stops_at_its_level(self):
        sec = NW.spec_section(SPEC, "X.1")
        self.assertIn("Do one.", sec)
        self.assertIn("Still part of X.1.", sec)
        self.assertNotIn("Not X.1.", sec)

    def test_a_longer_id_listed_first_is_never_taken(self):
        spec = "## X.10 the longer one\nNOT X.1.\n\n## X.1 the one\nTHE ONE.\n"
        self.assertEqual(NW.spec_section(spec, "X.1"), "## X.1 the one\nTHE ONE.\n")

    def test_section_never_matches_a_longer_id(self):
        self.assertIn("Not X.1.", NW.spec_section(SPEC, "X.10"))
        self.assertIsNone(NW.spec_section(SPEC, "X.2"))
        self.assertIsNone(NW.spec_section(None, "X.1"))

    def test_brief_carries_contract_note_and_section(self):
        b = NW.brief("X.1", "SECTION TEXT", "CONTRACT TEXT", note="RED-1 test_x failed")
        for part in ("SECTION TEXT", "CONTRACT TEXT", "RED-1 test_x failed", ".brother/mutations.json"):
            self.assertIn(part, b)
        self.assertNotIn("PREVIOUS ROUND", NW.brief("X.1", "S", "C"))

    def test_brief_sends_the_session_to_its_neighbour_suites(self):
        b = NW.brief("X.1", "S", "C")
        self.assertIn("Find every EXISTING test that loads a file you change", b)
        self.assertIn("add it to the stub", b)

    def test_contract_is_the_graders_own(self):
        c = NW.contract_text([])
        with open(os.path.join(HERE, "brief_head.md"), encoding="utf-8") as fh:
            self.assertTrue(c.startswith(fh.read()))


class Fanout(Base):
    def jobs(self, n=2, **extra):
        pf = os.path.join(self.root, "prompt.md")
        with open(pf, "w") as fh:
            fh.write("do X.1")
        jobs = [dict({"id": "X.1-r%d" % i, "model": MODEL, "prompt_file": pf,
                      "out": os.path.join(self.root, "out", "X.1-r%d-build.json" % i)}, **extra) for i in range(n)]
        jf = os.path.join(self.root, "jobs.json")
        with open(jf, "w") as fh:
            json.dump(jobs, fh)
        return jf, jobs

    def main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return NW.main(argv), buf.getvalue()

    def test_every_job_builds_on_its_own_seat(self):
        jf, jobs = self.jobs()
        res = os.path.join(self.root, "results.json")
        rows = NW.fanout(jobs, self.repo, self.base, 60, results=res, seat_count=2, runner=good_worker)
        self.assertEqual([r["ok"] for r in rows], [True, True], rows)
        for j in jobs:
            self.assertTrue(os.path.isfile(j["out"]))
        self.assertIs(FV.all_config_wait(res), False)

    def test_config_wait_rows_read_as_config_wait(self):
        self.held = "unknown model"
        jf, jobs = self.jobs()
        res = os.path.join(self.root, "results.json")
        NW.fanout(jobs, self.repo, self.base, 60, results=res, seat_count=2, runner=good_worker)
        self.assertIs(FV.all_config_wait(res), True)

    def test_a_claude_error_result_is_flagged_and_reads_as_all_claude_errors(self):
        bad = json.dumps({"is_error": True, "result": "usage limit", "total_cost_usd": 0})
        jf, jobs = self.jobs()
        res = os.path.join(self.root, "results.json")
        rows = NW.fanout(jobs, self.repo, self.base, 60, results=res, seat_count=2,
                         runner=lambda a, s, t, c: good_worker(a, s, t, c, stdout=bad))
        self.assertEqual([r["claude_error"] for r in rows], [True, True], rows)
        self.assertIs(FV.all_claude_errors(res), True)

    def test_any_other_failure_is_not_a_claude_error(self):
        jf, jobs = self.jobs()
        res = os.path.join(self.root, "results.json")
        rows = NW.fanout(jobs, self.repo, self.base, 60, results=res, seat_count=2,
                         runner=lambda a, s, t, c: good_worker(a, s, t, c, muts=MUTS[:2]))   # an adapter refusal
        self.assertEqual([r["claude_error"] for r in rows], [False, False], rows)
        self.assertIs(FV.all_claude_errors(res), False)

    def test_a_seat_wait_is_flagged_and_reads_as_unseated(self):
        jf, jobs = self.jobs(n=2)
        res = os.path.join(self.root, "results.json")
        os.makedirs(NW.seat_root(), exist_ok=True)
        with NW.seat(1, 5):   # the only seat is held for the whole round
            rows = NW.fanout(jobs, self.repo, self.base, 1, results=res, seat_count=1, runner=good_worker)
        self.assertEqual([r["seat_wait"] for r in rows], [True, True], rows)
        self.assertIs(FV.all_unseated(res), True)
        self.assertIs(FV.all_claude_errors(res), False)

    def test_the_fan_out_hands_each_job_its_effort(self):
        _, jobs = self.jobs(n=1, effort="xhigh")
        seen = {}
        def spy(a, s, t, c):
            seen["argv"] = a
            return good_worker(a, s, t, c)
        NW.fanout(jobs, self.repo, self.base, 60, seat_count=1, runner=spy)
        self.assertEqual(seen["argv"][seen["argv"].index("--effort") + 1], "xhigh")

    def test_main_refuses_a_malformed_jobs_file(self):
        jf = os.path.join(self.root, "bad.json")
        with open(jf, "w") as fh:
            fh.write("{")
        rc, said = self.main([jf, "--repo", self.repo, "--base", self.base, "--timeout", "60"])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", said)

    def test_main_refuses_missing_flags(self):
        jf, _ = self.jobs()
        self.assertEqual(self.main([jf, "--repo", self.repo, "--timeout", "60"])[0], 2)

    def test_main_reports_a_failed_job(self):
        jf, _ = self.jobs(n=1, model="deepseek")
        res = os.path.join(self.root, "results.json")
        rc, said = self.main([jf, "--repo", self.repo, "--base", self.base, "--timeout", "60", "--results", res])
        self.assertEqual(rc, 1)
        self.assertEqual(load(res)[0]["ok"], False)

    def test_a_job_with_no_prompt_file_is_a_refused_row(self):
        _, jobs = self.jobs(n=1)
        del jobs[0]["prompt_file"]
        rows = NW.fanout(jobs, self.repo, self.base, 60, seat_count=1, runner=good_worker)
        self.assertEqual(rows[0]["ok"], False)
        self.assertIn("malformed job", rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
