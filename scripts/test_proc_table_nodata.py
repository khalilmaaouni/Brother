"""A process table the loop cannot fully read is NO-DATA everywhere, and the loop owns a process by its PROGRAM only.

WHAT IT PROTECTS (RR lane C, 2026-09-27, reproduced by an adversarial audit on hub main e7e17784c):
  - bad_ps: loop_procs.snapshot skipped a malformed row among valid ones, so stop_loop.sh printed "STOPPED: nothing of
    the loop is alive" while the runner behind that row lived. A wholly unreadable table was already NO-DATA; a
    partially unreadable one now is too.
  - ownership: loop_procs selected a loop tool by a regex over the WHOLE command line, so an unrelated viewer whose
    ARGUMENT named bin/unit_runner.py was killed by a stop. The tool must sit at the program position: the executable
    itself, or the first non option argument of an interpreter.
  - diagnostic: diag_apply restarted runners although its ps read failed, so it could start a second runner beside a
    live one it could not see.
Entry points: stop_loop.sh and diag_apply.py as subprocesses over a fake `ps` (a fixed table naming only this suite's
own processes) and a scratch HOME. Run from the repository root: python3 -B scripts/test_proc_table_nodata.py
"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
STOP = os.path.join(LOOP, "stop_loop.sh")
sys.path.insert(0, LOOP)
import loop_procs as LP  # noqa: E402

SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
# A loop tool is the loop's only when it belongs to a run of this loop: its program in $HOME/.claude/bin, or its environment
# naming a run under the runs root (scripts/test_stop_run_identity.py). Every fixture runner here lives in the fixture home's
# bin; the fixture home is the placeholder "you": scripts/test_export_public.py refuses any other home-anchored .claude path.
HOME = "/Users/you"
RUN_VARS = ("BROTHER_RUN_DIR", "BROTHER_RUNS_ROOT", "STOP_LOOP_ONLY")   # the driver exports the first two to all it starts


def fixture_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
    env["HOME"] = home
    env.update(extra)
    return env


def scratch(test, prefix):
    os.makedirs(SCRATCH, exist_ok=True)
    d = tempfile.mkdtemp(prefix=prefix, dir=SCRATCH)
    test.addCleanup(shutil.rmtree, d, True)
    return d


def sleeper(test, argv):
    """A live process of this suite's own, in its own session, killed at cleanup whatever the test did."""
    p = subprocess.Popen(argv, start_new_session=True)

    def reap():
        if p.poll() is None:
            try: os.killpg(p.pid, signal.SIGKILL)
            except OSError: pass
        p.wait()
    test.addCleanup(reap)
    return p


def fake_ps(bindir, lines, table):
    """ps answering stop_loop.sh's three shapes: the snapshot prints `lines` verbatim; `-o pgid= -p P` and
    `-o command= -p P` answer from `table` (pid -> command) for a pid that is still alive, else exit 1 silently. Alive
    is asked of the real /bin/ps: a child this suite killed but has not reaped yet is a zombie, and a zombie is gone."""
    p = os.path.join(bindir, "ps")
    with open(p, "w") as f:
        f.write("#!%s\nimport subprocess, sys\nL=%r\nT=%r\n" % (sys.executable, lines, {str(k): v for k, v in table.items()})
                + "if '-p' not in sys.argv:\n    sys.stdout.write(''.join(l + '\\n' for l in L)); sys.exit(0)\n"
                + "pid = sys.argv[sys.argv.index('-p') + 1]\n"
                + "st = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', pid], capture_output=True, text=True).stdout.strip()\n"
                + "if not st or st.startswith('Z'): sys.exit(1)\n"
                + "if pid not in T: sys.exit(1)\n"
                + "print(pid if 'pgid=' in sys.argv else T[pid])\n")
    os.chmod(p, 0o755)


