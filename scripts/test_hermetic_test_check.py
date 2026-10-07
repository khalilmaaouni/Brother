"""hermetic_test_check.py driven both ways on canned answers, in its own
temporary root so it needs nothing from the tree or the home it runs in."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hermetic_test_check as H  # noqa: E402


class Runner(object):
    """A canned subprocess.run. `calls` records the test's own argv (the part from the interpreter on, the shape the
    check built before the sandbox wrapped it); `boxed` records the whole argv as the sandbox received it."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls = []
        self.boxed = []

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        self.boxed.append(cmd)
        if sys.executable in cmd:
            cmd = cmd[cmd.index(sys.executable):]
        key = os.path.basename(cmd[1]) if cmd[0] == sys.executable else " ".join(cmd[:2])
        self.calls.append((key, list(cmd), kw))
        answer = self.answers.get(key, (0, "OK\n"))
        if isinstance(answer, list):  # one per call, in order: the export run, then the checkout rerun
            answer = answer.pop(0)
        code, out = answer
        if code == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, code, out, "")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-hermetic-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts"))
        for name in ("test_door.py", "door.py", "lonely.py", "test_private.py"):
            open(os.path.join(self.root, "scripts", name), "w").close()
        # The checkout is a git repository with its scripts committed: the rerun runs in a disposable worktree of its
        # HEAD (review 14 finding 2, 2026-10-02), so a root with no HEAD has nothing to rerun in.
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "scripts")
        # The checkout rerun's empty HOME lands here, never in the real home's scratch root.
        self.scratch = os.path.join(self.root, "scratch")
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {"BROTHER_SCRATCH": self.scratch})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BROTHER_SANDBOX", None)   # restored by the patcher: an opt out would unwrap every run below
        # The canned runner never executes what it is handed, so the wrap is pinned present here and the argv it builds is
        # the real one on every host; TheSandboxOnThisHost below is where the wrap actually confines a process.
        present = mock.patch.object(H.G, "_sandbox_present", return_value=True)
        present.start()
        self.addCleanup(present.stop)
        # ...and so is the host's answer to "can a profile apply here" (sandbox_ready asks it since 2026-10-03). These are
        # PORTABLE ORCHESTRATION tests: inside the hermetic push check, itself sandboxed, the real probe reads "a sandbox
        # inside a sandbox" and every canned run would turn NO-DATA without one process having been started. The kernel
        # sandbox itself is tested only in TheSandboxOnThisHost, which skips (NO-DATA, never a pass) where it cannot run.
        applies = mock.patch.object(H.G, "_sandbox_applies", return_value="")
        applies.start()
        self.addCleanup(applies.stop)

    def git(self, *args):
        """git over the fixture checkout, hooks off, a fixed identity, no GIT_ variable from the host."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                            "-c", "core.hooksPath=/dev/null"] + list(args), cwd=self.root, capture_output=True, text=True,
                           env=env, timeout=60)
        if r.returncode != 0:
            raise AssertionError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
        return r.stdout

    def ships(self, *names):
        def build(dest):
            os.makedirs(os.path.join(dest, "scripts"))
            for name in names:
                open(os.path.join(dest, "scripts", name), "w").close()
        return build


class WhichTestsAChangeTouches(Base):
    def test_a_changed_test_is_itself_and_a_changed_script_is_its_test(self):
        self.assertEqual(H.tests_for(["scripts/test_door.py"], self.root),
                         ["scripts/test_door.py"])
        self.assertEqual(H.tests_for(["scripts/door.py"], self.root),
                         ["scripts/test_door.py"])

    def test_both_changed_names_the_test_once(self):
        self.assertEqual(H.tests_for(["scripts/door.py", "scripts/test_door.py"],
                                     self.root), ["scripts/test_door.py"])

    def test_what_is_not_a_scripts_python_file_is_not_this_checks_business(self):
        self.assertEqual(H.tests_for(["README.md", "scripts/lonely.py", "scripts/x.sh",
                                      "products/a/test_door.py", ""], self.root), [])

    def test_changed_paths_reads_added_and_modified_from_the_range(self):
        r = Runner({"git log": (0, "scripts/door.py\n\nscripts/door.py\nREADME.md\n")})
        paths, problem = H.changed_paths(self.root, ["a..b"], r)
        self.assertEqual((paths, problem), (["README.md", "scripts/door.py"], None))
        cmd = r.calls[0][1]
        self.assertIn("--diff-filter=AM", cmd)
        self.assertEqual(cmd[-1], "a..b")

    def test_an_unreadable_range_is_a_problem_never_an_empty_list(self):
        r = Runner({"git log": (128, "fatal: bad revision\n")})
        paths, problem = H.changed_paths(self.root, ["a..b"], r)
        self.assertIsNone(paths)
        self.assertIn("bad revision", problem)


class EachTestGetsItsOwnHome(Base):
    """2026-09-28: a test that leaves files under its HOME (the straggler harness, the grader suite) made the shared
    HOME's rmdir cleanup refuse the next test, and the whole pre-push gate crashed with no finding in the tree."""

    def test_a_test_that_leaves_files_does_not_break_the_next(self):
        homes = []

        class Litters(Runner):
            def __call__(self, cmd, **kw):
                home = kw["env"]["HOME"]
                homes.append(home)
                os.makedirs(os.path.join(home, ".claude", "evidence"), exist_ok=True)
                with open(os.path.join(home, ".claude", "evidence", "left.txt"), "w") as fh:
                    fh.write("left behind\n")
                return Runner.__call__(self, cmd, **kw)

        def build(dest):
            os.makedirs(os.path.join(dest, "scripts"))
            for name in ("test_one.py", "test_two.py"):
                with open(os.path.join(dest, "scripts", name), "w") as fh:
                    fh.write('"""doc"""\n')

        res = H.check(self.root, ["scripts/test_one.py", "scripts/test_two.py"], Litters(), build=build)
        self.assertEqual([v for v, _, _ in res], [H.OK, H.OK], res)
        self.assertEqual(len(set(homes)), 2, homes)


