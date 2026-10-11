#!/usr/bin/env python3
"""The plain loop launcher, scripts/loop/start_run.sh, tested at its entry point.

WHY THIS EXISTS. Until 2026-10-10 the launcher lived only in an evidence folder
that sessions prune by age, with no test anywhere; an older, different copy sat
in a handover pack. The owner ordered the loop onto the repository of record
("The loop should be part of the Github main capability and sit in a safe
place"), and a launcher that is stored but never tested is not yet safe.

ONE FIXTURE PER GUARD. Every refusal below is reached by a fixture that only
that refusal can stop: the argument checks, the five tree checks, the pins, a
hold, a live loop, the intake and the driver. The live check, the intake and
the driver are replaced through the script's own seams (START_RUN_LIVE_CHECK,
START_RUN_INTAKE, START_RUN_DRIVER), and every record it writes goes under
START_RUN_EVIDENCE in a throwaway folder, so nothing here can reach a real loop,
a real hold file or a real home folder.

OUT OF SCOPE, named, each a guard no fixture here reaches: the 120 second wait
for a driver that stays alive but never prints its first line (a two minute
test for one branch); `cd "$REPO"` failing right after git named it the top
level (a race with a deletion); the spawner itself failing to start bash; a
lock folder whose pid file cannot be written, a stale lock that cannot be
replaced, and a successful start's held folder that cannot be marked .started
(each an unwritable file inside a folder that was writable a moment before);
two launches judging one stale lock in the same instant. The
real live check, intake and driver have their own suites (test_stop_loop*,
test_loop_intake*, test_loop_until*).

Python 3.9 and 3.13, standard library only, no network.
"""
import datetime
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "start_run.sh")

STUB_INTAKE = r'''import os, sys
evd = os.environ["TEST_EVD"]
with open(os.path.join(evd, "intake-argv.txt"), "w") as fh:
    fh.write("\n".join(sys.argv[1:]))
mode = os.environ.get("TEST_INTAKE_MODE", "ready")
if mode in ("ready", "ready-no-env"):
    if mode == "ready":
        os.makedirs(os.path.join(evd, "loop-intake"), exist_ok=True)
        with open(os.path.join(evd, "loop-intake", "launch-env.sh"), "w") as fh:
            fh.write("export TEST_FROM_ENV=1\n")
    print("INTAKE READY")
elif mode == "ready-exit-1":
    print("INTAKE READY")
    sys.exit(1)
elif mode == "ready-then-not-ready":
    print("INTAKE READY")
    print("INTAKE NOT READY: the program record could not be written (stub)")
elif mode == "newer-hold":
    with open(os.path.join(evd, "LOOP-HOLD.txt"), "w") as fh:
        fh.write("newer hold by someone else\n")
    print("REFUSED budget: stub refusal after a newer hold")
elif mode == "readonly":
    os.chmod(evd, 0o555)
    print("REFUSED budget: stub refusal with the evidence folder read only")
elif mode == "sleep":
    # the intake and one child it started, both named, so a test can check neither outlives the launcher
    import subprocess
    child = subprocess.Popen(["sleep", os.environ.get("TEST_INTAKE_SLEEP", "60")])
    with open(os.path.join(evd, "intake-pids.tmp"), "w") as fh:
        fh.write("%d %d" % (os.getpid(), child.pid))
    os.rename(os.path.join(evd, "intake-pids.tmp"), os.path.join(evd, "intake-pids.txt"))
    child.wait()
    print("INTAKE READY")
else:
    print("REFUSED budget: stub refusal")
'''

STUB_DRIVER = '''#!/bin/bash
{
  echo "argv: $*"
  echo "env: ${TEST_FROM_ENV:-unset} ${BROTHER_LAUNCH_WORKTREE:-unset}"
  echo "pid: $$"
  echo "pgid: $(ps -o pgid= -p $$ | tr -d ' ')"
} > "$TEST_EVD/driver-argv.txt"
if [ "${TEST_DRIVER_MODE:-ok}" = ok ]; then
  echo "LOOP UNTIL $1 every $2 s | log $TEST_EVD/loop.log"
  sleep 2
else
  echo "driver refused: stub"
  exit 3
fi
'''

