#!/usr/bin/env python3
"""LR1.c: the run arms a watcher that announces an unexplained HELD once.

The watcher is scripts/loop/run_watch.py. It reads the driver's own log and heartbeat (never a second
heartbeat rule), writes ONE alarm record into the run folder per cause, announces that cause once, and
exits when the driver is gone. scripts/loop/start_run.sh arms it detached beside the driver and refuses
the launch ([watch]) when it cannot start.

ONE FIXTURE PER GUARD. Each case below is reached by a fixture only that guard can stop: the alarm
heartbeat has no HELD line in the log, the repeated HELD has no heartbeat, the exit case has a dead pid
and nothing else. The shell half is tested through the launcher's own seams (START_RUN_INTAKE,
START_RUN_DRIVER, START_RUN_LIVE_CHECK, START_RUN_CANARY, START_RUN_WATCH) in a throwaway folder, so
nothing here reaches a real loop, a real heartbeat or the real home folder.

Python 3.9 and 3.13, standard library only, no network.
"""
import datetime
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_watch as rw  # noqa: E402

LAUNCHER = os.path.join(HERE, "start_run.sh")
WATCH = os.path.join(HERE, "run_watch.py")
CLOCK = ":".join(("00", "00", "00"))       # built, never written out: a fixture clock is not a measured time
RUN_START = "RUN START 2099-01-01T" + CLOCK
DEADLINE = "2099-01-01 09" + ":" + "00"