class TheDeclaredBudget(Base):
    """A legitimately long suite (the proof pair rehearsal waits in real time) declares its own budget in its first
    lines; the run is still complete, only its deadline moves, and never past the cap."""

    def ships_with(self, line):
        def build(dest):
            os.makedirs(os.path.join(dest, "scripts"))
            with open(os.path.join(dest, "scripts", "test_door.py"), "w") as fh:
                fh.write('"""doc"""\n' + line + "\n")
        return build

    def timeout_used(self, line):
        r = Runner()
        H.check(self.root, ["scripts/test_door.py"], r, build=self.ships_with(line))
        return r.calls[0][2]["timeout"]

    def test_a_declared_budget_is_the_timeout(self):
        self.assertEqual(self.timeout_used("# hermetic-budget-seconds: 2400"), 2400)

    def test_the_rehearsals_own_declaration_line_is_read(self):
        with open(os.path.join(HERE, "test_proof_pair_rehearsal.py"), encoding="utf-8") as fh:
            line = next(l.rstrip("\n") for l in fh if l.startswith("# hermetic-budget-seconds:"))
        self.assertEqual(self.timeout_used(line), 2400)

    def test_a_budget_over_the_cap_is_capped(self):
        self.assertEqual(self.timeout_used("# hermetic-budget-seconds: 99999"), H.BUDGET_CAP_S)

    def test_no_or_garbled_declaration_keeps_the_default(self):
        self.assertEqual(self.timeout_used("# hermetic-budget-seconds: soon"), H.TIMEOUT_S)
        self.assertEqual(self.timeout_used("x = 1"), H.TIMEOUT_S)