STUB_LIVE = '''#!/bin/bash
[ "$1" = "--dry" ] || { echo "stub live check called without --dry: $*"; exit 9; }
[ ! -e "$TEST_EVD/loop-starts/.launch-lock/pid" ] || cat "$TEST_EVD/loop-starts/.launch-lock/pid" > "$TEST_EVD/live-saw-lock.txt"
echo "${TEST_LIVE_SAY:-}"
exit "${TEST_LIVE_RC:-0}"
'''

DATED = "2099-01-01 09:00"


def _git(cwd, env, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _remove_tree(root):
    """Fixtures make folders read-only on purpose, so write permission comes back on every folder before removal."""
    for dirpath, dirnames, _ in os.walk(root):
        for name in dirnames:
            os.chmod(os.path.join(dirpath, name), 0o755)
    os.chmod(root, 0o755)
    shutil.rmtree(root)


def _reap(pids):
    """Cleanup for a failing run: nothing a fixture started may outlive the test. Only a pid still running the stub
    intake or its sleep is killed, so a pid reused by another process is never touched."""
    for pid in pids:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout
        if "stub_intake.py" not in cmd and not cmd.startswith("sleep "):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass  # sbe: allow-silent the process already exited, which is exactly the state this cleanup wants


def _write(path, body, executable=False):
    with open(path, "w") as fh:
        fh.write(body)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class StartRunTest(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-start-run-"))
        self.addCleanup(_remove_tree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        self.evd = os.path.join(self.tmp, "evidence")
        os.makedirs(self.home)
        os.makedirs(self.evd)
        self.bare = os.path.join(self.tmp, "upstream.git")
        self.repo = os.path.join(self.tmp, "launch")
        env = self._git_env()
        _git(self.tmp, env, "init", "-q", "--bare", self.bare)
        _git(self.tmp, env, "init", "-q", self.repo)
        _git(self.repo, env, "checkout", "-q", "-b", "main")
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            _git(self.repo, env, "config", k, v)
        _write(os.path.join(self.repo, "README"), "fixture\n")
        _git(self.repo, env, "add", "README")
        _git(self.repo, env, "commit", "-q", "-m", "fixture")
        _git(self.repo, env, "remote", "add", "hub", self.bare)
        _git(self.repo, env, "push", "-q", "-u", "hub", "main:main")
        self.intake = os.path.join(self.tmp, "stub_intake.py")
        _write(self.intake, STUB_INTAKE)
        self.driver = os.path.join(self.tmp, "stub_driver.sh")
        _write(self.driver, STUB_DRIVER)
        self.live = os.path.join(self.tmp, "stub_live.sh")
        _write(self.live, STUB_LIVE)
        self.bin = os.path.join(self.tmp, "fakebin")
        _write(self.bin, "#!/bin/sh\nexit 0\n", executable=True)

    def _git_env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=self.home, GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=os.path.join(self.home, ".gitconfig"))
        return env

    def git(self, *args):
        _git(self.repo, self._git_env(), *args)

    def _env(self, **extra):
        env = self._git_env()
        env.update(START_RUN_EVIDENCE=self.evd, START_RUN_INTAKE=self.intake,
                   START_RUN_DRIVER=self.driver, START_RUN_LIVE_CHECK=self.live, TEST_EVD=self.evd,
                   BROTHER_CLAUDE_BIN=self.bin, BROTHER_CODEX_BIN=self.bin)
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def run_launcher(self, args, cwd=None, alarm=None, **extra):
        """`alarm` (seconds) runs the launcher under a perl alarm, so a launcher that never returns dies by SIGALRM."""
        env = self._env(**extra)
        cmd = ["bash", SCRIPT] + list(args)
        if alarm:
            cmd = ["perl", "-e", "alarm %d; exec @ARGV" % alarm] + cmd
        p = subprocess.run(cmd, cwd=cwd or self.repo, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
        return p.returncode, p.stdout

    def assertRefused(self, result, step, why):
        """The step AND the reason: two guards share a step name, so the step alone cannot tell which one fired."""
        rc, out = result
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [%s]" % step, out)
        self.assertIn(why, out)
        self.assertNotIn("STARTED", out)

    def go(self, *more, **extra):
        return self.run_launcher(["--deadline", DATED, "--budget-usd", "5"] + list(more), **extra)

    def assertNothingLaunched(self):
        self.assertFalse(os.path.exists(os.path.join(self.evd, "intake-argv.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.evd, "driver-argv.txt")))

    # arguments
    def test_missing_deadline_refused(self):
        self.assertRefused(self.run_launcher(["--budget-usd", "5"]), "args", "--deadline HH:MM or")

    def test_bad_budget_refused(self):
        self.assertRefused(self.run_launcher(["--deadline", DATED, "--budget-usd", "abc"]), "args", "--budget-usd N (a positive number)")

    def test_bad_gap_refused(self):
        self.assertRefused(self.go("--gap", "1m"), "args", "--gap is a whole number")

    def test_unknown_argument_refused(self):
        self.assertRefused(self.go("--surprise"), "args", "unknown argument --surprise")

    def test_trailing_option_without_a_value_refused(self):
        """N3: an option given last with no value used to loop forever (shift 2 with one argument left)."""
        for option in ("--deadline", "--budget-usd", "--gap", "--scope", "--finisher", "--documenter", "--checker",
                       "--worker", "--adversary", "--lift-hold"):
            with self.subTest(option=option):
                self.assertRefused(self.run_launcher(["--deadline", DATED, "--budget-usd", "5", option], alarm=10),
                                   "args", "%s needs a value" % option)

    def test_named_plan_is_exported(self):
        """LP1.c: a valid --plan reaches the intake, which exports it to the driver as BROTHER_WBS."""
        rc, out = self.go("--plan", "docs/plan/BROTHER-1.1.2-WBS.json")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   plan docs/plan/BROTHER-1.1.2-WBS.json", out)
        with open(os.path.join(self.evd, "intake-argv.txt")) as fh:
            argv = fh.read().split("\n")
        self.assertEqual(argv[argv.index("--plan") + 1], "docs/plan/BROTHER-1.1.2-WBS.json")

    def test_bad_plan_refused(self):
        """LP1.c: the selector's own refusal stops the launch before anything starts."""
        for bad in ("/etc/x.json", "docs/plan/../x.json", "notplan/x.json", "docs/plan/x.txt"):
            with self.subTest(plan=bad):
                self.assertRefused(self.go("--plan", bad), "args", "--plan:")
                self.assertNothingLaunched()

    def test_plan_twice_refused(self):
        """LP1.c: a second --plan is refused, never a last one wins."""
        self.assertRefused(self.go("--plan", "docs/plan/A.json", "--plan", "docs/plan/B.json"), "args", "--plan given twice")
        self.assertNothingLaunched()

    def test_midnight_deadline_is_tomorrow(self):
        before = datetime.datetime.now()
        rc, out = self.run_launcher(["--deadline", "00:00", "--budget-usd", "5", "--dry"])
        after = datetime.datetime.now()
        self.assertEqual(rc, 0, out)
        days = {(t + datetime.timedelta(days=1)).strftime("%Y-%m-%d") for t in (before, after)}
        self.assertTrue(any("OK   deadline %s 00:00" % d in out for d in days), out)

    # 1 tree
    def test_not_a_checkout_refused(self):
        outside = os.path.join(self.tmp, "plain")
        os.makedirs(outside)
        self.assertRefused(self.run_launcher(["--deadline", DATED, "--budget-usd", "5"], cwd=outside,
                                             GIT_CEILING_DIRECTORIES=self.tmp), "tree", "not inside a git checkout")

    def test_detached_head_refused(self):
        self.git("checkout", "-q", "--detach")
        self.assertRefused(self.go(), "tree", "detached HEAD")

    def test_no_upstream_refused(self):
        self.git("branch", "--unset-upstream")
        self.assertRefused(self.go(), "tree", "has no upstream")

    def test_dirty_tree_refused(self):
        _write(os.path.join(self.repo, "README"), "dirty\n")
        self.assertRefused(self.go(), "tree", "changed path(s)")
        self.assertNothingLaunched()

    def test_unpushed_commit_refused(self):
        self.git("commit", "-q", "--allow-empty", "-m", "local only")
        self.assertRefused(self.go(), "tree", "unpushed commit(s)")
        self.assertNothingLaunched()

    def test_behind_upstream_refused(self):
        self.git("commit", "-q", "--allow-empty", "-m", "will be upstream only")
        self.git("push", "-q", "hub", "main:main")
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.assertRefused(self.go(), "tree", "behind")

    def test_unfetchable_upstream_refused(self):
        os.rename(self.bare, self.bare + ".gone")
        self.assertRefused(self.go(), "tree", "cannot fetch hub")

    # 2 pins
    def test_pin_not_executable_refused(self):
        plain = os.path.join(self.tmp, "not-exec")
        _write(plain, "x")
        self.assertRefused(self.go(BROTHER_CLAUDE_BIN=plain), "pins", "not an executable file")

    def test_pin_unresolvable_refused(self):
        # unset, and the fixture checkout carries no scripts/loop/model_router.py to resolve it from
        self.assertRefused(self.go(BROTHER_CODEX_BIN=None), "pins", "BROTHER_CODEX_BIN resolves to")

    # 3 controls
    def _hold(self):
        path = os.path.join(self.evd, "LOOP-HOLD.txt")
        _write(path, "held by a test\n")
        return path

    def test_hold_without_words_refused(self):
        hold = self._hold()
        self.assertRefused(self.go(), "controls", "a HOLD or PAUSE is in force")
        self.assertTrue(os.path.exists(hold))
        self.assertNothingLaunched()

    def test_hold_with_words_dry_keeps_hold(self):
        hold = self._hold()
        rc, out = self.go("--dry", "--lift-hold", "owner said go")
        self.assertEqual(rc, 0, out)
        self.assertIn("DRY  controls would be lifted on: owner said go", out)
        self.assertTrue(os.path.exists(hold))

    def test_hold_lifted_on_words_is_recorded(self):
        hold = self._hold()
        rc, out = self.go("--lift-hold", "owner said go")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(hold))
        starts = os.path.join(self.evd, "loop-starts")
        lifted = [n for n in os.listdir(starts) if n.startswith("controls-lifted-")]
        self.assertEqual(len(lifted), 1)
        with open(os.path.join(starts, lifted[0])) as fh:
            body = fh.read()
        self.assertIn("owner said go", body)
        self.assertIn("held by a test", body)

    def _read_only(self, path):
        os.chmod(path, 0o555)

    def test_starts_folder_not_creatable_refused(self):
        self._read_only(self.evd)
        self.assertRefused(self.go(), "controls", "cannot create")

    def test_unwritable_starts_folder_refused_at_the_lock(self):
        # round 3: a read only loop-starts now refuses at the launch lock, before the lift; the lift record guard
        # keeps its own fixture in test_a_same_second_lift_record_is_never_truncated
        hold = self._hold()
        starts = os.path.join(self.evd, "loop-starts")
        os.makedirs(starts)
        self._read_only(starts)
        self.assertRefused(self.go("--lift-hold", "owner said go"), "launch", "cannot create the launch lock")
        self.assertTrue(os.path.exists(hold))
        self.assertNothingLaunched()

    def test_hold_not_removable_refused(self):
        hold = self._hold()
        os.makedirs(os.path.join(self.evd, "loop-starts"))
        self._read_only(self.evd)
        self.assertRefused(self.go("--lift-hold", "owner said go"), "controls", "cannot remove the hold or pause")
        self.assertTrue(os.path.exists(hold))

    # N2: a refused start leaves the hold and pause exactly as it found them
    def _pause(self):
        path = os.path.join(self.evd, "LOOP-PAUSE.txt")
        _write(path, "paused by a test\nsecond line\n")
        return path

    def _controls(self):
        """{name: (bytes, mode, mtime)} for the hold and pause as they stand, absent ones left out."""
        found = {}
        for name in ("LOOP-HOLD.txt", "LOOP-PAUSE.txt"):
            path = os.path.join(self.evd, name)
            if os.path.lexists(path):
                st = os.lstat(path)
                with open(path, "rb") as fh:
                    found[name] = (fh.read(), st.st_mode, st.st_mtime)
        return found

    def test_pause_only_without_words_refused(self):
        self._pause()
        before = self._controls()
        self.assertRefused(self.go(), "controls", "a HOLD or PAUSE is in force")
        self.assertEqual(self._controls(), before)
        self.assertNothingLaunched()

    def test_pause_only_lifted_on_words(self):
        pause = self._pause()
        rc, out = self.go("--lift-hold", "owner said go")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(pause))

    def test_live_refusal_keeps_controls_as_found(self):
        self._hold()
        self._pause()
        before = self._controls()
        self.assertRefused(self.go("--lift-hold", "owner said go", TEST_LIVE_RC="1"), "live", "already running")
        self.assertEqual(self._controls(), before)
        self.assertNothingLaunched()

    def test_intake_refusal_restores_controls_as_found(self):
        self._hold()
        self._pause()
        before = self._controls()
        rc, out = self.go("--lift-hold", "owner said go", TEST_INTAKE_MODE="refuse")
        self.assertRefused((rc, out), "intake", "not READY")
        self.assertIn("OK   controls restored exactly as found", out)
        self.assertEqual(self._controls(), before)
        self.assertFalse(os.path.exists(os.path.join(self.evd, "driver-argv.txt")))

    def test_lift_hold_followed_by_an_option_refused(self):
        # 2026-10-11 review F1: `--lift-hold --dry` stored "--dry" as the owner's words and started a REAL run
        self._hold()
        before = self._controls()
        self.assertRefused(self.go("--lift-hold", "--dry"), "args", "--lift-hold needs a value")
        self.assertEqual(self._controls(), before)
        self.assertNothingLaunched()

    def test_lift_hold_with_empty_words_refused(self):
        self._hold()
        before = self._controls()
        self.assertRefused(self.go("--lift-hold", ""), "args", "--lift-hold needs a value")
        self.assertEqual(self._controls(), before)
        self.assertNothingLaunched()

    def test_newer_hold_written_during_the_window_is_kept(self):
        # review F3: the newer hold stays in force, the owner's lifted one stays held aside, and the launcher says so
        self._hold()
        rc, out = self.go("--lift-hold", "owner said go", TEST_INTAKE_MODE="newer-hold")
        self.assertRefused((rc, out), "intake", "not READY")
        self.assertIn("WARN controls: a newer hold or pause is in place", out)
        with open(os.path.join(self.evd, "LOOP-HOLD.txt")) as fh:
            self.assertEqual(fh.read(), "newer hold by someone else\n")
        starts = os.path.join(self.evd, "loop-starts")
        held = [d for d in os.listdir(starts) if d.startswith("controls-held-")]
        self.assertEqual(len(held), 1, held)
        with open(os.path.join(starts, held[0], "LOOP-HOLD.txt")) as fh:
            self.assertEqual(fh.read(), "held by a test\n")

    def test_a_failed_move_back_is_named_not_called_newer(self):
        # review F4: an unwritable folder with nothing in the way is a failed move, not a newer hold
        self._hold()
        rc, out = self.go("--lift-hold", "owner said go", TEST_INTAKE_MODE="readonly")
        self.assertRefused((rc, out), "intake", "not READY")
        self.assertIn("could not move back", out)
        self.assertNotIn("a newer hold or pause is in place", out)

    def _launch_during_a_sleeping_intake(self, **extra):
        """Start a lifting launch whose intake (and one child of it) sleeps; return the launcher and both pids."""
        pidfile = os.path.join(self.evd, "intake-pids.txt")
        if os.path.exists(pidfile):
            os.remove(pidfile)
        proc =subprocess.Popen(["bash", SCRIPT, "--deadline", DATED, "--budget-usd", "5", "--lift-hold", "owner said go"],
                                cwd=self.repo, env=self._env(TEST_INTAKE_MODE="sleep", **extra), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True, start_new_session=True)
        for _ in range(400):
            if os.path.exists(pidfile) or proc.poll() is not None:
                break
            time.sleep(0.05)
        if not os.path.exists(pidfile):
            proc.kill()
            self.fail("the intake never started: %s" % proc.communicate()[0])
        with open(pidfile) as fh:
            pids = [int(p) for p in fh.read().split()]
        self.addCleanup(_reap, pids)
        self.addCleanup(lambda: proc.poll() is not None or proc.kill())
        return proc, pids

    def assertAllGone(self, pids):
        """Every straggler is killed before the assertion, so a failing run leaves nothing behind."""
        alive = list(pids)
        for _ in range(100):
            still = []
            for pid in alive:
                try:
                    os.kill(pid, 0)
                    still.append(pid)
                except OSError:
                    pass  # sbe: allow-silent the process already exited, which is exactly the state this cleanup wants
            alive = still
            if not alive:
                break
            time.sleep(0.05)
        _reap(alive)
        self.assertEqual(alive, [],"intake processes still running after the launcher stopped")

    def test_every_signal_during_the_intake_restores_controls_and_stops_its_group(self):
        # review F2, round 3 B1, N1, N2: each signal after the lift must put the hold and pause back byte for byte
        # and stop the intake AND what it started; INT is sent to the launcher's whole group, as a Ctrl-C would be
        self._hold()
        self._pause()
        before = self._controls()
        for name, deliver in (("TERM", lambda p: p.send_signal(signal.SIGTERM)),
                              ("INT to the group", lambda p: os.killpg(p.pid, signal.SIGINT)),
                              ("HUP", lambda p: p.send_signal(signal.SIGHUP)),
                              ("ALRM", lambda p: p.send_signal(signal.SIGALRM)),
                              ("QUIT", lambda p: p.send_signal(signal.SIGQUIT))):
            with self.subTest(signal=name):
                proc, pids = self._launch_during_a_sleeping_intake()
                self.assertEqual(self._controls(), {}, "the lift did not happen before the intake")
                deliver(proc)
                out, _ = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 2, out)
                self.assertIn("START REFUSED [signal]", out)
                self.assertEqual(self._controls(), before)
                self.assertAllGone(pids)
                self.assertFalse(os.path.exists(os.path.join(self.evd, "driver-argv.txt")))

    def test_a_closed_stdout_does_not_kill_the_launcher_after_the_lift(self):
        # round 3 B1: `start_run.sh ... | head -6` used to die by SIGPIPE (rc 141) on the first write after the lift,
        # leaving both controls out of force; now it goes on silently to the refusal, which restores them
        self._hold()
        self._pause()
        before = self._controls()
        proc, pids = self._launch_during_a_sleeping_intake(TEST_INTAKE_SLEEP="2")
        proc.stdout.close()
        rc = proc.wait(timeout=30)
        self.assertEqual(rc, 2)
        self.assertEqual(self._controls(), before)
        self.assertAllGone(pids)
        starts = os.path.join(self.evd, "loop-starts")
        lifted = [n for n in os.listdir(starts) if n.startswith("controls-lifted-")]
        with open(os.path.join(starts, lifted[0])) as fh:
            self.assertIn("restored after a refused start", fh.read())

    def test_a_same_second_lift_record_is_never_truncated(self):
        # review F5: a lift record already carrying this launch's stamp is refused, never truncated. The stamp holds
        # the launcher's pid since round 3, so a perl gate execs the launcher under a pid known before it runs.
        self._hold()
        starts = os.path.join(self.evd, "loop-starts")
        os.makedirs(starts)
        proc = subprocess.Popen(["perl", "-e", "<STDIN>; exec @ARGV or die", "bash", SCRIPT, "--deadline", DATED,
                                 "--budget-usd", "5", "--lift-hold", "owner said go"],
                                cwd=self.repo, env=self._env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        now = datetime.datetime.now()
        records = []
        for second in range(0, 10):
            stamp = (now + datetime.timedelta(seconds=second)).strftime("%Y%m%d-%H%M%S")
            path = os.path.join(starts, "controls-lifted-%s-%d.txt" % (stamp, proc.pid))
            _write(path, "the other launch's record\n")
            records.append(path)
        out, _ = proc.communicate("go\n", timeout=60)
        self.assertRefused((proc.returncode, out), "controls", "cannot record the lift")
        for path in records:
            with open(path) as fh:
                self.assertEqual(fh.read(), "the other launch's record\n", path)
        self.assertTrue(os.path.exists(os.path.join(self.evd, "LOOP-HOLD.txt")))

    def test_driver_refusal_restores_controls_as_found(self):
        self._hold()
        before = self._controls()
        self.assertRefused(self.go("--lift-hold", "owner said go", TEST_DRIVER_MODE="fail"), "driver",
                           "the driver exited before it started")
        self.assertEqual(self._controls(), before)

    def test_lift_hold_with_whitespace_words_refused(self):
        # round 3 N3: words that are empty once whitespace is removed are no words (FX-06.md says they are refused)
        self._hold()
        before = self._controls()
        for words in ("   ", "\t", " \n "):
            with self.subTest(words=repr(words)):
                self.assertRefused(self.go("--lift-hold", words), "args", "--lift-hold needs a value")
                self.assertEqual(self._controls(), before)
        self.assertNothingLaunched()

    # round 3 B1: a held folder left by a launch that never started refuses; a marked successful one does not
    def _held_folder(self, started):
        d = os.path.join(self.evd, "loop-starts", "controls-held-20990101-000000-1")
        os.makedirs(d)
        _write(os.path.join(d, "LOOP-HOLD.txt"), "held by an earlier launch\n")
        if started:
            _write(os.path.join(d, ".started"), "")
        return d

    def test_leftover_held_controls_refused(self):
        d = self._held_folder(started=False)
        self.assertRefused(self.go(), "controls", "%s still holds a hold or pause" % d)
        rc, out = self.go("--dry")
        self.assertIn("those controls are NOT in force; move them back to %s by hand" % self.evd, out)
        self.assertTrue(os.path.exists(os.path.join(d, "LOOP-HOLD.txt")))
        self.assertNothingLaunched()

    def test_marked_successful_held_folder_is_skipped(self):
        self._held_folder(started=True)
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        self.assertIn("STARTED", out)

    def test_a_successful_start_marks_its_held_folder(self):
        self._hold()
        rc, out = self.go("--lift-hold", "owner said go")
        self.assertEqual(rc, 0, out)
        starts = os.path.join(self.evd, "loop-starts")
        held = [d for d in os.listdir(starts) if d.startswith("controls-held-")]
        self.assertEqual(len(held), 1, held)
        self.assertTrue(os.path.exists(os.path.join(starts, held[0], ".started")))
        self.assertTrue(os.path.exists(os.path.join(starts, held[0], "LOOP-HOLD.txt")))
        rc, out = self.go()
        self.assertEqual(rc, 0, out)

    # round 3 B2: one launch at a time, through a lock that names its pid
    def _lock(self, pid):
        d = os.path.join(self.evd, "loop-starts", ".launch-lock")
        os.makedirs(d)
        _write(os.path.join(d, "pid"), "%s\n" % pid)
        return d

    def test_lock_held_through_the_live_check_and_released_on_every_exit(self):
        lock = os.path.join(self.evd, "loop-starts", ".launch-lock")
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.evd, "live-saw-lock.txt")), "no lock during the live check")
        self.assertFalse(os.path.exists(lock), "the lock outlived a successful start")
        self.assertRefused(self.go(TEST_INTAKE_MODE="refuse"), "intake", "not READY")
        self.assertFalse(os.path.exists(lock), "the lock outlived a refusal")
        rc, out = self.go("--dry")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(lock), "the lock outlived a dry run")

    def test_stale_lock_is_removed_with_a_warning(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        lock = self._lock(dead.pid)
        rc, out = self.go("--dry")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN launch: removing a stale launch lock, pid %d is not alive" % dead.pid, out)
        self.assertFalse(os.path.exists(lock))

    def test_live_lock_refuses_and_is_left_in_place(self):
        lock = self._lock(os.getpid())
        self.assertRefused(self.go(), "launch", "another launch is in progress (pid %d" % os.getpid())
        with open(os.path.join(lock, "pid")) as fh:
            self.assertEqual(fh.read().strip(), str(os.getpid()))
        self.assertNothingLaunched()

    def test_lock_naming_no_pid_refused(self):
        lock = self._lock("not-a-pid")
        self.assertRefused(self.go(), "launch", "names no pid ('not-a-pid')")
        self.assertTrue(os.path.exists(lock))
        self.assertNothingLaunched()

    def test_two_launches_in_one_second_never_share_a_log(self):
        # round 3 B2: with one stamp, a refused driver read the other launch's LOOP UNTIL and printed STARTED, rc 0.
        # A fake date gives both launches the same second, deterministically.
        fake = os.path.join(self.tmp, "fakedate")
        os.makedirs(fake)
        _write(os.path.join(fake, "date"),
               '#!/bin/bash\n[ "$1" != "+%Y%m%d-%H%M%S" ] || { echo 20990101-000000; exit 0; }\nexec /bin/date "$@"\n',
               executable=True)
        path = fake + os.pathsep + os.environ.get("PATH", "")
        rc, out = self.go(PATH=path)
        self.assertEqual(rc, 0, out)
        self.assertIn("STARTED", out)
        self.assertRefused(self.go(PATH=path, TEST_DRIVER_MODE="fail"), "driver", "the driver exited before it started")
        starts = os.path.join(self.evd, "loop-starts")
        for kind in ("intake", "driver"):
            logs = [n for n in os.listdir(starts) if n.startswith("%s-20990101-000000-" % kind)]
            self.assertEqual(len(logs), 2, (kind, logs))

    # 3b live
    def test_live_loop_refused(self):
        self.assertRefused(self.go(TEST_LIVE_RC="1", TEST_LIVE_SAY="driver 4242 alive"), "live", "already running")
        self.assertNothingLaunched()

    def test_live_loop_refused_even_dry(self):
        self.assertRefused(self.go("--dry", TEST_LIVE_RC="1"), "live", "already running")

    def test_unreadable_live_check_refused(self):
        self.assertRefused(self.go(TEST_LIVE_RC="2", TEST_LIVE_SAY="NO-DATA: ps unreadable"), "live",
                           "could not answer (exit 2)")
        self.assertNothingLaunched()

    def test_missing_live_check_refused(self):
        self.assertRefused(self.go(START_RUN_LIVE_CHECK=os.path.join(self.tmp, "absent.sh")), "live", "could not answer")

    def test_dry_run_starts_nothing(self):
        rc, out = self.go("--dry")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   live   no loop is running", out)
        self.assertIn("DRY  would prepare the intake", out)
        self.assertNothingLaunched()

    # 4 intake
    def test_missing_intake_refused(self):
        self.assertRefused(self.go(START_RUN_INTAKE=os.path.join(self.tmp, "absent.py")), "intake", "absent.py in this checkout")

    def test_intake_not_ready_refused(self):
        self.assertRefused(self.go(TEST_INTAKE_MODE="refuse"), "intake", "not READY")
        self.assertFalse(os.path.exists(os.path.join(self.evd, "driver-argv.txt")))

    def test_intake_exit_one_after_ready_refused(self):
        """N4, the exit code guard: READY printed, then exit 1."""
        self.assertRefused(self.go(TEST_INTAKE_MODE="ready-exit-1"), "intake", "not READY (exit 1")
        self.assertFalse(os.path.exists(os.path.join(self.evd, "driver-argv.txt")))

    def test_intake_ready_overturned_by_a_later_verdict_refused(self):
        """N4, the last verdict guard: READY printed, then INTAKE NOT READY, exit 0."""
        self.assertRefused(self.go(TEST_INTAKE_MODE="ready-then-not-ready"), "intake",
                           "last verdict: INTAKE NOT READY")
        self.assertFalse(os.path.exists(os.path.join(self.evd, "driver-argv.txt")))

    # 5 driver
    def test_intake_without_launch_env_refused(self):
        self.assertRefused(self.go(TEST_INTAKE_MODE="ready-no-env"), "driver", "the intake did not write its launch settings")

    def test_driver_exiting_early_refused(self):
        self.assertRefused(self.go(TEST_DRIVER_MODE="fail"), "driver", "the driver exited before it started")

    # the whole path
    def test_start_passes_scope_budget_env_and_detaches(self):
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   driver pid", out)
        self.assertIn("STARTED: watch with: tail -f %s/loop.log" % self.evd, out)
        with open(os.path.join(self.evd, "intake-argv.txt")) as fh:
            argv = fh.read().split("\n")
        self.assertEqual(argv[0], "prepare")
        self.assertEqual(argv[argv.index("--scope") + 1], ".")
        self.assertEqual(argv[argv.index("--deadline") + 1], DATED)
        self.assertEqual(argv[argv.index("--budget-usd") + 1], "5")
        with open(os.path.join(self.evd, "driver-argv.txt")) as fh:
            seen = dict(line.split(": ", 1) for line in fh.read().splitlines())
        self.assertEqual(seen["argv"], "%s 240" % DATED)
        self.assertEqual(seen["env"], "1 %s" % self.repo)
        # detached: the driver leads its own process group, so the launcher's exit cannot take it down
        self.assertEqual(seen["pgid"], seen["pid"])
        self.assertNotEqual(seen["pgid"], str(os.getpgid(0)))

    def test_scope_is_passed_through(self):
        rc, out = self.go("--scope", "^U2$")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.evd, "intake-argv.txt")) as fh:
            argv = fh.read().split("\n")
        self.assertEqual(argv[argv.index("--scope") + 1], "^U2$")

    def test_no_plan_named_passes_no_plan(self):
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.evd, "intake-argv.txt")) as fh:
            self.assertNotIn("--plan", fh.read().split("\n"))

    def test_hhmm_deadline_is_dated(self):
        before = datetime.datetime.now()
        rc, out = self.run_launcher(["--deadline", "23:59", "--budget-usd", "5", "--dry"])
        after = datetime.datetime.now()
        self.assertEqual(rc, 0, out)
        days = {(t if t.strftime("%H:%M") < "23:59" else t + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                for t in (before, after)}
        self.assertTrue(any("OK   deadline %s 23:59" % d in out for d in days), out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