def stop(d, bindir):
    env = fixture_env(os.path.join(d, "home"), STOP_LOOP_ONLY=d, PATH=bindir + os.pathsep + os.environ["PATH"])
    os.makedirs(os.path.join(d, "home", ".claude", "evidence"), exist_ok=True)
    return subprocess.run(["bash", STOP, "--runners-only"], env=env, capture_output=True, text=True, timeout=90)


def runner_script(d, body="import time\ntime.sleep(60)\n"):
    """a unit runner in the fixture home's own bin, the one place a stop with HOME=<d>/home owns a runner by"""
    script = os.path.join(d, "home", ".claude", "bin", "unit_runner.py")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w") as f: f.write(body)
    return script


class TheLiveRunnerCountIsTheStopsRule(unittest.TestCase):
    """X2 finding 7: loop_done.py and loop_pass.sh count live unit runners through loop_procs' `unit-runners` verb, by
    the program position a stop signals by. Driven at the verb over a fake `ps`."""

    def verb(self, lines):
        d = scratch(self, "unit-runners-")
        fake_ps(d, lines, {})
        env = fixture_env(HOME, PATH=d + os.pathsep + os.environ["PATH"])
        return subprocess.run([sys.executable, "-B", os.path.join(LOOP, "loop_procs.py"), "unit-runners"], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_it_counts_only_a_runner_a_stop_owns(self):
        r = self.verb(["1 0 1 /sbin/launchd",
                       "4242 1 4242 /usr/bin/python3 /Users/you/.claude/bin/unit_runner.py U1 U1.1",
                       "4243 1 4243 python3 /v/viewer.py /Users/you/.claude/bin/unit_runner.py U1 U1.1",
                       "4244 1 4244 python3 /x/scripts/loop/unit_runner.py U1 U1.1"])
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"), r.stderr)

    def test_an_unreadable_table_prints_no_count_and_exits_2(self):
        r = self.verb(["1 0 1 /sbin/launchd", "not a row"])
        self.assertEqual((r.returncode, r.stdout), (2, ""), r.stderr)
        self.assertIn("NO-DATA", r.stderr)


class APartlyUnreadableTableIsNoData(unittest.TestCase):
    def test_snapshot_with_one_malformed_row_among_valid_rows_is_none(self):
        table = "17 1 17 /usr/bin/harmless\n4242 unreadable 4242 python3 /x/bin/unit_runner.py X X.a\n"
        with mock.patch.object(LP.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, table, "")):
            self.assertIsNone(LP.snapshot(), "a table with an unreadable row read as a partial truth")

    def test_the_stop_never_says_stopped_over_a_row_it_could_not_read(self):
        d = scratch(self, "nodata-badps-")
        bindir = os.path.join(d, "fake"); os.makedirs(bindir)
        script = runner_script(d)
        runner = sleeper(self, [sys.executable, "-B", script])
        fake_ps(bindir, ["17 1 17 /usr/bin/harmless %s" % d, "%d unreadable %d python3 %s" % (runner.pid, runner.pid, script)],
                {runner.pid: "python3 %s" % script})
        r = stop(d, bindir)
        self.assertIsNone(runner.poll(), "nothing may be signalled on a table the stop could not read")
        self.assertNotIn("STOPPED", r.stdout, "the stop claimed STOPPED while a runner lived behind an unreadable row: " + r.stdout)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stdout)