def now_stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write(path, text, executable=False):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def dead_pid():
    """A pid that existed a moment ago and is reaped now."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


HELD_PP1 = ("HELD    PP1.c      held on the unchanged landing tree d154b634952f (HELD at the deploy step, "
            "nothing pushed: judge refused: the whole gate exited 1 boxed: readiness-gate FAIL exit 1); "
            "offered again when the tree changes\n")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-run-watch-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir)
        self.log = os.path.join(self.tmp, "driver.log")
        self.hb = os.path.join(self.tmp, "hb.json")

    def alive_for(self, passes, between=None):
        """A stand-in for _pid_alive: True for `passes` calls, then False. `between(i)` runs before call i."""
        calls = {"n": 0}

        def alive(_pid):
            i = calls["n"]
            calls["n"] += 1
            if between is not None:
                between(i)
            return i < passes
        saved = rw._pid_alive
        rw._pid_alive = alive
        self.addCleanup(setattr, rw, "_pid_alive", saved)
        return calls

    def records(self):
        return sorted(n for n in os.listdir(self.run_dir) if n.startswith("watch-alarm"))

    def go(self, interval=0.001, pid=4242):
        said = []
        rc = rw.watch(pid, self.log, self.hb, self.run_dir, interval=interval, notify=lambda t, m: said.append((t, m)))
        return rc, said


class Launch(object):
    """start_run.sh in a throwaway repository, through its seams: step 5c arms the watcher beside the driver, or refuses [watch]."""

    DRIVER = ('#!/bin/bash\necho $$ > "$TEST_EVD/driver.pid"\necho "LOOP UNTIL $1 every $2 s | log $TEST_EVD/loop.log"\n'
              'echo "%s" >> "$TEST_EVD/loop.log"\necho "N12   N12.a    START" >> "$TEST_EVD/loop.log"\nsleep 30\n' % RUN_START)
    INTAKE = ('import os\nevd = os.environ["TEST_EVD"]\nos.makedirs(os.path.join(evd, "loop-intake"), exist_ok=True)\n'
              'open(os.path.join(evd, "loop-intake", "launch-env.sh"), "w").write("export X=1\\n")\nprint("INTAKE READY")\n')

    def __init__(self, case):
        self.case = case
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-run-watch-start-"))
        case.addCleanup(shutil.rmtree, self.tmp, True)
        self.stubs = []
        case.addCleanup(self.assert_no_stub_alive)   # registered before any reap, so it runs after every one
        self.home = os.path.join(self.tmp, "home")
        self.evd = os.path.join(self.tmp, "evidence")
        os.makedirs(self.home)
        os.makedirs(self.evd)
        env = self.genv()
        bare, self.repo = os.path.join(self.tmp, "up.git"), os.path.join(self.tmp, "launch")
        quiet = dict(env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "init", "-q", "--bare", bare], cwd=self.tmp, check=True, **quiet)
        subprocess.run(["git", "init", "-q", self.repo], cwd=self.tmp, check=True, **quiet)
        for argv in (["checkout", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"],
                     ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git"] + argv, cwd=self.repo, check=True, **quiet)
        write(os.path.join(self.repo, "README"), "fixture\n")
        for argv in (["add", "README"], ["commit", "-q", "-m", "fixture"], ["remote", "add", "hub", bare],
                     ["push", "-q", "-u", "hub", "main:main"]):
            subprocess.run(["git"] + argv, cwd=self.repo, check=True, **quiet)
        self.driver, self.intake, self.live, self.canary, self.bin = (
            os.path.join(self.tmp, n) for n in ("driver.sh", "intake.py", "live.sh", "canary.sh", "fakebin"))
        write(self.driver, self.DRIVER, True)
        write(self.intake, self.INTAKE)
        write(self.live, "#!/bin/bash\nexit 0\n", True)
        write(self.canary, "#!/bin/bash\necho 'CANARY WOULD LAND: the replay and the landing stage (1 judge row(s)): stub'\nexit 0\n", True)
        write(self.bin, "#!/bin/sh\nexit 0\n", True)

    def genv(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=self.home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.join(self.home, ".gitconfig"))
        return env

    STUB_PIDS = ("watcher.pid", "driver.pid", "canary.pid")   # every stub the launcher fixtures start records one of these

    @staticmethod
    def pid_alive(pid):
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def reap(self):
        """Nothing a fixture started may outlive the test: every stub the launcher recorded (the watcher, the stub
        driver, the stub canary, each the leader of its own process group) gets TERM first (a watcher that outlives its
        driver reads its death), a short grace, then KILL to its whole group and to the pid itself. A canary stub that
        traps TERM (the case the launcher's own grace tests build) survived the old TERM-only reap and was found
        orphaned for hours (2026-10-21)."""
        found = []
        for d, _, files in os.walk(self.evd):
            for name in self.STUB_PIDS:
                if name in files:
                    try:
                        with open(os.path.join(d, name)) as fh:
                            found.append(int(fh.read().split()[0]))
                    except (OSError, ValueError, IndexError):
                        pass  # sbe: allow-silent a torn or absent pid file names no stub to stop
        found = [p for p in found if p > 1]
        for pid in found:
            for send in (lambda: os.killpg(pid, signal.SIGTERM), lambda: os.kill(pid, signal.SIGTERM)):
                try:
                    send()
                except OSError:
                    pass  # sbe: allow-silent the stub already ended, which is the state this cleanup wants
        deadline = time.time() + 2.0
        while time.time() < deadline and any(self.pid_alive(p) for p in found):
            time.sleep(0.05)
        for pid in found:
            for send in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
                try:
                    send()
                except OSError:
                    pass  # sbe: allow-silent the stub already ended
        self.stubs.extend(found)

    def assert_no_stub_alive(self):
        """Runs after reap (cleanups run last in, first out): no pid a fixture recorded is alive. A stopped child of a
        live parent reads as a zombie until reaped, so wait for it to go for a moment before saying it stands."""
        deadline = time.time() + 3.0
        while time.time() < deadline and any(self.pid_alive(p) for p in self.stubs):
            time.sleep(0.05)
        alive = [p for p in self.stubs if self.pid_alive(p)]
        self.case.assertEqual(alive, [], "a stub the launcher fixture started outlived its test")

    def env(self, **extra):
        env = self.genv()
        env.update(START_RUN_EVIDENCE=self.evd, START_RUN_INTAKE=self.intake, START_RUN_DRIVER=self.driver,
                   START_RUN_LIVE_CHECK=self.live, START_RUN_CANARY=self.canary, TEST_EVD=self.evd,
                   BROTHER_CLAUDE_BIN=self.bin, BROTHER_CODEX_BIN=self.bin, BROTHER_POWER_SOURCE="AC Power",
                   BROTHER_RUNS_ROOT=os.path.join(self.tmp, "runs"))
        env.update(extra)
        return env

    def launch(self, **extra):
        self.case.addCleanup(self.reap)
        p = subprocess.run(["bash", LAUNCHER, "--deadline", DEADLINE, "--budget-usd", "5"], cwd=self.repo, env=self.env(**extra),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=90)
        return p.returncode, p.stdout

    def popen(self, **extra):
        """The launcher as a running process, for a case that signals it."""
        self.case.addCleanup(self.reap)
        p = subprocess.Popen(["bash", LAUNCHER, "--deadline", DEADLINE, "--budget-usd", "5"], cwd=self.repo, env=self.env(**extra),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.case.addCleanup(lambda: p.poll() is None and p.kill())
        return p


class TestRunWatch(Fixture):
    # the three named in the spec
    def test_an_alarm_heartbeat_writes_one_alarm_record(self):
        write(self.hb, json.dumps({"state": "BLOCKED", "reason": "stub", "stamp": now_stamp(), "log": self.log}))
        self.alive_for(4)
        rc, said = self.go()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.records()), 1, "four passes over one ALARM heartbeat: one record")
        self.assertEqual(len(said), 1, "and one announcement")
        with open(os.path.join(self.run_dir, self.records()[0]), encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["kind"], "heartbeat")
        self.assertIn("BLOCKED", rec["cause"])

    def test_the_same_held_cause_twice_announces_once(self):
        write(self.log, RUN_START + "\n" + HELD_PP1 + HELD_PP1 + HELD_PP1)
        self.alive_for(4)
        rc, said = self.go()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.records()), 1, "one HELD cause on every pass: one record")
        self.assertEqual(len(said), 1)
        with open(os.path.join(self.run_dir, self.records()[0]), encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["kind"], "held")
        self.assertIn("PP1.c", rec["cause"])

    def test_the_watcher_exits_when_the_driver_is_gone(self):
        """Through the command line, so a loop that never ends is a TIMEOUT of this test, not a hang of the suite."""
        p = subprocess.run([sys.executable, "-B", WATCH, "--pid", str(dead_pid()), "--log", self.log, "--heartbeat", self.hb,
                            "--run-dir", self.run_dir, "--interval", "30"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.records(), [])

    # the edges the spec lists
    def test_many_different_causes_each_announce_once(self):
        write(self.log, HELD_PP1 + "HELD    I3.a       held because of the clock\nDEPLOY  HELD | judge refused: x\n")
        self.alive_for(4)
        rc, said = self.go()
        self.assertEqual((rc, len(self.records()), len(said)), (0, 3, 3))

    def test_a_cause_seen_on_one_pass_only_is_not_announced(self):
        self.alive_for(3, between=lambda i: write(self.log, HELD_PP1) if i == 1 else write(self.log, ""))
        rc, said = self.go()
        self.assertEqual((rc, self.records(), said), (0, [], []))

    def test_an_empty_log_and_a_missing_heartbeat_write_nothing(self):
        write(self.log, "")
        self.alive_for(3)
        rc, said = self.go()
        self.assertEqual((rc, self.records(), said), (0, [], []))

    def test_a_driver_already_gone_is_accepted(self):
        self.alive_for(0)
        self.assertEqual(self.go()[0], 0)

    def test_a_missing_log_is_accepted(self):
        self.alive_for(2)
        self.assertEqual(self.go()[0], 0)

    def test_a_heartbeat_of_another_run_is_not_this_runs_alarm(self):
        write(self.hb, json.dumps({"state": "BLOCKED", "reason": "an earlier run", "stamp": now_stamp(),
                                   "log": os.path.join(self.tmp, "other.log")}))
        self.alive_for(3)
        rc, said = self.go()
        self.assertEqual((rc, self.records(), said), (0, [], []))

    def test_a_healthy_heartbeat_writes_nothing_and_a_clean_end_adds_nothing(self):
        self.pulse("PASS", 1, passes_since_landing=0)
        self.alive_for(3, between=lambda i: self.pulse("FINISHED", 0, reason="deadline") if i == 3 else None)
        rc, said = self.go()
        self.assertEqual((rc, self.records(), said), (0, [], []))

    def test_a_torn_heartbeat_is_ignored_and_the_watcher_keeps_reading(self):
        write(self.hb, '{"state": "BLOC')
        write(self.log, HELD_PP1)
        self.alive_for(3)
        rc, said = self.go()
        self.assertEqual((rc, len(self.records())), (0, 1), "the torn pulse is skipped; the HELD line still counts")

    def test_an_unwritable_run_dir_exits_2_before_any_pass(self):
        blocked = os.path.join(self.tmp, "afile")
        write(blocked, "not a folder")
        write(self.log, HELD_PP1)
        calls = self.alive_for(5)
        rc = rw.watch(4242, self.log, self.hb, blocked, interval=0.001, notify=lambda t, m: None)
        self.assertEqual(rc, 2)
        self.assertEqual(calls["n"], 0, "refused before it looked at the driver")

    def test_a_record_that_cannot_be_written_mid_run_exits_2_after_announcing(self):
        write(self.log, HELD_PP1)

        def vanish(i):                    # the folder was there at the start and is a file when the first record is due
            if i == 1:
                shutil.rmtree(self.run_dir)
                write(self.run_dir, "now a file")
        self.alive_for(6, between=vanish)
        rc, said = self.go()
        self.assertEqual(rc, 2)
        self.assertEqual(len(said), 1, "the announcement still went out")

    def test_refusals_return_2_and_run_no_pass(self):
        calls = self.alive_for(3)
        write(self.log, "")
        os.makedirs(os.path.join(self.tmp, "adir"))
        cases = (
            ("interval zero", (4242, self.log, self.hb, self.run_dir), {"interval": 0}),
            ("interval negative", (4242, self.log, self.hb, self.run_dir), {"interval": -1}),
            ("interval a string", (4242, self.log, self.hb, self.run_dir), {"interval": "30"}),
            ("interval a bool", (4242, self.log, self.hb, self.run_dir), {"interval": True}),
            ("interval NaN", (4242, self.log, self.hb, self.run_dir), {"interval": float("nan")}),
            ("pid a bool", (True, self.log, self.hb, self.run_dir), {}),
            ("pid not an int", ("4242", self.log, self.hb, self.run_dir), {}),
            ("log a directory", (4242, os.path.join(self.tmp, "adir"), self.hb, self.run_dir), {}),
            ("run_dir an existing file", (4242, self.log, self.hb, self.log), {}))
        for label, args, kw in cases:
            with self.subTest(label):
                self.assertEqual(rw.watch(*args, notify=lambda t, m: None, **dict({"interval": 0.001}, **kw)), 2)
        self.assertEqual(calls["n"], 0)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes into a read only folder")
    def test_a_read_only_run_dir_is_refused(self):
        os.chmod(self.run_dir, 0o555)
        self.addCleanup(os.chmod, self.run_dir, 0o755)
        calls = self.alive_for(3)
        self.assertEqual(rw.watch(4242, self.log, self.hb, self.run_dir, interval=0.001, notify=lambda t, m: None), 2)
        self.assertEqual(calls["n"], 0)

    def test_a_pid_that_exists_but_cannot_be_signalled_is_alive(self):
        real = os.kill

        def denied(pid, sig):
            raise PermissionError(1, "not permitted")
        os.kill = denied
        self.addCleanup(setattr, os, "kill", real)
        self.assertTrue(rw._pid_alive(4242))

    def pulse(self, state, minutes_ago, log=True, **extra):
        """Write a heartbeat record the way loop_heartbeat does: this run's log on a start pulse, none on a pass pulse."""
        stamp = (datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
        rec = {"state": state, "stamp": stamp, "passes": 1}
        if log is True:
            rec["log"] = self.log
        elif log:
            rec["log"] = log
        rec.update(extra)
        write(self.hb, json.dumps(rec))

    def run_sequence(self, steps, final=None):
        """One heartbeat per pass from `steps` ((state, minutes_ago, log, extra) tuples), then the driver ends; `final`,
        when given, is the last heartbeat written before the end is noticed. Returns (rc, announced messages)."""
        def between(i):
            if i < len(steps):
                state, ago = steps[i][0], steps[i][1]
                log = steps[i][2] if len(steps[i]) > 2 else True
                self.pulse(state, ago, log, **(steps[i][3] if len(steps[i]) > 3 else {}))
            elif i == len(steps) and final is not None:
                self.pulse(final[0], final[1], **(final[2] if len(final) > 2 else {}))
        self.alive_for(len(steps), between=between)
        rc, said = self.go()
        return rc, [m for _t, m in said]

    # review round 1, B1: one healthy long pass must not use up the key of a later wedge (the reviewer's adv_watch2)
    def test_a_healthy_long_pass_does_not_use_up_a_later_wedge(self):
        rc, said = self.run_sequence([("STARTED", 13), ("PASS", 1, ""), ("PASS", 30, ""), ("PASS", 1, ""), ("PASS", 240, ""), ("PASS", 600, "")])
        self.assertEqual(rc, 0)
        self.assertEqual(len(said), 1, said)
        self.assertIn("episode 1", said[0])
        self.assertEqual(len(self.records()), 1, "the same wedge on later passes is one episode: one record")

    def test_a_wedge_that_ended_and_came_back_is_announced_again(self):
        rc, said = self.run_sequence([("BLOCKED", 0), ("BLOCKED", 0), ("PASS", 1, ""), ("PASS", 1, ""), ("BLOCKED", 0, ""), ("BLOCKED", 0, "")])
        self.assertEqual(len(said), 2, said)
        self.assertIn("episode 1", said[0])
        self.assertIn("episode 2", said[1])
        self.assertEqual(len(self.records()), 2)

    def test_a_pass_a_normal_driver_reaches_is_not_an_alarm_and_past_the_bound_it_is(self):
        rc, said = self.run_sequence([("STARTED", 75), ("PASS", 100), ("PASS", 149)], final=("FINISHED", 0))
        self.assertEqual((rc, said, self.records()), (0, [], []), "the 75 minute first pass measured on 2026-10-19 is a live driver")
        for state, ago in (("STARTED", 151), ("PASS", 151)):
            with self.subTest(state=state):
                os.makedirs(os.path.join(self.tmp, state), exist_ok=True)
                self.run_dir = os.path.join(self.tmp, state)
                rc, said = self.run_sequence([(state, ago), (state, ago)], final=("FINISHED", 0))
                self.assertEqual(len(said), 1, said)

    def test_the_watchers_bound_is_named_and_above_the_measured_pass(self):
        self.assertGreaterEqual(rw.WATCH_BOUND_MINUTES, 2 * 75)

    def test_the_rule_with_the_watchers_bound(self):
        now = lambda m: (datetime.datetime.now() - datetime.timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
        for rec, want in (({"state": "STARTED", "stamp": now(80)}, "PRODUCING"), ({"state": "STARTED", "stamp": now(200)}, "ALARM"),
                          ({"state": "PASS", "stamp": now(149)}, "PRODUCING"), ({"state": "PASS", "stamp": now(151)}, "ALARM"),
                          ({"state": "BLOCKED", "stamp": now(0)}, "ALARM"), ({"state": "PASS", "stamp": now(-360)}, "ALARM"),
                          ({"state": "PASS", "stamp": "nonsense"}, "ALARM"), ({"state": "FINISHED", "stamp": now(0)}, "PRODUCING"),
                          ({"state": "PASS", "stamp": now(1), "passes": "banana"}, "NO-DATA"), ({"state": 5, "stamp": now(1)}, "NO-DATA"),
                          ({"state": ["x"], "stamp": now(1)}, "NO-DATA"), (None, "ALARM")):
            with self.subTest(rec=rec):
                self.assertEqual(rw.classify(rec), want)

    def test_an_unreadable_pulse_in_the_middle_of_an_episode_does_not_end_it(self):
        rc, said = self.run_sequence([("BLOCKED", 0), ("BLOCKED", 0), (5, 0), ("BLOCKED", 0), ("BLOCKED", 0)])
        self.assertEqual(len(said), 1, said)

    # M1: a dead driver is not silent
    def test_a_driver_that_died_without_a_clean_end_is_announced(self):
        for state, extra in (("STARTED", {}), ("PASS", {}), ("UNPRODUCTIVE", {}), ("BLOCKED", {}), ("DEADLINE", {"end_failed": True})):
            with self.subTest(state=state):
                os.makedirs(os.path.join(self.tmp, "d" + state), exist_ok=True)
                self.run_dir = os.path.join(self.tmp, "d" + state)
                rc, said = self.run_sequence([(state, 1, True, extra)])
                self.assertEqual(rc, 0)
                self.assertEqual(len(said), 1, said)

    def test_a_clean_looking_end_whose_end_failed_is_announced_at_death(self):
        rc, said = self.run_sequence([("PASS", 1)], final=("DEADLINE", 0, {"end_failed": True}))
        self.assertEqual(len(said), 1, said)
        self.assertIn("driver gone", said[0])
        self.assertEqual(len(self.records()), 1)

    def test_an_unwritable_run_folder_at_death_is_exit_2_after_announcing(self):
        self.pulse("PASS", 1)

        def between(i):
            if i == 1:
                shutil.rmtree(self.run_dir)
                write(self.run_dir, "now a file")
        self.alive_for(1, between=between)
        rc, said = self.go()
        self.assertEqual(rc, 2)
        self.assertEqual(len(said), 1, "announced even though the record could not be written")

    def test_a_driver_that_ended_cleanly_is_not_announced(self):
        for state in ("FINISHED", "DEADLINE", "STOPPED"):
            with self.subTest(state=state):
                os.makedirs(os.path.join(self.tmp, "c" + state), exist_ok=True)
                self.run_dir = os.path.join(self.tmp, "c" + state)
                rc, said = self.run_sequence([("PASS", 1), (state, 0)])
                self.assertEqual((rc, said), (0, []))

    def test_a_driver_that_died_after_an_announced_alarm_is_not_announced_twice(self):
        rc, said = self.run_sequence([("BLOCKED", 0), ("BLOCKED", 0)])
        self.assertEqual(len(said), 1, said)

    def test_a_driver_that_died_with_no_heartbeat_of_this_run_says_nothing(self):
        self.alive_for(2)
        self.assertEqual((self.go()[0], self.records()), (0, []))
        self.pulse("PASS", 1, log=os.path.join(self.tmp, "other.log"))
        self.alive_for(2)
        self.assertEqual((self.go()[0], self.records()), (0, []), "another run's last pulse is not this run's death")

    # minors: unreadable and unowned pulses
    def test_a_heartbeat_with_an_unreadable_field_never_crashes_the_watcher(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for label, rec, judged in (("state a number", {"state": 5}, False), ("state a list", {"state": ["x"]}, False),
                                   ("state a dict", {"state": {}}, False), ("passes a string", {"state": "PASS", "passes": "banana"}, False),
                                   ("passes_since_landing a list", {"state": "PASS", "passes_since_landing": []}, True),
                                   ("stamp a number", {"state": "PASS", "stamp": 7}, True), ("log a list", {"state": "PASS", "log": ["x"]}, False)):
            with self.subTest(label):
                write(self.hb, json.dumps(dict({"stamp": now, "log": self.log}, **rec)))
                write(self.log, HELD_PP1)
                self.alive_for(3)
                rc, said = self.go()
                self.assertEqual(rc, 0, label)
                if not judged:      # a pulse the rule cannot read is NO-DATA: it is ignored, never an alarm of its own
                    self.assertEqual([m for _t, m in said if "heartbeat ALARM" in m or ("driver gone" in m and not isinstance(rec["state"], str))], [], label)
                for name in self.records():
                    os.remove(os.path.join(self.run_dir, name))

    def test_a_heartbeat_naming_no_log_is_not_this_runs_unless_it_follows_one_that_is_or_is_newer_than_the_watcher(self):
        self.pulse("BLOCKED", 60, log=False)             # the run before: no log, an hour old
        self.alive_for(3)
        rc, said = self.go()
        self.assertEqual((rc, self.records(), said), (0, [], []))
        stamp = (datetime.datetime.now() + datetime.timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S")
        write(self.hb, json.dumps({"state": "BLOCKED", "stamp": stamp, "passes": 3}))   # written by the driver that is running now
        self.alive_for(3)
        rc, said = self.go()
        self.assertEqual(len(said), 1, said)

    # M2: the alert window beside the banner
    def test_notify_posts_a_banner_and_a_detached_alert(self):
        calls = []
        real = rw.subprocess.run
        rw.subprocess.run = lambda argv, **kw: calls.append((list(argv), kw))
        self.addCleanup(setattr, rw.subprocess, "run", real)
        rw._notify("brother.loop watch", 'a "quoted" cause\\')
        self.assertEqual(len(calls), 2)
        banner, alert = calls[0][0], calls[1][0]
        self.assertEqual(banner[:2], ["osascript", "-e"])
        self.assertIn("display notification", banner[2])
        self.assertEqual(alert[:2], ["sh", "-c"])
        self.assertTrue(alert[2].rstrip().endswith("&"), "detached: it must not hold the watcher while the window is open")
        self.assertIn("display alert", alert[-1])
        self.assertIn("as critical", alert[-1])
        for argv, kw in calls:
            self.assertTrue(kw.get("check"), "the exit code is read, not swallowed")
            self.assertNotIn('"quoted"', " ".join(argv))

    def test_notify_never_raises(self):
        real = rw.subprocess.run

        def boom(argv, **kw):
            raise OSError("no osascript here")
        rw.subprocess.run = boom
        self.addCleanup(setattr, rw.subprocess, "run", real)
        rw._notify("t", "m")

    # the readers
    def test_read_heartbeat(self):
        self.assertIsNone(rw.read_heartbeat(self.hb))
        write(self.hb, "[1, 2]")
        self.assertIsNone(rw.read_heartbeat(self.hb), "not a dict")
        write(self.hb, "{torn")
        self.assertIsNone(rw.read_heartbeat(self.hb))
        self.assertIsNone(rw.read_heartbeat(self.tmp), "a directory")
        self.assertIsNone(rw.read_heartbeat(None))
        write(self.hb, '{"state": "PASS"}')
        self.assertEqual(rw.read_heartbeat(self.hb), {"state": "PASS"})

    def test_held_causes_reads_both_line_shapes_and_nothing_else(self):
        write(self.log, "NOTE STARTED: x\n" + HELD_PP1 + "HELD    I3.a      \n"
              "DEPLOY  HELD | judge refused: plugin-runtime-tests-fast FAIL exit 1\n"
              "DEPLOY  PREPARED | all good\n"
              "LANDED  I11.a | units with every sub unit landed\n"
              "HELD at the deploy step, nothing pushed: not a HELD row\n"
              "HELD: pass exit 45 is not terminal\n")
        got = rw.held_causes(self.log)
        self.assertEqual(len(got), 3, got)
        self.assertEqual(rw.held_causes(self.log_with("DEPLOY  HELD | \n")), ["deploy: UNEXPLAINED"])
        self.assertTrue(got[0].startswith("PP1.c held on the unchanged landing tree"))
        self.assertEqual(got[1], "I3.a UNEXPLAINED", "a HELD row naming no cause says so")
        self.assertEqual(got[2], "deploy: judge refused: plugin-runtime-tests-fast FAIL exit 1")
        self.assertEqual(rw.held_causes(os.path.join(self.tmp, "absent.log")), [])
        self.assertEqual(rw.held_causes(self.tmp), [], "a directory is unreadable, not a log")
        self.assertEqual(rw.held_causes(None), [])

    def log_with(self, text):
        write(self.log, text)
        return self.log

    def test_a_previous_runs_held_rows_in_an_appended_log_never_alarm_for_the_new_run(self):
        mark = RUN_START + "\n"
        write(self.log, mark + HELD_PP1 + "HELD    I3.a       previous run's hold\nDEPLOY  HELD | previous detail\n" + mark + "LANDED  X.a | ok\n")
        self.assertEqual(rw.held_causes(self.log), [], "only the last run is read")
        self.alive_for(4)
        rc, said = self.go()
        self.assertEqual((rc, self.records(), said), (0, [], []))
        with open(self.log, "a") as fh:
            fh.write("HELD    PP1.c      this run's own hold\n")
        self.assertEqual(rw.held_causes(self.log), ["PP1.c this run's own hold"])
        import loop_report
        self.assertIs(loop_report._RW.last_run_lines, rw.last_run_lines, "loop_report reads the same slicer, not a copy")

    def test_pid_alive(self):
        self.assertTrue(rw._pid_alive(os.getpid()))
        self.assertFalse(rw._pid_alive(dead_pid()))
        for bad in (True, False, "1", None, 0, -1, 1.5):
            with self.subTest(bad=bad):
                self.assertFalse(rw._pid_alive(bad))

    def test_the_heartbeat_rule_is_the_heartbeat_modules_own(self):
        import loop_heartbeat
        self.assertIs(rw._hb, loop_heartbeat)
        for rec in (None, {"state": "PASS", "stamp": now_stamp()}, {"state": "BLOCKED", "stamp": now_stamp()}):
            with self.subTest(rec=rec):
                self.assertEqual(rw.classify(rec), loop_heartbeat.classify(rec)[0])

    # start_run.sh arms the watcher (REQ-LR1-C04)
    def test_the_launcher_arms_the_watcher_and_records_its_pid(self):
        launch = Launch(self)
        rc, out = launch.launch()
        self.assertEqual(rc, 0, out)
        line = [l for l in out.splitlines() if l.startswith("OK   watch")]
        self.assertEqual(len(line), 1, out)
        folder = os.path.join(launch.evd, "loop-starts")
        pids = [os.path.join(d, "watcher.pid") for d, _, files in os.walk(folder) if "watcher.pid" in files]
        self.assertEqual(len(pids), 1, "the watcher's pid is recorded in a run folder (here the launch's own: the stub driver names none)")
        with open(pids[0]) as fh:
            pid = int(fh.read().split()[0])
        self.assertIn("pid %d" % pid, line[0])

    def test_a_watcher_that_cannot_start_refuses_the_launch(self):
        bad = os.path.join(self.tmp, "bad_watch.py")
        write(bad, "import sys\nsys.exit(7)\n")
        launch = Launch(self)
        rc, out = launch.launch(START_RUN_WATCH=bad)
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [watch]:", out)
        self.assertNotIn("STARTED:", out)
        with open(os.path.join(launch.evd, "driver.pid")) as fh:
            driver = int(fh.read().split()[0])
        for _ in range(30):
            if not rw._pid_alive(driver):
                break
            time.sleep(0.1)
        self.assertFalse(rw._pid_alive(driver), "a run nobody watches is not left running: the driver was stopped")

    def test_a_driver_that_names_no_log_refuses_the_launch(self):
        launch = Launch(self)
        nolog = os.path.join(launch.tmp, "nolog_driver.sh")
        write(nolog, '#!/bin/bash\necho $$ > "$TEST_EVD/driver.pid"\necho "LOOP UNTIL $1 every $2 s"\nsleep 30\n', True)
        rc, out = launch.launch(START_RUN_DRIVER=nolog)
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [watch]: the driver's log path could not be read", out)

    def test_a_missing_watcher_refuses_the_launch(self):
        rc, out = Launch(self).launch(START_RUN_WATCH=os.path.join(self.tmp, "absent.py"))
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [watch]: no watcher", out)


class TestStubReap(unittest.TestCase):
    """LR1 tests reap their stub canaries: the fixture's cleanup stops a stub that ignores TERM, whole group, and the
    cleanup asserts none is left."""

    def test_a_stub_that_ignores_term_is_killed_by_the_fixture_cleanup(self):
        launch = Launch(self)
        stub = os.path.join(launch.tmp, "stubborn.sh")
        write(stub, '#!/bin/bash\necho $$ > "$TEST_EVD/canary.pid"\ntrap \'\' TERM\nwhile :; do sleep 0.2; done\n', True)
        # started like the launcher starts its canary (its own process group), detached so init reaps it
        subprocess.run(["bash", "-c", "( perl -e 'setpgrp(0, 0); exec @ARGV' bash \"$0\" >/dev/null 2>&1 & )", stub],
                       env=launch.env(), check=True, timeout=30)
        pidfile = os.path.join(launch.evd, "canary.pid")
        for _ in range(100):
            if os.path.exists(pidfile) and open(pidfile).read().strip():
                break
            time.sleep(0.05)
        pid = int(open(pidfile).read().split()[0])
        self.assertTrue(Launch.pid_alive(pid), "the stub never started")
        launch.reap()
        self.assertIn(pid, launch.stubs)
        launch.assert_no_stub_alive()
        self.assertFalse(Launch.pid_alive(pid), "a stub that traps TERM outlived the fixture cleanup")


if __name__ == "__main__":
    unittest.main()