class TheRun(Base):
    def test_nothing_touched_is_ok_and_builds_nothing(self):
        built = []
        res = H.check(self.root, [], Runner(), build=built.append)
        self.assertEqual([r[0] for r in res], [H.OK])
        self.assertEqual(built, [])

    def test_a_test_that_fails_there_refuses_and_says_why(self):
        r = Runner({"test_door.py": [(1, "FAILED (failures=1, errors=1)\n"), (0, "OK\n")]})
        res = H.check(self.root, ["scripts/test_door.py"], r,
                      build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("failures=1", res[0][2])
        self.assertIn("own fixture", res[0][2])
        self.assertNotIn("red in the checkout too", res[0][2])
        self.assertNotIn("failing:", res[0][2])  # no FAIL/ERROR line, so no empty list

    def test_red_in_the_checkout_too_refuses_and_says_it_is_not_hermeticity(self):
        """2026-09-26: two of three refusals were red in the full checkout too
        (missing implementation, real lint hit); "own fixture" sent the fixer
        the wrong way."""
        r = Runner({"test_door.py": [(1, "FAILED (failures=20)\n"), (1, "FAILED (failures=20)\n")]})
        res = H.check(self.root, ["scripts/test_door.py"], r,
                      build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("red in the checkout too: not a hermeticity defect, "
                      "fix the code or the test", res[0][2])
        self.assertNotIn("own fixture", res[0][2])
        self.assertEqual(H.P.exit_code(res), H.P.EXIT_REFUSED)

    def test_a_checkout_rerun_that_cannot_run_says_unknown_and_still_refuses(self):
        r = Runner({"test_door.py": [(1, "FAILED\n"), ("timeout", "")]})
        res = H.check(self.root, ["scripts/test_door.py"], r,
                      build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("could not run (timed out", res[0][2])
        self.assertNotIn("own fixture", res[0][2])
        self.assertNotIn("red in the checkout too", res[0][2])
        self.assertEqual(H.P.exit_code(res), H.P.EXIT_REFUSED)

    def test_no_empty_home_for_the_rerun_says_unknown_and_still_refuses(self):
        from unittest import mock
        open(self.scratch, "w").close()  # a file where the scratch root should be
        r = Runner({"test_door.py": (1, "FAILED\n")})
        with mock.patch.object(H, "_keep", return_value="unused"):
            res = H.check(self.root, ["scripts/test_door.py"], r,
                          build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("no empty HOME could be made", res[0][2])
        self.assertEqual(len(r.calls), 1)

    def test_the_rerun_is_a_disposable_worktree_of_the_checkouts_head_under_a_fresh_empty_scratch_home(self):
        # review 14 finding 2 (2026-10-02): the rerun ran IN the checkout under a sandbox rooted there, so a red test
        # wrote into the landing tree, the lander could not unwind ("tree not clean") and every later landing refused
        from unittest import mock
        r = Runner({"test_door.py": [(1, "FAILED\n"), (1, "FAILED\n")]})
        with mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git"}):
            H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        self.assertEqual(len(r.calls), 2)
        _, cmd, kw = r.calls[1]
        tree = kw["cwd"]
        self.assertNotEqual(os.path.realpath(tree), os.path.realpath(self.root), "the rerun ran in the checkout itself")
        self.assertTrue(tree.startswith(os.path.join(self.scratch, "run-hermetic-worktree-")), tree)
        self.assertEqual(cmd[1], os.path.join(tree, "scripts/test_door.py"))
        home = kw["env"]["HOME"]
        self.assertTrue(home.startswith(os.path.join(self.scratch, "run-")), home)
        self.assertNotEqual(home, r.calls[0][2]["env"]["HOME"])
        self.assertFalse([k for k in kw["env"] if k.startswith("GIT_")])
        self.assertFalse(os.path.exists(home), "the rerun's HOME was left behind")
        self.assertFalse(os.path.exists(tree), "the rerun's worktree was left behind")
        listed = self.git("worktree", "list", "--porcelain")
        self.assertEqual(listed.count("worktree "), 1, "git still registers the disposable worktree: " + listed)

    def test_a_test_missing_from_the_checkouts_head_cannot_be_rerun_and_says_so(self):
        # an uncommitted test is not in HEAD, so the worktree has no copy to run: unknown, never "red in the checkout too"
        open(os.path.join(self.root, "scripts", "test_new.py"), "w").close()
        r = Runner({"test_new.py": (1, "FAILED\n")})
        res = H.check(self.root, ["scripts/test_new.py"], r, build=self.ships("test_new.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("could not run (", res[0][2])
        self.assertIn("not in the checkout's HEAD", res[0][2])
        self.assertNotIn("red in the checkout too", res[0][2])
        self.assertEqual(len(r.calls), 1, "nothing ran in the checkout")

    def test_a_refusal_names_the_failing_tests_and_keeps_the_whole_output(self):
        out = ("FAIL: test_b_shadow (__main__.Seam.test_b_shadow)\n"
               "ERROR: test_d_red_proof (__main__.Seam.test_d_red_proof)\n"
               "Traceback ...\nRan 132 tests\n\nFAILED (failures=1, errors=1)\n")
        r = Runner({"test_door.py": (1, out)})
        res = H.check(self.root, ["scripts/test_door.py"], r,
                      build=self.ships("test_door.py"))
        detail = res[0][2]
        self.assertIn("failing: test_b_shadow, test_d_red_proof", detail)
        kept = detail.split("[full: ")[1].rstrip("]")
        self.addCleanup(lambda: os.path.exists(kept) and os.remove(kept))
        with open(kept, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), out)

    def test_a_refusal_keeps_output_in_the_scratch_root(self):
        from unittest import mock
        r = Runner({"test_door.py": (1, "FAILED\n")})
        again = subprocess.CompletedProcess([], 0, "OK\n", "")
        with mock.patch.object(H, "_checkout_rerun", return_value=(again, None)):
            res = H.check(self.root, ["scripts/test_door.py"], r,
                          build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        kept = res[0][2].split("[full: ")[1].rstrip("]")
        self.assertEqual(os.path.dirname(kept), self.scratch)
        self.assertNotEqual(os.path.dirname(kept), tempfile.gettempdir())
        self.assertEqual(os.path.basename(kept),
                         "run-hermetic-refused-test_door.py-%d.txt" % os.getpid())
        self.assertTrue(os.path.isfile(kept), "the refused output was not kept")

    def test_a_refusal_that_cannot_save_output_still_refuses(self):
        from unittest import mock
        open(self.scratch, "w").close()  # only saving fails, the rerun succeeds
        r = Runner({"test_door.py": (1, "FAILED\n")})
        again = subprocess.CompletedProcess([], 0, "OK\n", "")
        with mock.patch.object(H, "_checkout_rerun", return_value=(again, None)):
            res = H.check(self.root, ["scripts/test_door.py"], r,
                          build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("[full: (could not save: ", res[0][2])
        self.assertNotIn("could not run", res[0][2])
        self.assertEqual(H.P.exit_code(res), H.P.EXIT_REFUSED)

    def test_a_passing_test_is_ok(self):
        r = Runner()
        res = H.check(self.root, ["scripts/test_door.py"], r,
                      build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.OK)
        self.assertEqual(len(r.calls), 1, "a pass must never pay for the checkout rerun")

    def test_it_runs_the_export_trees_copy_under_the_runners_conditions(self):
        seen = {}
        r = Runner()

        def build(dest):
            seen["tree"] = dest
            self.ships("test_door.py")(dest)
        H.check(self.root, ["scripts/test_door.py"], r, build=build)
        _, cmd, kw = r.calls[0]
        self.assertEqual(cmd[1], os.path.join(seen["tree"], "scripts/test_door.py"))
        self.assertEqual(kw["cwd"], seen["tree"])
        self.assertIn("hermetic-home-", kw["env"]["HOME"])
        self.assertFalse([k for k in kw["env"] if k.startswith("GIT_")])
        self.assertFalse(os.path.exists(seen["tree"]), "the export tree was left behind")

    def test_a_hooks_git_dir_never_reaches_the_tests_it_runs(self):
        """2026-09-20: run from the pre-push hook, the tests inherited GIT_DIR
        and wrote core.bare and a fixture identity into the real repository."""
        from unittest import mock
        r = Runner()
        with mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git", "GIT_WORK_TREE": "/real"}):
            H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        self.assertFalse([k for k in r.calls[0][2]["env"] if k.startswith("GIT_")])

    def test_a_test_the_export_does_not_ship_is_ok_and_never_run(self):
        r = Runner({"test_private.py": (1, "would fail\n")})
        res = H.check(self.root, ["scripts/test_private.py"], r, build=self.ships())
        self.assertEqual(res[0][0], H.OK)
        self.assertIn("not shipped", res[0][2])
        self.assertEqual(r.calls, [])

    def test_a_timeout_is_no_data_never_ok(self):
        r = Runner({"test_door.py": ("timeout", "")})
        res = H.check(self.root, ["scripts/test_door.py"], r,
                      build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.NODATA)

    def test_a_tree_that_cannot_be_built_is_no_data(self):
        def boom(dest):
            raise RuntimeError("allowlist unreadable")
        res = H.check(self.root, ["scripts/test_door.py"], Runner(), build=boom)
        self.assertEqual(res[0][0], H.NODATA)

    def test_one_red_among_greens_decides_the_exit(self):
        r = Runner({"test_door.py": (1, "FAILED\n")})
        res = H.check(self.root, ["scripts/test_door.py", "scripts/test_private.py"], r,
                      build=self.ships("test_door.py", "test_private.py"))
        self.assertEqual(H.P.exit_code(res), H.P.EXIT_REFUSED)


class TheRunIsConfined(Base):
    """Decision D12 (2026-10-02): a touched test imports the build's modules, and the landing pushes after every landing,
    so the pre-push run of that test used to execute a landed build's code on the open host. Both runs now carry the
    grader's sandbox and the landing suite environment. These read the argv and env the check hands its runner."""

    def test_the_export_run_is_wrapped_in_the_graders_sandbox_rooted_at_the_tree(self):
        seen = {}
        r = Runner()

        def build(dest):
            seen["tree"] = dest
            self.ships("test_door.py")(dest)
        H.check(self.root, ["scripts/test_door.py"], r, build=build)
        argv = r.boxed[0]
        self.assertEqual(argv[0], "sandbox-exec", argv)
        self.assertIn("ROOT=" + os.path.realpath(seen["tree"]), argv)
        self.assertIn("TMP=" + os.path.realpath(r.calls[0][2]["env"]["HOME"]), argv)

    def test_the_checkout_rerun_is_wrapped_the_same_way_rooted_at_its_disposable_worktree(self):
        r = Runner({"test_door.py": [(1, "FAILED\n"), (1, "FAILED\n")]})
        H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        self.assertEqual(len(r.boxed), 2)
        argv = r.boxed[1]
        self.assertEqual(argv[0], "sandbox-exec", argv)
        tree = r.calls[1][2]["cwd"]
        self.assertNotEqual(os.path.realpath(tree), os.path.realpath(self.root))
        self.assertIn("ROOT=" + os.path.realpath(tree), argv)
        self.assertNotIn("ROOT=" + os.path.realpath(self.root), argv, "the sandbox let the rerun write into the checkout")
        self.assertIn("TMP=" + os.path.realpath(r.calls[1][2]["env"]["HOME"]), argv)

    def test_the_run_gets_the_landing_suites_environment(self):
        from unittest import mock
        seen = {}

        class Looks(Runner):
            def __call__(self, cmd, **kw):
                seen["tmpdir_exists"] = os.path.isdir(kw["env"].get("TMPDIR", ""))
                return Runner.__call__(self, cmd, **kw)
        r = Looks()
        with mock.patch.dict(os.environ, {"BROTHER_TRANSPORTS": "claude", "BROTHER_RUN_DIR": "/run"}):
            H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        env = r.calls[0][2]["env"]
        self.assertEqual(env["TMPDIR"], os.path.join(env["HOME"], "tmp"))
        self.assertTrue(seen["tmpdir_exists"], "TMPDIR must exist when the test starts, or tempfile falls back to "
                                              "the host's temp folder, which the sandbox denies")
        self.assertEqual(env.get("PYTHONDONTWRITEBYTECODE"), "1")
        self.assertNotIn("BROTHER_TRANSPORTS", env)
        self.assertNotIn("BROTHER_RUN_DIR", env)

    def test_a_host_that_cannot_confine_runs_nothing_and_reads_no_data(self):
        from unittest import mock
        r = Runner()
        with mock.patch.object(H.G, "_sandbox_present", return_value=False):
            res = H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.NODATA)
        self.assertIn("sandbox", res[0][2])
        self.assertEqual(r.calls, [], "nothing may run bare")
        self.assertEqual(H.P.exit_code(res), H.P.EXIT_NODATA)

    def test_a_rerun_the_sandbox_refuses_says_unknown_and_still_refuses(self):
        from unittest import mock
        r = Runner({"test_door.py": (1, "FAILED\n")})
        # one seam per run: sandboxed() asks sandbox_ready() (which itself asks _sandbox_present and, since 2026-10-03,
        # whether a profile applies here), so the export run's answer and the rerun's are given there, not by counting
        # calls to a helper sandboxed() makes twice
        with mock.patch.object(H.G, "_sandbox_present", return_value=True), \
             mock.patch.object(H.G, "sandbox_ready", side_effect=["", "the sandbox refused this run (fixture)"]):
            res = H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        self.assertEqual(res[0][0], H.REFUSED)
        self.assertIn("could not run (the sandbox refused", res[0][2])
        self.assertNotIn("own fixture", res[0][2])
        self.assertEqual(len(r.calls), 1)

    def test_an_opt_out_narrates_on_stderr_never_in_the_verdict_stream(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from unittest import mock
        r = Runner()
        out, err = StringIO(), StringIO()
        with mock.patch.dict(os.environ, {"BROTHER_SANDBOX": "off"}), redirect_stdout(out), redirect_stderr(err):
            H.check(self.root, ["scripts/test_door.py"], r, build=self.ships("test_door.py"))
        self.assertEqual(r.boxed[0][0], sys.executable, "the opt out runs the bare command")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("SANDBOX OFF", err.getvalue())


def _sandbox_runs_here():
    """True when the grader's sandbox can start a process on this host, MEASURED by starting one: a run that is itself
    already inside the sandbox (the pre-push gate running this file, 2026-10-02) is refused a nested one by macOS
    (sandbox-exec: sandbox_apply: Operation not permitted, exit 71), and inside it every fixture below would read
    REFUSED whatever the check did. Skipping on that measurement is honest; a pass there would be false."""
    if not H.G.contained():
        return False
    home = tempfile.mkdtemp(prefix="test-hermetic-sandbox-probe-")
    try:
        r = subprocess.run(H.G.sandboxed(["/usr/bin/true"], home, home), capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, H.G.SandboxRefused):
        return False
    finally:
        shutil.rmtree(home, ignore_errors=True)


@unittest.skipUnless(_sandbox_runs_here(), "the grader's sandbox cannot start a process here (no sandbox-exec, no "
                                            "profile, or this run is already inside a sandbox, which macOS will not nest)")
class TheSandboxOnThisHost(Base):
    """The confinement itself, through check() with the default runner, which is how pre_push_gate calls it. One
    condition per test: an outside write, a network socket, an ordinary test, and the wording when the sandbox alone
    makes a test red. The fixture is the same file in the checkout (the rerun) and in the export tree (the first run)."""

    def fixture(self, body):
        name = "test_fixture.py"
        with open(os.path.join(self.root, "scripts", name), "w") as fh:
            fh.write(body)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")   # the rerun runs HEAD's copy

        def build(dest):
            os.makedirs(os.path.join(dest, "scripts"))
            shutil.copy(os.path.join(self.root, "scripts", name), os.path.join(dest, "scripts", name))
        return "scripts/" + name, build

    def test_a_red_rerun_writes_into_its_disposable_worktree_and_never_into_the_checkout(self):
        # review 14 finding 2 (2026-10-02), through check() with the real sandbox: the rerun's sandbox was rooted at the
        # checkout, so a red test that writes beside itself dirtied the landing tree and every later landing refused
        test, build = self.fixture("import unittest\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_plant(self):\n"
                                   "        open('planted.txt', 'w').write('x')\n"
                                   "        self.fail('red on purpose')\n"
                                   "unittest.main()\n")
        res = H.check(self.root, [test], build=build)
        self.assertEqual(res[0][0], H.REFUSED, res)
        self.assertIn("red in the checkout too", res[0][2], "the rerun did run, and was red there: %r" % (res,))
        self.assertFalse(os.path.exists(os.path.join(self.root, "planted.txt")), "the rerun wrote into the checkout")
        # the fixture's scratch root (the kept refusal output) sits inside the checkout; nothing else may appear
        outside_scratch = [l for l in self.git("status", "--porcelain", "-uall").splitlines() if not l.startswith("?? scratch/")]
        self.assertEqual(outside_scratch, [], "the checkout is left clean")
        listed = self.git("worktree", "list", "--porcelain")
        self.assertEqual(listed.count("worktree "), 1, "the disposable worktree was left behind: " + listed)

    def test_a_touched_test_that_writes_outside_the_tree_fails_and_the_write_never_lands(self):
        elsewhere = tempfile.mkdtemp(prefix="test-hermetic-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        leak = os.path.join(elsewhere, "leak")
        test, build = self.fixture("import unittest\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_leak(self):\n"
                                   "        open(%r, 'w').write('x')\n"
                                   "unittest.main()\n" % leak)
        res = H.check(self.root, [test], build=build)
        self.assertEqual(res[0][0], H.REFUSED, res)
        self.assertFalse(os.path.exists(leak), "the write reached the open host")

    def test_a_touched_test_that_opens_a_network_socket_fails(self):
        test, build = self.fixture("import socket, unittest\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_bind(self):\n"
                                   "        socket.socket().bind(('127.0.0.1', 0))\n"
                                   "unittest.main()\n")
        res = H.check(self.root, [test], build=build)
        self.assertEqual(res[0][0], H.REFUSED, res)

    def test_an_ordinary_touched_test_still_passes(self):
        test, build = self.fixture("import os, tempfile, unittest\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_scratch(self):\n"
                                   "        d = tempfile.mkdtemp()\n"
                                   "        open(os.path.join(d, 'f'), 'w').write('x')\n"
                                   "        self.assertTrue(os.path.isfile(os.path.join(d, 'f')))\n"
                                   "unittest.main()\n")
        res = H.check(self.root, [test], build=build)
        self.assertEqual(res[0][0], H.OK, res)
        self.assertIn("passes on the export tree", res[0][2])

    def test_a_test_red_only_because_of_the_sandbox_reads_the_same_on_both_sides(self):
        test, build = self.fixture("import socket, unittest\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_bind(self):\n"
                                   "        socket.socket().bind(('127.0.0.1', 0))\n"
                                   "unittest.main()\n")
        res = H.check(self.root, [test], build=build)
        self.assertIn("red in the checkout too", res[0][2])
        self.assertNotIn("own fixture", res[0][2])


class TheBatteriesAreDeafToAGitHook(unittest.TestCase):
    """34 of the 77 scripts tests that write through git never install
    tmp_sandbox (measured 2026-09-20), so the one place they are all launched
    from drops git's location variables, before its first check."""

    def test_both_batteries_unset_every_location_variable_before_any_check(self):
        import tmp_sandbox
        for name in ("required_fast.sh", "check_all.sh"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                text = fh.read()
            head = text[:text.index("\nrun_check \"")]
            unset = [l for l in head.splitlines() if l.startswith("unset ")]
            self.assertTrue(unset, name)
            missing = set(tmp_sandbox.GIT_LOCATION_VARS) - set(" ".join(unset).split())
            self.assertEqual(missing, set(), name)


class TheHooksGitEnvironmentNeverReachesTheBuild(unittest.TestCase):
    """git exports GIT_DIR to its hooks. The tests' own environment was already scrubbed; the step BEFORE them, building
    the export tree, was not, so from the pre-push hook the tree was built against the wrong repository shape and an honest
    test failed on it (measured 2026-09-20: test_export_public.py green standalone, red with GIT_DIR set, twice)."""

    def test_scrub_removes_every_git_variable_and_nothing_else(self):
        env = {"GIT_DIR": "/somewhere/.git", "GIT_WORK_TREE": "/somewhere", "GIT_AUTHOR_NAME": "x", "HOME": "/h", "PATH": "/bin", "GITHUB_ACTIONS": "1"}
        H.scrub_git_env(env)
        self.assertEqual(env, {"HOME": "/h", "PATH": "/bin", "GITHUB_ACTIONS": "1"})

    def test_main_scrubs_the_process_environment_before_it_builds_anything(self):
        import os
        seen = {}
        old = dict(os.environ); os.environ["GIT_DIR"] = "/not/a/repo/.git"; os.environ["GIT_INDEX_FILE"] = "/not/an/index"
        real = H.check
        H.check = lambda *a, **k: seen.update({k2: v for k2, v in os.environ.items() if k2.startswith("GIT_")}) or [(H.OK, "stub", "stub")]
        try:
            H.main(["scripts/test_hermetic_test_check.py"])
        finally:
            H.check = real; os.environ.clear(); os.environ.update(old)
        self.assertEqual(seen, {})


class ImpactSelectedTests(unittest.TestCase):
    """Each tree isolates one selection rule; no fixture uses the checkout."""

    def setUp(self):
        self.tree = tempfile.TemporaryDirectory(prefix="test-reach-")
        self.addCleanup(self.tree.cleanup)
        self.root = self.tree.name

    def put(self, path, body=""):
        dest = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def reach(self, *paths):
        return H.tests_reaching(paths, self.root)

    def test_direct_import_reached(self):
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_entry.py", "import door\n")
        self.assertEqual(self.reach(changed), [test])

    def test_subprocess_filename_reached(self):
        changed = self.put("scripts/loop/door.sh")
        test = self.put("scripts/test_entry.py", 'import subprocess\nsubprocess.run(["door.sh"])\n')
        self.assertEqual(self.reach(changed), [test])

    def test_callers_test_reached(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/loop/entry.py", "import door\n")
        test = self.put("scripts/test_contract.py", "import entry\n")
        self.assertEqual(self.reach(changed), [test])

    def test_transitive_test_import_reached(self):
        changed = self.put("scripts/door.py")
        first = self.put("scripts/test_first.py", "import door\n")
        second = self.put("scripts/loop/test_second.py", "import test_first\n")
        third = self.put("plugin/runtime/brother/core/test_third.py", "import test_second\n")
        self.assertEqual(self.reach(changed), sorted([first, second, third]))

    def test_unrelated_test_not_reached(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/other.py")
        self.put("scripts/test_other.py", "import other\n")
        self.assertEqual(self.reach(changed), [])

    def test_unresolvable_import_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        unknown = self.put("scripts/loop/test_unknown.py", "import absent_local_module\n")
        sibling = self.put("scripts/loop/test_sibling.py")
        self.put("scripts/test_outside.py")
        self.assertEqual(self.reach(changed), [unknown])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_unresolved_production_import_does_not_widen_unrelated_tests(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/other.py", "import absent_local_module\n")
        self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [])

    def test_empty_change_selects_nothing(self):
        self.put("scripts/test_unknown.py", "import absent_local_module\n")
        self.assertEqual(self.reach(), [])

    def test_imported_alias_loader_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_entry.py", 'from importlib import import_module as load\nload(chosen)\n')
        sibling = self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_unreadable_changed_path_is_no_data(self):
        from unittest import mock
        changed = self.put("scripts/door.py")
        real_open = open

        def refuse(path, *args, **kwargs):
            if os.fspath(path) == os.path.join(self.root, changed):
                raise PermissionError("fixture denied")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=refuse):
            with self.assertRaisesRegex(ValueError, "NO-DATA.*scripts/door.py"):
                self.reach(changed)

    def test_non_directory_root_is_no_data(self):
        path = self.put("root-file")
        with self.assertRaisesRegex(ValueError, "NO-DATA.*root-file"):
            H.tests_reaching([], os.path.join(self.root, path))

    def test_callers_named_test_reached(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/loop/entry.py", "import door\n")
        test = self.put("scripts/test_entry.py")
        self.assertEqual(self.reach(changed), [test])

    def test_caller_selftest_reached(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/loop/entry.py", 'import door\nimport sys\nif "--selftest" in sys.argv: pass\n')
        self.assertEqual(self.reach(changed), ["python3 scripts/loop/entry.py --selftest"])

    def test_changed_module_selftest_reached(self):
        changed = self.put("scripts/door.py", 'import sys\nif "--selftest" in sys.argv: pass\n')
        self.assertEqual(self.reach(changed), ["python3 scripts/door.py --selftest"])

    def test_shell_caller_selftest_reached(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/loop/entry.sh", 'python3 door.py\ncase "$1" in\n--selftest) exit 0 ;;\nesac\n')
        self.assertEqual(self.reach(changed), ["sh scripts/loop/entry.sh --selftest"])

    def test_referenced_selftest_flag_is_not_own_selftest(self):
        changed = self.put("scripts/door.py", 'COMMAND = ["other.py", "--selftest"]\n')
        self.assertEqual(self.reach(changed), [])

    def test_shell_reference_to_other_selftest_not_selected(self):
        changed = self.put("scripts/door.sh", "python3 other.py --selftest\n")
        self.assertEqual(self.reach(changed), [])

    def test_selftest_outside_search_not_selected(self):
        changed = self.put("bundle/door.py", 'import sys\nif "--selftest" in sys.argv: pass\n')
        self.assertEqual(self.reach(changed), [])

    def test_changed_test_outside_search_not_selected(self):
        changed = self.put("bundle/test_entry.py")
        self.assertEqual(self.reach(changed), [])

    def test_changed_test_reached(self):
        changed = self.put("scripts/test_entry.py")
        self.assertEqual(self.reach(changed), [changed])

    def test_qualified_import_reached(self):
        changed = self.put("scripts/loop/door.py")
        test = self.put("scripts/test_entry.py", "from scripts.loop import door as D\n")
        self.put("scripts/test_unrelated.py")
        self.assertEqual(self.reach(changed), [test])

    def test_dotted_import_reached(self):
        changed = self.put("plugin/runtime/brother/core/door.py")
        test = self.put("scripts/test_entry.py", "import plugin.runtime.brother.core.door as D\n")
        self.put("scripts/test_unrelated.py")
        self.assertEqual(self.reach(changed), [test])

    def test_relative_import_reached(self):
        changed = self.put("scripts/loop/door.py")
        test = self.put("scripts/loop/test_entry.py", "from . import door\n")
        self.put("scripts/loop/test_unrelated.py")
        self.assertEqual(self.reach(changed), [test])

    def test_relative_import_does_not_reach_other_package(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/loop/door.py")
        self.put("scripts/loop/test_entry.py", "from .door import value\n")
        self.assertEqual(self.reach(changed), [])

    def test_unresolvable_from_import_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_entry.py", "from absent_local_module import value\n")
        sibling = self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_unresolved_namespace_member_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/loop/member.py")
        test = self.put("scripts/test_entry.py", "from scripts.loop import absent_member\n")
        sibling = self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_literal_loader_does_not_widen_directory(self):
        changed = self.put("scripts/loop/door.py")
        test = self.put("scripts/test_entry.py", 'from importlib.util import spec_from_file_location\nspec_from_file_location("chosen", "scripts/loop/door.py")\n')
        self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])

    def test_changed_module_named_test_reached(self):
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_door.py")
        self.assertEqual(self.reach(changed), [test])

    def test_stdlib_import_does_not_widen(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/test_other.py", "import os\nfrom pathlib import Path\nfrom unittest import mock\n")
        self.assertEqual(self.reach(changed), [])

    def test_dynamic_import_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        unknown = self.put("scripts/loop/test_unknown.py", "import importlib\nimportlib.import_module(chosen)\n")
        sibling = self.put("scripts/loop/test_sibling.py")
        self.assertEqual(self.reach(changed), [unknown])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_literal_dynamic_import_reached(self):
        changed = self.put("scripts/loop/door.py")
        test = self.put("scripts/test_entry.py", 'import importlib\nimportlib.import_module("door")\n')
        self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])

    def test_unresolved_loader_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_entry.py", 'from importlib.util import spec_from_file_location\nspec_from_file_location("chosen", path)\n')
        sibling = self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_invalid_python_selects_itself_not_its_directory(self):
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_entry.py", "import (\n")
        sibling = self.put("scripts/test_other.py")
        self.assertEqual(self.reach(changed), [test])   # unknown reach runs; the resolved sibling keeps its own reach

    def test_second_production_hop_not_followed(self):
        changed = self.put("scripts/door.py")
        self.put("scripts/entry.py", "import door\n")
        self.put("scripts/other.py", "import entry\n")
        self.put("scripts/test_other.py", "import other\n")
        self.assertEqual(self.reach(changed), [])

    def test_test_filename_mentions_do_not_extend_transitive_imports(self):
        changed = self.put("scripts/door.py")
        first = self.put("scripts/test_first.py", "import door\n")
        self.put("scripts/test_other.py", 'NAME = "test_first.py"\n')
        self.assertEqual(self.reach(changed), [first])

    def test_duplicate_test_basenames_preserve_both_paths(self):
        changed = self.put("scripts/door.py")
        first = self.put("scripts/test_entry.py", "import door\n")
        second = self.put("scripts/loop/test_entry.py", "import door\n")
        self.assertEqual(self.reach(changed, changed), sorted([first, second]))

    def test_missing_changed_path_is_no_data(self):
        with self.assertRaisesRegex(ValueError, "NO-DATA.*scripts/missing.py"):
            self.reach("scripts/missing.py")

    def test_absolute_changed_path_is_no_data(self):
        path = self.put("scripts/door.py")
        with self.assertRaisesRegex(ValueError, "NO-DATA.*door.py"):
            self.reach(os.path.join(self.root, path))

    def test_parent_changed_path_is_no_data(self):
        self.put("outside.py")
        nested = os.path.join(self.root, "repo")
        os.mkdir(nested)
        with self.assertRaisesRegex(ValueError, "NO-DATA.*outside.py"):
            H.tests_reaching(["../outside.py"], nested)

    def test_reach_cli_lists_selection_then_summary(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest import mock
        changed = self.put("scripts/door.py")
        test = self.put("scripts/test_entry.py", "import door\n")
        out = StringIO()
        with mock.patch.object(H, "ROOT", self.root), redirect_stdout(out):
            code = H.main(["--reach", changed])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().splitlines(), [test, "REACH: 1 test(s) from 1 changed path(s)"])

    def test_reach_cli_bad_input_is_no_data(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest import mock
        out = StringIO()
        with mock.patch.object(H, "ROOT", self.root), redirect_stdout(out):
            code = H.main(["--reach", "scripts/missing.py"])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out.getvalue())

    def test_reach_cli_unknown_flag_is_no_data(self):
        from contextlib import redirect_stderr
        from io import StringIO
        out = StringIO()
        with redirect_stderr(out), self.assertRaises(SystemExit) as raised:
            H.main(["--reach", "--unknown"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("NO-DATA", out.getvalue())

    def test_reach_cli_without_paths_is_no_data(self):
        from contextlib import redirect_stdout
        from io import StringIO
        out = StringIO()
        with redirect_stdout(out):
            code = H.main(["--reach"])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out.getvalue())


class _R14DeadRunner(object):
    """A runner whose call returns None, the shape P._run gives when it has
    no result at all. changed_paths must turn that into a problem, never an
    AttributeError into the caller."""

    def __call__(self, *args, **kwargs):
        return None


class R14HostileInputIsRefused(unittest.TestCase):
    """R1.4: every entry point this slice owns (tests_for, changed_paths,
    scrub_git_env, check) refuses a wrong type with ValueError or a returned
    refusal, never a raw interpreter exception and never a silent accept.
    None, a str where a list is expected, an int in a list of strings, a
    non-directory root, a bool or NaN timeout and a non-string env key are
    the shapes the red team fires. An empty list is still the honest empty
    change answer; None and a bare string are not."""

    def setUp(self):
        self.tree = tempfile.TemporaryDirectory(prefix="test-r14-hostile-")
        self.addCleanup(self.tree.cleanup)
        self.root = self.tree.name
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", "test_door.py"), "w") as fh:
            fh.write('"""doc"""\n')

    def test_tests_for_refuses_none(self):
        with self.assertRaises(ValueError):
            H.tests_for(None, self.root)

    def test_tests_for_refuses_a_str_where_a_list_is_expected(self):
        with self.assertRaises(ValueError):
            H.tests_for("scripts/test_door.py", self.root)

    def test_tests_for_refuses_a_non_string_path(self):
        with self.assertRaises(ValueError):
            H.tests_for([123], self.root)

    def test_tests_for_refuses_a_non_directory_root(self):
        with self.assertRaises(ValueError):
            H.tests_for(["scripts/door.py"], os.path.join(self.root, "missing"))

    def test_tests_for_still_accepts_its_honest_input(self):
        self.assertEqual(H.tests_for(["scripts/door.py"], self.root),
                         ["scripts/test_door.py"])

    def test_changed_paths_refuses_a_str_where_a_list_is_expected(self):
        with self.assertRaises(ValueError):
            H.changed_paths(self.root, "a..b", _R14DeadRunner())

    def test_changed_paths_refuses_a_non_string_revision(self):
        with self.assertRaises(ValueError):
            H.changed_paths(self.root, [123], _R14DeadRunner())

    def test_changed_paths_a_dead_runner_is_a_problem_not_a_crash(self):
        paths, problem = H.changed_paths(self.root, ["a..b"], _R14DeadRunner())
        self.assertIsNone(paths)
        self.assertTrue(problem)

    def test_scrub_git_env_refuses_a_non_mapping(self):
        with self.assertRaises(ValueError):
            H.scrub_git_env(42)

    def test_scrub_git_env_refuses_a_string(self):
        with self.assertRaises(ValueError):
            H.scrub_git_env("GIT_DIR=/x")

    def test_scrub_git_env_refuses_a_non_string_key(self):
        with self.assertRaises(ValueError):
            H.scrub_git_env({1: "x", "GIT_DIR": "/x"})

    def test_scrub_git_env_still_scrubs_a_dict(self):
        env = {"GIT_DIR": "/x", "HOME": "/h"}
        H.scrub_git_env(env)
        self.assertEqual(env, {"HOME": "/h"})

    def test_check_refuses_none_tests(self):
        with self.assertRaises(ValueError):
            H.check(self.root, None, build=lambda dest: None)

    def test_check_refuses_a_str_where_a_list_is_expected(self):
        with self.assertRaises(ValueError):
            H.check(self.root, "scripts/test_door.py", build=lambda dest: None)

    def test_check_refuses_a_non_string_test(self):
        with self.assertRaises(ValueError):
            H.check(self.root, [123], build=lambda dest: None)

    def test_check_refuses_a_non_directory_root(self):
        with self.assertRaises(ValueError):
            H.check(os.path.join(self.root, "missing"), ["scripts/test_door.py"],
                    build=lambda dest: None)

    def test_check_refuses_a_bool_timeout(self):
        with self.assertRaises(ValueError):
            H.check(self.root, [], timeout=True)

    def test_check_refuses_a_nan_timeout(self):
        with self.assertRaises(ValueError):
            H.check(self.root, [], timeout=float("nan"))

    def test_check_still_returns_the_empty_change_answer(self):
        res = H.check(self.root, [])
        self.assertEqual([r[0] for r in res], [H.OK])

    def test_main_help_returns_zero(self):
        from contextlib import redirect_stdout
        from io import StringIO
        out = StringIO()
        with redirect_stdout(out):
            code = H.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.getvalue())


if __name__ == "__main__":
    unittest.main()