class TheLoopOwnsAProcessByItsProgramOnly(unittest.TestCase):
    """...and only a program of this loop's: in the fixture home's bin, or carrying a run of this loop (a code root
    helper such as scripts/probe_round.py, which the driver starts with BROTHER_RUN_DIR exported)."""
    RUNNER = HOME + "/.claude/bin/unit_runner.py"
    RUN = HOME + "/.claude/evidence/loop-runs/run-t"

    def owned(self, *commands):
        """commands: strings, or (command, run_dir) for a row snapshot() would have read a run marker for"""
        snap = [dict(pid=100 + i, ppid=1, pgid=100 + i, command=c if isinstance(c, str) else c[0],
                     **({} if isinstance(c, str) else {"run_dir": c[1]})) for i, c in enumerate(commands)]
        with mock.patch.dict(os.environ, fixture_env(HOME), clear=True):
            return {snap[p - 100]["command"] for p in LP.owned(snap, LP.RUNNERS_RX)}

    def test_an_argument_naming_a_loop_tool_is_not_the_tool(self):
        viewer = "/usr/bin/python3 -B /tmp/viewer.py " + self.RUNNER
        pager = "/usr/bin/less /Users/you/.claude/bin/grade_lane.sh"
        editor = "vim -R /Users/you/.claude/bin/probe_wave.py"
        self.assertEqual(self.owned(viewer, pager, editor), set(), "a process whose argument names a loop tool was selected")

    def test_the_tool_at_the_program_position_is_still_owned(self):
        cmds = ["python3 %s X X.a" % self.RUNNER,
                "/usr/bin/python3 -B %s X X.a" % self.RUNNER,
                "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python %s X X.a" % self.RUNNER,
                "%s X X.a" % self.RUNNER,
                "/bin/bash /Users/you/.claude/bin/grade_lane.sh /r/round0 X.a",
                ("python3 -W ignore scripts/probe_round.py", self.RUN),   # a code root helper, owned by the run it carries
                "python3 -X utf8 /Users/you/.claude/bin/repair_wave.py a b c"]
        self.assertEqual(self.owned(*cmds), {c if isinstance(c, str) else c[0] for c in cmds})

    def test_an_empty_command_names_no_program_and_never_raises(self):
        self.assertEqual(LP.programs(""), [])
        self.assertEqual(LP.programs("   "), [])

    def test_a_module_or_inline_program_is_not_a_script(self):
        # the inline code's first word holds the tool path, so only the -c rule can refuse it
        inline = "python3 -c exec(open('%s').read())" % self.RUNNER
        self.assertEqual(self.owned("python3 -m http.server " + self.RUNNER, inline), set())

    def test_the_driver_is_owned_by_its_program_only(self):
        snap = [dict(pid=1, ppid=0, pgid=1, command="bash /Users/you/.claude/bin/loop_until.sh 07:00"),
                dict(pid=2, ppid=0, pgid=2, command="tail -f /Users/you/.claude/bin/loop_until.sh")]
        with mock.patch.dict(os.environ, fixture_env(HOME), clear=True):
            self.assertEqual(LP.owned(snap, LP.DRIVER_RX, generic=False), {1})

    def test_a_stop_leaves_the_viewer_alive_and_stops_the_runner(self):
        d = scratch(self, "nodata-owner-")
        bindir = os.path.join(d, "fake"); os.makedirs(bindir)
        body = "import time\ntime.sleep(60)\n"
        viewer_py, script = os.path.join(d, "viewer.py"), runner_script(d, body)
        with open(viewer_py, "w") as f: f.write(body)
        viewer = sleeper(self, [sys.executable, "-B", viewer_py, script])
        runner = sleeper(self, [sys.executable, "-B", script, "X", "X.a"])
        vcmd, rcmd = "python3 -B %s %s" % (viewer_py, script), "python3 -B %s X X.a" % script
        fake_ps(bindir, ["%d 1 %d %s" % (viewer.pid, viewer.pid, vcmd), "%d 1 %d %s" % (runner.pid, runner.pid, rcmd)],
                {viewer.pid: vcmd, runner.pid: rcmd})
        r = stop(d, bindir)
        time.sleep(0.3)
        self.assertIsNone(viewer.poll(), "an unrelated viewer whose argument names a loop tool was killed: " + r.stdout)
        self.assertIsNotNone(runner.poll(), "the loop's own runner survived the stop: " + r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DiagApplyStartsNothingItCannotSee(unittest.TestCase):
    """diag_apply.py <diag dir> as a subprocess. Since plan E step 2b a proven fact never starts a runner, whatever the
    process table says: it becomes the unit's note for the next brief. The runner here is a stub that records its own
    start, so a start would be observed; the note file is read back, so a silent run that wrote nothing is caught."""

    def setUp(self):
        self.d = scratch(self, "nodata-diag-")
        self.home, self.repo, self.fake = (os.path.join(self.d, n) for n in ("home", "repo", "fake"))
        os.makedirs(os.path.join(self.home, ".claude", "evidence", "unit-runs")); os.makedirs(os.path.join(self.home, ".claude", "bin"))
        os.makedirs(os.path.join(self.repo, "docs", "plan")); os.makedirs(os.path.join(self.repo, "diag", "out")); os.makedirs(self.fake)
        self.started = os.path.join(self.d, "started.txt")
        with open(os.path.join(self.home, ".claude", "bin", "unit_runner.py"), "w") as f:
            f.write("import sys\nopen(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n" % self.started)
        with open(os.path.join(self.repo, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
            json.dump({"units": [{"id": "X", "state": "OPEN", "sub_units": ["X.a"], "evidence": "", "spec": "docs/plan/specs/X.md"}]}, f)
        # A real unit always has a readable spec with one accepted done check; since 2026-09-29 the pool refuses one that
        # does not (runner_pool.admissible), which skipped this unit before the process table was ever read.
        os.makedirs(os.path.join(self.repo, "docs", "plan", "specs"))
        with open(os.path.join(self.repo, "docs", "plan", "specs", "X.md"), "w") as f:
            f.write("# X\n## X.a one\nDone check: `python3 -B scripts/test_x.py`\n")
        with open(os.path.join(self.repo, "sample"), "w") as f: f.write("token\n")
        with open(os.path.join(self.repo, "diag", "out", "X.a.json"), "w") as f:
            json.dump({"fact": "token exists", "prove": "grep token sample", "expect": "token", "hint": "fact"}, f)
        os.symlink(shutil.which("grep") or "/usr/bin/grep", os.path.join(self.fake, "grep"))

    def apply(self, ps_body, path=None):
        if ps_body is not None:
            with open(os.path.join(self.fake, "ps"), "w") as f: f.write("#!/bin/sh\n" + ps_body)
            os.chmod(os.path.join(self.fake, "ps"), 0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_") and k != "RUNNER_HINT"}
        env.update(HOME=self.home, PATH=path or (self.fake + os.pathsep + os.environ["PATH"]), PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "diag_apply.py"), os.path.join(self.repo, "diag")],
                           cwd=self.repo, env=env, capture_output=True, text=True, timeout=60)
        until = time.time() + 2.0
        while time.time() < until and not os.path.exists(self.started):
            time.sleep(0.05)
        return r, (open(self.started).read() if os.path.exists(self.started) else "")

    def note(self):
        p = os.path.join(self.home, ".claude", "evidence", "diag-notes", "X.txt")
        return open(p).read() if os.path.exists(p) else ""

    def test_control_a_readable_table_without_the_runner_still_starts_nothing_and_writes_the_note(self):
        r, started = self.apply("echo '/sbin/launchd'\nexit 0\n")
        self.assertEqual(started, "", "a proven fact started a runner: " + r.stdout + r.stderr)
        self.assertIn("X.a: VERIFIED FACT: token exists fact", self.note(), r.stdout + r.stderr)

    def refused(self, r, started, why):
        self.assertEqual(started, "", "a runner was started on %s: %s" % (why, r.stdout + r.stderr))
        self.assertIn("X.a", r.stdout)
        self.assertIn("note written for the next brief", r.stdout, r.stdout + r.stderr)
        self.assertIn("X.a: VERIFIED FACT: token exists fact", self.note())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_ps_that_exits_nonzero_restarts_nothing(self):
        # it printed a table before failing, so only the exit code can refuse it
        self.refused(*self.apply("echo 'COMMAND'\necho '/sbin/launchd'\nexit 1\n"), why="a failed process table read")

    def test_a_ps_that_prints_nothing_restarts_nothing(self):
        self.refused(*self.apply("exit 0\n"), why="an empty process table")

    def test_no_ps_at_all_restarts_nothing(self):
        self.refused(*self.apply(None, path=self.fake), why="no process table at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
