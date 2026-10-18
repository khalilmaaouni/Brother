#!/usr/bin/env python3
"""1.1.6 S2: the run's login holder is wired, end to end, from start_run.sh to the seat that asks it.

WHY. The night of 2026-10-17 merged S2 (the launcher starting `brother_login.py hold --run DIR` detached) and S1.b
(the holder now STARTS its command and answers only on an unnamed socket pair: `hold --run DIR -- COMMAND`). Both
suites stayed green after the merge, and the token lane launch was broken: the holder refused the old command line,
so start_run.sh refused STARTED. No test drove start_run.sh with the real brother_login.py. This file does.

TESTED AT THE ENTRY POINTS. start_run.sh as a subprocess with its seams (the S2 launcher fixture,
test_seat_gate_both_hosts.LaunchFixture), the REAL brother_login.py behind START_RUN_HOLDER (a two line wrapper that
points its READER at a fake Keychain reader and calls brother_login.main), a stub driver that makes its run folder and
starts a seat, and the seat is the real native_worker.login_child. The Python hops between the driver and the seat
are driven at their own entry points: runner_pool.main through the LA5.a loader (test_runner_pool_seats), and the
real unit_runner.py script through the native round fixture (test_unit_runner_native.Run). stop_loop.sh as a
subprocess limited by STOP_LOOP_ONLY to this test's own folder.

THE FAKE READER answers the login once (at launch) and then fails like a locked Keychain; every seat's own fallback
reader is locked from the start. So a seat that gets the login got it from the holder. The canary token is searched for
in every file the launch writes, and in every output a case reads.

The mutations that must turn this suite red, each against its own case:
  M-S2W-OLD-CLI      start_run.sh starts `hold --run DIR` without `-- COMMAND`  -> test_the_token_lane_start_hands_...
  M-S2W-NO-POOL-FD   runner_pool hands the unit runner no descriptor          -> test_the_pool_hands_the_holder_...
  M-S2W-NO-POP       runner_pool keeps the holder's name for every child      -> test_the_pool_hands_the_holder_...
  M-S2W-NO-RUNNER-FD unit_runner hands native_worker no descriptor            -> test_the_unit_runner_hands_...
  M-S2W-FIN-NAME     the finisher job keeps the holder's name                 -> test_the_finisher_never_names_...
  M-S2W-NO-REAP      stop_loop.sh no longer matches the new hold command line -> test_stop_ends_the_holder_with_the_run
  M-S2W-ANY-FD       take_hold passes a descriptor that is not a socket       -> test_the_hop_rule
  M-S2W-NO-PIDFILE   start_run.sh writes no holder pid file                  -> test_the_token_lane_start_hands_...
  M-S2W-SILENT-DEATH a holder that exits before LOOP UNTIL reads [driver]    -> test_a_reader_that_fails_at_launch_...
  M-S2W-ALWAYS       the holder starts off the token lane too                -> test_the_cli_lane_is_unchanged
  M-S2W-HOLDER-AS-DRIVER the launch record names the holder as the driver   -> test_the_token_lane_start_hands_...
Round 1 of the S2 review (2026-10-18), one case each:
  M-S2R-NO-SELF       take_hold keeps nothing for its own process          -> test_the_holding_process_keeps_serving_...
                                                                              and test_an_in_process_role_of_the_unit_...
  M-S2R-ANY-MAP       a mapping handed to take_hold sets the process hold  -> test_a_given_mapping_never_sets_...
  M-S2R-INHERIT       take_hold leaves the descriptor inheritable          -> test_the_held_descriptor_is_closed_to_...
  M-S2R-DEAD-QUIET    ask_holder answers "" for a dead named holder        -> test_a_holder_that_dies_mid_run_refuses_...
  M-S2R-CANCELLED     a stopped holder prints the sign in's Cancelled line -> test_a_stopped_holder_says_what_ended
  M-S2R-RUNNERS-REAP  stop_loop.sh --runners-only reaps holders            -> test_runners_only_leaves_the_holder
  M-S2R-SHARED-GROUP  the holder starts the driver in its own group        -> test_the_driver_leads_its_own_group,
                                                                              test_a_full_stop_ends_the_drivers_...
  M-S2R-NO-REFRESH-RX loop_procs does not name native_worker.py            -> test_a_refresh_and_a_seat_worker_...
  M-S2R-DPID-KILL     a refused launch stops the holder's group only       -> test_a_holder_refusal_after_the_driver_...
  M-S2R-ASK-OWN       ask_holder ignores the descriptor take_hold kept     -> test_the_holding_process_keeps_serving_...
  M-S2R-HINT          the ended holder's words leave LOGIN_HINTS           -> test_seat_lost_parks_once ...held_login_refusal...
  M-S2R-FIN-SILENT    the finisher calls on without a login               -> test_the_finisher_refuses_loudly_...
  M-S2R-FIN-NO-ALARM  the finisher job writes no alarm on a refusal        -> test_the_finisher_job_raises_the_alarm
Round 3 (2026-10-18), one case each (sweep: ~/.claude/evidence/1.1.6-s2r3/mutate.py):
  M-S2R3-NO-MARK        hold() does not mark its run                       -> test_the_holder_marks_its_run
  M-S2R3-MARK-IGNORED   an unhanded child of a held run reads fresh         -> test_a_held_run_child_handed_no_holder_...
  M-S2R3-MARK-ALWAYS    every unhanded child refuses, held run or not       -> test_outside_a_held_run_the_fresh_read_stands
  M-S2R3-NO-ROLLBACK    BROTHER_LOOP_HOLD=off no longer reads fresh         -> test_the_rollback_reads_fresh_in_a_held_run
  M-S2R3-FIN-MARK       the finisher job keeps the mark                     -> test_the_finisher_job_clears_the_mark
  M-S2R3-DIAG-*         diag_round serves, refuses a bridge model, or refuses an unheld run -> HeldRun's three diag cases
  M-S2R3-NW-NO-TAKE     native_worker.main takes no hold                    -> test_native_worker_takes_the_hold_at_its_start
  M-S2R3-SUITE-NAME     suite_env keeps the holder's name                   -> test_the_suite_env_never_names_the_holder
  M-S2R3-BROKEN-NUMBER  take_hold keeps a broken name as its number         -> test_an_invalid_hop_name_is_not_reused_...
  M-S2R3-BROKEN-EMPTY   take_hold keeps nothing for a broken name           -> test_a_broken_hop_is_loud_for_the_process_...
  M-S2R3-PRELUDE-INSIDE start_run.sh sources the launch settings inside the holder's command -> test_a_child_of_the_...
  M-S2R3-OSA-UNWRAPPED  loop_until.sh starts osascript with the descriptor  -> test_only_the_pass_and_the_refresh_get_...
  M-S2R3-DRIVER-DROPS-MARK / M-S2R3-PASS-DROPS-MARK  the driver or the pass unsets the mark -> DriverChildren, PassChildren

OUT OF SCOPE, named: the stub driver starts its seat directly (bash to Python), so the driver's own pass wrapper
(loop_until.sh run_pass, an os.execvp that keeps inheritable descriptors) is not run here; the hops after it are.
Roles a unit runner calls in process (repair advisor, build plan) are served from the descriptor it keeps (round 1);
the build plan stand in asks through the real brother_login, the real model_call._TokenLane is not run (it needs
sandbox-exec and a claude program). Python 3.9 and 3.13, standard library only, no network, the real Keychain is
never read.
Run: python3 -B scripts/loop/test_login_hold_wired.py
"""
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "stop_loop.sh")
sys.path.insert(0, HERE)
import brother_login as BL  # noqa: E402
import test_seat_gate_both_hosts as TSG  # noqa: E402  the S2 launcher fixture

PREFIX = "sk-" + "ant-oat01-"   # joined so no file holds the key shape at rest; the push gate scans for it
CANARY = PREFIX + "WIREDCANARY" + "k7Pz" * 8 + "-_END"
CANARY_SHA = hashlib.sha256(CANARY.encode()).hexdigest()

#: brother_login.py itself, behind a fake reader: the launcher's START_RUN_HOLDER. argv: what start_run.sh passes.
WRAPPER = r'''import json, os, sys
with open(%(marker)r, "a") as fh:
    fh.write("%%d %%s\n" %% (os.getpid(), json.dumps(sys.argv[1:])))
sys.path.insert(0, %(here)r)
import brother_login as BL
BL.READER = %(reader)r
sys.exit(BL.main(sys.argv[1:]))
'''

#: The fake Keychain reader: the login once, then locked. quiet() hands it a scrubbed environment, so its paths are
#: written in. mode "dead" is locked from the start (a reader that fails AT launch).
READER = '''#!/bin/sh
echo "$1" >> %(calls)s
[ "$1" = get ] || exit 9
[ -e %(locked)s ] && exit 9
: > %(locked)s
printf '%%s\\n' '%(token)s'
'''

#: The driver: its run folder, its first line, then one token lane seat, then it runs on (a run that is alive).
DRIVER = '''#!/bin/bash
RUN="$BROTHER_RUNS_ROOT/run-20990101-000000-$$"
mkdir -p "$RUN"
echo "LOOP UNTIL $1 | gap $2s | log $TEST_EVD/loop.log"
python3 -B "$TEST_SEAT" "$TEST_EVD/seat.json"
sleep "${TEST_DRIVER_SLEEP:-3}"
'''

#: One seat: native_worker.login_child with its own fallback reader locked. Writes what it read, as a digest only.
SEAT = r'''import hashlib, json, os, sys
sys.path.insert(0, %(here)r)
import brother_login as BL
import native_worker as NW
BL.READER = %(dead)r
os.environ["BROTHER_LOOP_TOKEN"] = "on"
res = {"hold_fd_var": os.environ.get(BL.HOLD_FD_VAR, ""), "locked_before": os.path.exists(%(locked)r)}
try:
    env, fd = NW.login_child()
    try:
        res["seat"] = hashlib.sha256(os.read(fd, 65536)).hexdigest()
    finally:
        os.close(fd)
    res["names_holder"] = BL.HOLD_FD_VAR in env
except BL.Failure as exc:
    res["seat"] = "FAILURE " + str(exc)
with open(sys.argv[1] + ".tmp", "w") as fh:
    json.dump(res, fh)
os.rename(sys.argv[1] + ".tmp", sys.argv[1])
'''

#: A process of the same uid OUTSIDE the spawn chain, knowing the descriptor number a seat was told. It asks the
#: holder the documented way and through a seat's own path. Prints what it tried, never what it read; exit 3 on a login.
SIBLING = r'''import hashlib, os, sys
sys.path.insert(0, %(here)r)
import brother_login as BL
import native_worker as NW
BL.READER = %(dead)r
os.environ["BROTHER_LOOP_TOKEN"] = "on"
got = b""
for name, ask in (("ask", lambda: BL.ask_holder().encode()), ("seat", lambda: os.read(NW.login_child()[1], 65536))):
    try:
        got += ask()
        print(name, "answered")
    except BL.Failure as exc:
        print(name, "refused Failure")
sys.exit(3 if hashlib.sha256(got).hexdigest() == %(sha)r or got.startswith(b"sk-") else 0)
'''


def _write(path, body, mode=0o600):
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, mode)
    return path


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class WiredFixture(TSG.LaunchFixture):
    """The launcher fixture with the real holder behind a fake reader, no cases of its own."""

    def setUp(self):
        TSG.LaunchFixture.setUp(self)
        self.locked = os.path.join(self.tmp, "keychain-locked")
        self.calls = os.path.join(self.tmp, "reader-calls")
        self.marker = os.path.join(self.tmp, "holder-started")
        self.reader = _write(os.path.join(self.tmp, "reader.sh"), READER % {
            "calls": self.calls, "locked": self.locked, "token": CANARY}, 0o700)
        self.dead = _write(os.path.join(self.tmp, "dead-reader.sh"), "#!/bin/sh\nexit 9\n", 0o700)
        self.wrapper = _write(os.path.join(self.tmp, "holder_wrapper.py"), WRAPPER % {
            "marker": self.marker, "here": HERE, "reader": self.reader})
        self.driver = _write(os.path.join(self.tmp, "wired_driver.sh"), DRIVER)
        self.seat_py = _write(os.path.join(self.tmp, "seat.py"), SEAT % {
            "here": HERE, "dead": self.dead, "locked": self.locked})
        self.sibling = _write(os.path.join(self.tmp, "sibling.py"), SIBLING % {
            "here": HERE, "dead": self.dead, "sha": CANARY_SHA})
        self.seat_json = os.path.join(self.evd, "seat.json")
        self.addCleanup(self._kill_runs)

    def _pidfiles(self):
        out = []
        for d in sorted(os.listdir(self.runs)):
            f = os.path.join(self.runs, d, "login-holder.pid")
            if os.path.exists(f):
                with open(f) as fh:
                    out.append(int(fh.read().split()[0]))
        return out

    def holders(self):
        """[(pid, argv)] of every holder this test started, as each wrote itself (no process table needed)."""
        if not os.path.exists(self.marker):
            return []
        with open(self.marker) as fh:
            return [(int(l.split(" ", 1)[0]), json.loads(l.split(" ", 1)[1])) for l in fh if l.strip()]

    def _kill_runs(self):
        """Every holder this test started, each driver's own group (round 1: the driver leads its group again, the
        holder is not in it) and any refresh stand in: ended by the pids they wrote."""
        pids = [pid for pid, _argv in self.holders()]
        pids += [int(d.rsplit("-", 1)[1]) for d in os.listdir(self.runs) if d.rsplit("-", 1)[-1].isdigit()]
        refresh = os.path.join(self.evd, "refresh.pid")
        if os.path.exists(refresh):
            with open(refresh) as fh:
                pids.append(int(fh.read().strip() or 0))
        for pid in [p for p in pids if p > 1]:
            for kill in (os.killpg, os.kill):
                try:
                    kill(pid, signal.SIGKILL)
                except OSError:
                    pass  # sbe: allow-silent already gone, the state this cleanup wants

    def go(self, lane="on", **extra):
        extra.setdefault("TEST_DRIVER_SLEEP", "20")
        return self.launch(TEST_SEATS="in", BROTHER_LOOP_TOKEN=lane, START_RUN_HOLDER=self.wrapper,
                           START_RUN_DRIVER=self.driver, TEST_SEAT=self.seat_py, **extra)

    def seat(self):
        deadline = time.time() + 30
        while not os.path.exists(self.seat_json) and time.time() < deadline:
            time.sleep(0.1)
        with open(self.seat_json) as fh:
            return json.load(fh)

    def assertNoCanaryOnDisk(self, *texts):
        for text in texts:
            self.assertNotIn(CANARY, text)
        for root, _, files in os.walk(self.tmp):
            for name in files:
                path = os.path.join(root, name)
                if path == self.reader or os.path.islink(path):
                    continue
                try:
                    with open(path, "rb") as fh:
                        self.assertNotIn(CANARY.encode(), fh.read(), path)
                except OSError:
                    continue  # sbe: allow-silent a socket or a vanished file holds no bytes to search



class TokenLaneLaunch(WiredFixture):
    """start_run.sh with the real holder: STARTED, a seat served, nobody else served, the reader failing after launch."""

    def test_the_token_lane_start_hands_the_login_to_a_seat(self):
        """REQ-S2W-01: STARTED on the token lane with the real holder, and a seat the driver starts gets the login on
        its descriptor (its own reader locked), the holder's pid in the run folder, the holder alive with the run."""
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        self.assertIn("STARTED", out)
        self.assertIn("OK   holder", out)
        res = self.seat()
        self.assertEqual(res["seat"], CANARY_SHA, res)
        self.assertRegex(res["hold_fd_var"], r"^[0-9]+$")
        self.assertFalse(res["names_holder"], "a seat's own child is never told about the holder")
        pids = self._pidfiles()
        self.assertEqual(len(pids), 1, out)
        self.assertTrue(_alive(pids[0]))
        (hpid, argv), = self.holders()
        self.assertEqual(hpid, pids[0], "the pid file names the holder that ran")
        self.assertEqual(argv[:2], ["hold", "--run"])
        self.assertEqual(argv[3], "--", "the holder starts the driver")
        self.assertIn(self.driver, argv, "and the driver is its command")
        starts = os.path.join(self.evd, "loop-starts")
        recs = [n for n in os.listdir(starts) if n.startswith("launch-") and n.endswith(".json")]
        self.assertEqual(len(recs), 1, recs)
        with open(os.path.join(starts, recs[0])) as fh:
            rec = json.load(fh)
        runs = [os.path.join(self.runs, d) for d in os.listdir(self.runs)]
        self.assertEqual(rec["run_dir"], runs[0], "the launch record names the driver's run folder")
        self.assertNotEqual(rec["driver_pid"], hpid, "and the driver, never the holder that started it")
        self.assertTrue(runs[0].endswith("-%d" % rec["driver_pid"]), (runs, rec))
        self.assertNoCanaryOnDisk(out)

    def test_a_reader_that_fails_after_launch_still_starts_the_seat(self):
        """S1's promise at the launch: the reader answered once, at launch, and was locked before the seat asked."""
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        res = self.seat()
        self.assertTrue(res["locked_before"], "the Keychain was locked before the seat started")
        self.assertEqual(res["seat"], CANARY_SHA, res)
        with open(self.calls) as fh:
            self.assertEqual(fh.read().split(), ["get"], "the login was read once, by the holder")

    def test_a_process_outside_the_spawn_chain_cannot_get_it(self):
        """A same user process the holder did not start, told the descriptor number the seat was told, asks the
        holder and asks through a seat's path: both refused, and the seat in the chain was served."""
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        res = self.seat()
        self.assertEqual(res["seat"], CANARY_SHA, res)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env[BL.HOLD_FD_VAR] = res["hold_fd_var"]
        p = subprocess.run([sys.executable, "-B", self.sibling], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("ask refused Failure", p.stdout)
        self.assertIn("seat refused Failure", p.stdout)
        self.assertNoCanaryOnDisk(p.stdout, out)

    def test_a_reader_that_fails_at_launch_refuses_started(self):
        """Partially done: the holder cannot read the login at launch, so it starts nothing and the launch refuses."""
        _write(self.locked, "")
        rc, out = self.go()
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [holder]", out)
        self.assertIn("Brother cannot read its login", out)
        self.assertNotIn("STARTED", out)
        self.assertFalse(os.path.exists(self.seat_json), "no driver ran")

    def test_the_cli_lane_is_unchanged(self):
        """BROTHER_LOOP_TOKEN unset: no holder runs, the driver is started as before, and nothing names a holder."""
        rc, out = self.go(lane=None)
        self.assertEqual(rc, 0, out)
        self.assertIn("STARTED", out)
        self.assertIn("holder not started", out)
        self.assertFalse(os.path.exists(self.marker), "the holder never ran")
        res = self.seat()
        self.assertEqual(res["hold_fd_var"], "")
        self.assertEqual(self._pidfiles(), [])

    def test_stop_ends_the_holder_with_the_run(self):
        """stop_loop.sh, limited to this test's folder, ends the holder by its pid file."""
        why = TSG.process_table_unreadable()
        if why:
            self.skipTest("NO-DATA: %s, so stop_loop.sh reads no process and signals nothing" % why)
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        pids = self._pidfiles()
        self.assertEqual(len(pids), 1, out)
        p = subprocess.run(["bash", STOP, "--reason", "test"],
                           env=dict(self._env(), STOP_LOOP_ONLY=self.tmp, STOP_DRIVER_GRACE="2"),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        self.assertIn("login holder %d" % pids[0], p.stdout)
        deadline = time.time() + 5
        while _alive(pids[0]) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(pids[0]), p.stdout)

    def stop(self, *args):
        return subprocess.run(["bash", STOP] + list(args),
                              env=dict(self._env(), STOP_LOOP_ONLY=self.tmp, STOP_DRIVER_GRACE="2"),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=90)

    def need_ps(self):
        why = TSG.process_table_unreadable()
        if why:
            self.skipTest("NO-DATA: %s, so stop_loop.sh reads no process and signals nothing" % why)

    def test_runners_only_leaves_the_holder(self):
        """B (round 1): stop_loop.sh --runners-only stops runners and leaves the run alive, so the run's holder stays
        up: reaping it there brought back the 1.1.5 lock failure for every seat the live driver starts after."""
        self.need_ps()
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        pids = self._pidfiles()
        self.assertEqual(len(pids), 1, out)
        p = self.stop("--runners-only")
        time.sleep(0.5)
        self.assertNotIn("login holder", p.stdout)
        self.assertTrue(_alive(pids[0]), p.stdout)

    def test_the_driver_leads_its_own_group(self):
        """C (round 1): on the token lane the driver leads its own process group, as it does off it, so stop_loop.sh
        signals the driver's whole group (its foreground children included), never the driver pid alone."""
        self.need_ps()
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        starts = os.path.join(self.evd, "loop-starts")
        rec, = [n for n in os.listdir(starts) if n.startswith("launch-") and n.endswith(".json")]
        with open(os.path.join(starts, rec)) as fh:
            dpid = json.load(fh)["driver_pid"]
        pg = subprocess.run(["ps", "-o", "pgid=", "-p", str(dpid)], stdout=subprocess.PIPE,
                            universal_newlines=True).stdout.strip()
        self.assertEqual(pg, str(dpid), "the driver leads its group")

    def test_a_full_stop_ends_the_drivers_foreground_child(self):
        """C (round 1), at stop_loop.sh's entry: a driver busy in a foreground native_worker.py refresh (the shape of
        loop_until.sh's refresh step) ends by its own TERM trap once a full stop signals its group, the refresh child
        is gone, and the STOPPED line is true. Red at 8a5223ceb: the driver alone was TERMed, its trap waited on the
        child, the grace ran out, the driver was KILLed and the refresh child outlived a STOPPED line."""
        self.need_ps()
        bin_dir = os.path.join(self.home, ".claude", "bin")
        os.makedirs(bin_dir)
        driver = _write(os.path.join(bin_dir, "loop_until.sh"), TREE_DRIVER)
        _write(os.path.join(bin_dir, "native_worker.py"), REFRESH_STUB)
        rc, out = self.launch(TEST_SEATS="in", BROTHER_LOOP_TOKEN="on", START_RUN_HOLDER=self.wrapper,
                              START_RUN_DRIVER=driver)
        self.assertEqual(rc, 0, out)
        refresh = os.path.join(self.evd, "refresh.pid")
        deadline = time.time() + 30
        while not os.path.exists(refresh) and time.time() < deadline:
            time.sleep(0.1)
        with open(refresh) as fh:
            child = int(fh.read().strip())
        p = self.stop("--reason", "test")
        deadline = time.time() + 5
        while _alive(child) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(child), p.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.evd, "driver-trap")),
                        "the driver ended by its own trap: " + p.stdout)
        self.assertIn("STOPPED: nothing of the loop is alive", p.stdout)


    def test_a_holder_refusal_after_the_driver_started_stops_the_driver(self):
        """The chain reaction of C, closed: the driver now leads its own group, so when the launch refuses [holder]
        after LOOP UNTIL (here the driver made no run folder) stopping the holder's group no longer reaches it; the
        launcher stops the driver's group too, and no driver outlives a refused launch."""
        self.need_ps()
        driver = _write(os.path.join(self.tmp, "folderless_driver.sh"),
                        '#!/bin/bash\necho $$ > "$TEST_EVD/driver.pid"\necho "LOOP UNTIL $1 | gap $2s | log x"\nsleep 30\n')
        rc, out = self.launch(TEST_SEATS="in", BROTHER_LOOP_TOKEN="on", START_RUN_HOLDER=self.wrapper,
                              START_RUN_DRIVER=driver)
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [holder]", out)
        with open(os.path.join(self.evd, "driver.pid")) as fh:
            dpid = int(fh.read().strip())
        self.addCleanup(lambda: _alive(dpid) and os.kill(dpid, signal.SIGKILL))
        deadline = time.time() + 5
        while _alive(dpid) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(dpid), out)


#: A driver at work in a foreground child: its run folder, its first line, a TERM trap (loop_until.sh has one), then a
#: refresh stand in started the way loop_until.sh starts native_worker.py refresh. Lives in the fixture's loop bin.
TREE_DRIVER = """#!/bin/bash
RUN="$BROTHER_RUNS_ROOT/run-20990101-000000-$$"
mkdir -p "$RUN"
trap 'echo trapped > "$TEST_EVD/driver-trap"; exit 0' TERM
echo "LOOP UNTIL $1 | gap $2s | log $TEST_EVD/loop.log"
python3 -B "$HOME/.claude/bin/native_worker.py" refresh
sleep 30
"""
REFRESH_STUB = """import os, time
with open(os.path.join(os.environ["TEST_EVD"], "refresh.pid"), "w") as fh:
    fh.write(str(os.getpid()))
time.sleep(60)
"""

#: A command the real holder starts: one seat (served), then it waits for the test to end its holder, then a second
#: seat. Its own fallback reader is locked from the start. argv: out ready go
DEATH_CHILD = r'''import hashlib, json, os, sys, time
sys.path.insert(0, %(here)r)
import brother_login as BL
import native_worker as NW
BL.READER = %(dead)r
os.environ["BROTHER_LOOP_TOKEN"] = "on"
out, ready, go = sys.argv[1:4]
def seat():
    try:
        env, fd = NW.login_child()
    except BL.Failure as exc:
        return "FAILURE " + str(exc)
    try:
        return hashlib.sha256(os.read(fd, 65536)).hexdigest()
    finally:
        os.close(fd)
res = [seat()]
open(ready, "w").close()
deadline = time.time() + 60
while not os.path.exists(go) and time.time() < deadline:
    time.sleep(0.05)
res.append(seat())
with open(out, "w") as fh:
    json.dump(res, fh)
'''


class HolderDeath(WiredFixture):
    """B (round 1), the reviewer's probe at the real holder: it is TERMed while its command runs."""

    def die(self):
        run = os.path.join(self.tmp, "hold-run")
        os.makedirs(run)
        child = _write(os.path.join(self.tmp, "death_child.py"), DEATH_CHILD % {"here": HERE, "dead": self.dead})
        out, ready, go = (os.path.join(self.tmp, n) for n in ("death.json", "ready", "go"))
        log = open(os.path.join(self.tmp, "holder.log"), "w+")
        self.addCleanup(log.close)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        h = subprocess.Popen([sys.executable, "-B", self.wrapper, "hold", "--run", run, "--", sys.executable, "-B",
                              child, out, ready, go], stdout=log, stderr=subprocess.STDOUT, env=env)
        self.addCleanup(lambda: h.poll() is None and h.kill())
        deadline = time.time() + 30
        while not os.path.exists(ready) and time.time() < deadline and h.poll() is None:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(ready), "the command's first seat ran")
        h.send_signal(signal.SIGTERM)
        h.wait(20)
        open(go, "w").close()
        deadline = time.time() + 30
        while not os.path.exists(out) and time.time() < deadline:
            time.sleep(0.05)
        with open(out) as fh:
            res = json.load(fh)
        log.seek(0)
        return res, log.read()

    def test_a_holder_that_dies_mid_run_refuses_loudly(self):
        """Served, then the holder is TERMed: the next seat refuses naming the run's login holder, never a quiet fresh
        read (red at 8a5223ceb: 'FAILURE Brother cannot read its login...', the Keychain named, the holder not)."""
        res, _log = self.die()
        self.assertEqual(res[0], CANARY_SHA, res)
        self.assertTrue(res[1].startswith("FAILURE The run's login holder has ended"), res[1])
        self.assertNotIn("Brother cannot read its login", res[1])

    def test_a_stopped_holder_says_what_ended(self):
        """A TERMed holder says the run's login is no longer held, in the log it shares with the driver; never the
        sign in's 'Cancelled. No successful change confirmed.'"""
        _res, log = self.die()
        self.assertIn("brother-login hold: stopped", log)
        self.assertNotIn("Cancelled.", log)
        self.assertNoCanaryOnDisk(log)


class TakeHold(unittest.TestCase):
    """brother_login.take_hold, the one rule every Python hop applies: one condition per case."""

    def test_the_hop_rule(self):
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        r, w = os.pipe()
        os.close(r)
        os.close(w)
        plain = open(os.devnull)
        self.addCleanup(plain.close)
        n = str(theirs.fileno())
        for raw, want in (("", ({}, ())), (n, ({BL.HOLD_FD_VAR: n}, (theirs.fileno(),))),
                          (str(r), ({BL.HOLD_FD_VAR: str(r)}, ())), (str(plain.fileno()), ({BL.HOLD_FD_VAR: str(plain.fileno())}, ())),
                          ("abc", ({BL.HOLD_FD_VAR: "abc"}, ())), ("9999999", ({BL.HOLD_FD_VAR: "9999999"}, ()))):
            with self.subTest(raw=raw):
                env = {BL.HOLD_FD_VAR: raw, "KEEP": "1"} if raw else {"KEEP": "1"}
                self.assertEqual(BL.take_hold(env), want)
                self.assertEqual(env, {"KEEP": "1"}, "the name is taken from this process")

    def own(self):
        """A served pair named in this process's own environment; the environment and the process hold restored."""
        saved = os.environ.get(BL.HOLD_FD_VAR)
        self.addCleanup(lambda: os.environ.pop(BL.HOLD_FD_VAR, None) if saved is None
                        else os.environ.__setitem__(BL.HOLD_FD_VAR, saved))
        self.addCleanup(lambda: setattr(BL, "_HELD", "") if hasattr(BL, "_HELD") else None)
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        os.environ[BL.HOLD_FD_VAR] = str(theirs.fileno())
        return theirs

    def test_the_holding_process_keeps_serving_its_own_roles(self):
        """A (round 1): after the hop the process's own in process roles (build plan, repair advisor, through
        native_worker.login_child) still ask the holder, while its environment names it to no child. Red at
        8a5223ceb: the name was popped and the role read the Keychain fresh."""
        self.own()
        BL.take_hold()
        self.assertNotIn(BL.HOLD_FD_VAR, os.environ, "no child this process starts is told")
        self.assertEqual(BL.ask_holder(), CANARY)

    def test_the_held_descriptor_is_closed_to_every_other_child(self):
        """The descriptor stays open here for the process's own roles, closed on exec: only a pass_fds child gets it."""
        theirs = self.own()
        os.set_inheritable(theirs.fileno(), True)   # how a pass_fds spawn leaves it in the child
        BL.take_hold()
        self.assertFalse(os.get_inheritable(theirs.fileno()))

    def test_a_given_mapping_never_sets_the_process_hold(self):
        """take_hold over a mapping that is not this process's environment keeps nothing for this process."""
        theirs = self.own()
        os.environ.pop(BL.HOLD_FD_VAR)
        self.assertEqual(BL.take_hold({BL.HOLD_FD_VAR: str(theirs.fileno())})[1], (theirs.fileno(),))
        self.assertEqual(BL.ask_holder(), "")

    def test_a_broken_hop_is_loud_for_the_process_own_roles_too(self):
        """S2 round 2 (survivor X12): the name names a descriptor that is not open here (a hop that lost it). The
        process's own roles then refuse naming the holder, never "" and a quiet fresh Keychain read."""
        theirs = self.own()
        n = os.dup(theirs.fileno())
        os.close(n)   # a number nothing holds open now
        os.environ[BL.HOLD_FD_VAR] = str(n)
        self.assertEqual(BL.take_hold(), ({BL.HOLD_FD_VAR: str(n)}, ()))
        with self.assertRaises(BL.Failure) as cm:
            BL.ask_holder()
        self.assertIn("login holder", str(cm.exception))


class PoolHop(unittest.TestCase):
    """runner_pool.main, the real spawn site of a unit runner, through the LA5.a loader (no process started)."""

    def test_the_pool_hands_the_holder_to_the_unit_runner_only(self):
        import test_runner_pool_seats as TRPS
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        pool = TRPS._load()
        self.addCleanup(TRPS.tearDownModule)
        self.addCleanup(lambda: setattr(BL, "_HELD", "") if hasattr(BL, "_HELD") else None)   # main's hop keeps it
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        spawns, runs = [], []

        class Proc(object):
            pid = 4242

            def poll(self):
                return None

        class Sub(TRPS._FakeSubprocess):
            STDOUT = subprocess.STDOUT

            def run(self, argv, **kw):
                runs.append(dict(kw.get("env") or os.environ))
                return TRPS._FakeSubprocess.run(self, argv, **kw)

            def Popen(self, argv, **kw):
                spawns.append((argv, kw))
                getattr(kw.get("stdout"), "close", lambda: None)()
                return Proc()

        pool.subprocess = Sub()
        pool.spawned = lambda proc, sub: ""
        os.environ[BL.HOLD_FD_VAR] = str(theirs.fileno())
        argv = sys.argv
        try:
            sys.argv = ["runner_pool.py"]
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                pool.main()
        finally:
            sys.argv = argv
        self.assertTrue(spawns, "the pool started a unit runner")
        for argv, kw in spawns:
            self.assertIn("unit_runner.py", " ".join(argv))
            self.assertEqual(tuple(kw.get("pass_fds") or ()), (theirs.fileno(),), kw)
            self.assertEqual((kw.get("env") or {}).get(BL.HOLD_FD_VAR), str(theirs.fileno()), "named to that child")
        self.assertNotIn(BL.HOLD_FD_VAR, os.environ, "every other child of the pool is never told")
        self.assertTrue(runs and all(BL.HOLD_FD_VAR not in e for e in runs), "the pool's other children")


class RunnerHop(unittest.TestCase):
    """The real unit_runner.py script spawning native_worker (a recording stub that asks the real holder)."""

    def test_the_unit_runner_hands_the_holder_to_native_worker(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        stub = TUN.NATIVE_STUB.replace(
            'if __name__ == "__main__":\n    a = sys.argv[1:]\n',
            'if __name__ == "__main__":\n    a = sys.argv[1:]\n'
            '    sys.path.append(os.environ["TEST_REAL_LOOP"]); import brother_login as BL\n'
            '    try: got = BL.ask_holder()\n'
            '    except BL.Failure as exc: got = "FAILURE " + str(exc)\n'
            '    import hashlib\n'
            '    with open(os.environ["FAKE_FANOUT_RECORD"], "a") as fh: fh.write("asked " + (hashlib.sha256(got.encode()).hexdigest() if got.startswith("sk-") else got) + "\\n")\n', 1)
        self.assertIn("TEST_REAL_LOOP", stub, "the stub hook found its place")
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        with open(os.path.join(HERE, "brother_login.py")) as fh:
            real_login = fh.read()   # deployed beside the runner in every bin; the fixture bin copies a subset
        r = TUN.Run(dict(TUN.ON, **{BL.HOLD_FD_VAR: str(theirs.fileno()), "TEST_REAL_LOOP": HERE}),
                    stubs={"native_worker.py": stub, "brother_login.py": real_login}, pass_fds=(theirs.fileno(),))
        self.addCleanup(r.cleanup)
        asked = [l for l in r.record if l.startswith("asked ")]
        self.assertEqual(asked, ["asked " + CANARY_SHA], r.out)

    def test_an_in_process_role_of_the_unit_runner_is_served(self):
        """A (round 1), at the real unit_runner.py: its build plan runs in process (BP.plan, and on the token lane
        model_call._TokenLane, NW.login_child, ask_holder) after the runner's hop took the name from its environment.
        The stand in build plan asks through the real brother_login: served, and its environment still names no holder.
        Red at 8a5223ceb: an empty answer, so the role read the Keychain fresh and, locked, planned nothing."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        with open(os.path.join(HERE, "brother_login.py")) as fh:
            real_login = fh.read()
        r = TUN.Run(dict(TUN.ON, **{BL.HOLD_FD_VAR: str(theirs.fileno()), "BROTHER_BUILD_PLAN_MODEL": "opus55"}),
                    stubs={"build_plan.py": PLAN_STUB, "brother_login.py": real_login}, pass_fds=(theirs.fileno(),))
        self.addCleanup(r.cleanup)
        asked = [l for l in r.record if l.startswith("plan asked ")]
        self.assertEqual(asked, ["plan asked %s named False" % CANARY_SHA], r.out)

    # S2 ROUND 2: the unit runner's other Claude role hosts, each started as a child, one at a time
    def served(self):
        """A served pair and the real brother_login source, for a TUN.Run whose children ask it."""
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        with open(os.path.join(HERE, "brother_login.py")) as fh:
            real_login = fh.read()
        dead = tempfile.mkdtemp(prefix="dead-reader-")
        self.addCleanup(shutil.rmtree, dead, True)
        dead = _write(os.path.join(dead, "dead.sh"), "#!/bin/sh\nexit 9\n", 0o700)
        return theirs.fileno(), real_login, {BL.HOLD_FD_VAR: str(theirs.fileno()), "TEST_REAL_LOOP": HERE,
                                            "TEST_DEAD_READER": dead}

    def blind(self, env):
        """The real unit_runner.py on a blind fan out whose fake asks the holder first, as a Claude arm's login_child."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        import test_runner_straggler_settles as TRS
        fd, real_login, extra = self.served()
        from unittest import mock
        with mock.patch.object(TRS, "FAKE_FANOUT", FANOUT_ASKS + TRS.FAKE_FANOUT):
            r = TUN.Run(dict(env, **extra), stubs={"brother_login.py": real_login}, pass_fds=(fd,))
        self.addCleanup(r.cleanup)
        return [l for l in r.record if l.startswith("fanout asked ")], r

    def test_a_blind_round_with_a_claude_arm_is_served_by_the_holder(self):
        """S2 round 2 (claim 3 part b): native off, the arm on the claude transport, so the blind fan out makes the
        round's Claude calls (model_call.call_one, _TokenLane, login_child). Served by the holder on a locked Keychain.
        Red at d98550631: the fan out was given neither, so it read the Keychain fresh and failed (probe4)."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        asked, r = self.blind(TUN.OFF)
        self.assertEqual(asked, ["fanout asked %s named True" % CANARY_SHA], r.out)

    def test_a_round_with_no_claude_arm_never_hands_the_holder(self):
        """Its own condition: every arm on the bridge, so the fan out makes no Claude call and is told nothing."""
        asked, r = self.blind({"BROTHER_CLAUDE_NATIVE": "on"})   # no pin: deepseek, a bridge model
        self.assertEqual(len(asked), 1, r.out)
        self.assertTrue(asked[0].endswith("named False"), asked)

    def test_the_probe_stage_is_handed_the_holder(self):
        """S2 round 2 (item 1): a grader PASS starts probe_wave, whose adversaries may be Claude models
        (BROTHER_ADVERSARY_MODEL): the probe stage is given the holder. Red at d98550631: named nowhere."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        fd, real_login, extra = self.served()
        r = TUN.Run(dict(TUN.OFF, BROTHER_PROBES="on", **extra),
                    stubs={"brother_login.py": real_login, "grade_lane.sh": GRADE_PASSES, "probe_wave.py": PROBE_WAVE_ASKS},
                    pass_fds=(fd,))
        self.addCleanup(r.cleanup)
        asked = [l for l in r.record if l.startswith("probes asked ")]
        self.assertEqual(asked, ["probes asked %s" % CANARY_SHA], r.out)

    def test_a_role_refused_its_login_is_said_and_the_owner_told(self):
        """S2 round 2 (claim 3 part c): the build plan comes back None with a login refusal (build_plan.LAST_REFUSAL).
        The run log says so by name and one notify row is written for the run. Red at d98550631: PLAN NO-DATA only."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        run_dir = tempfile.mkdtemp(prefix="role-login-run-")
        self.addCleanup(shutil.rmtree, run_dir, True)
        with open(os.path.join(LOOP_FILES, "loop_intake.py")) as fh:
            intake = fh.read()
        with open(os.path.join(LOOP_FILES, "proof_ledger.py")) as fh:
            ledger = fh.read()
        r = TUN.Run(dict(TUN.ON, BROTHER_BUILD_PLAN_MODEL="opus55", BROTHER_RUN_DIR=run_dir),
                    stubs={"build_plan.py": PLAN_REFUSED, "loop_intake.py": intake, "proof_ledger.py": ledger})
        self.addCleanup(r.cleanup)
        self.assertIn("LOGIN   the planner was refused its login: " + ROLE_REFUSAL, r.out)
        with open(os.path.join(run_dir, "notify-request.jsonl")) as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual([x["key"] for x in rows], ["role-login:planner"], r.out)

    def test_a_repair_advisor_refused_its_login_is_said_and_the_owner_told(self):
        """The same at the repair advisor, after a round the grader refused: said by name, one row for the run."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        run_dir = tempfile.mkdtemp(prefix="role-login-run-")
        self.addCleanup(shutil.rmtree, run_dir, True)
        with open(os.path.join(LOOP_FILES, "loop_intake.py")) as fh:
            intake = fh.read()
        with open(os.path.join(LOOP_FILES, "proof_ledger.py")) as fh:
            ledger = fh.read()
        r = TUN.Run(dict(TUN.ON, BROTHER_REPAIR_ADVISOR_MODEL="opus55", BROTHER_RUN_DIR=run_dir), rounds=2,
                    stubs={"repair_advisor.py": PLAN_REFUSED.replace("def plan(", "def advise("),
                           "loop_intake.py": intake, "proof_ledger.py": ledger})
        self.addCleanup(r.cleanup)
        self.assertIn("LOGIN   the repair_advisor was refused its login: " + ROLE_REFUSAL, r.out)
        with open(os.path.join(run_dir, "notify-request.jsonl")) as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual([x["key"] for x in rows], ["role-login:repair_advisor"], r.out)

    def test_a_finisher_prebrief_refused_its_login_is_said(self):
        """The repair advisor's last call, the finisher's prebrief when the sub unit parks EXHAUSTED with a build that
        passed the grader (probes dirty): said by name in the run log."""
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        r = TUN.Run(dict(TUN.OFF, BROTHER_PROBES="on", BROTHER_REPAIR_ADVISOR_MODEL="opus55"), rounds=1,
                    stubs={"repair_advisor.py": PLAN_REFUSED.replace("def plan(", "def advise("),
                           "grade_lane.sh": GRADE_PASSES,
                           "probe_wave.py": PROBE_WAVE_DIRTY})
        self.addCleanup(r.cleanup)
        self.assertIn("EXHAUSTED", r.status(), r.out)
        self.assertIn("LOGIN   the repair_advisor was refused its login: " + ROLE_REFUSAL, r.out)


LOOP_FILES = HERE
#: probe_wave as the unit runner starts it, finding dirt: the round's grader pass is refused at the probes.
PROBE_WAVE_DIRTY = r'''import os, sys
sub = sys.argv[3].strip("^$").replace("\\", "")
os.makedirs(os.path.join(sys.argv[2], "logs"), exist_ok=True)
with open(os.path.join(sys.argv[2], "logs", sub + ".done"), "w") as fh:
    fh.write("DIRTY\n")
'''
ROLE_REFUSAL = ("CONFIG_WAIT: BROTHER_LOOP_TOKEN is on but the loop login cannot be used (" + BL.HOLDER_ENDED +
                "); restart the run (start_run.sh) to read it again")
#: A build plan whose call was refused the loop login: None, with the refusal where the real one keeps it.
PLAN_REFUSED = ("LAST_REFUSAL = ''\ndef enabled(sub): return True\ndef plan(*a, **k):\n    global LAST_REFUSAL\n"
                "    LAST_REFUSAL = %r\n    return None\n" % ROLE_REFUSAL)
#: Prepended to the fake fan out: a Claude arm's login, read the way model_call._TokenLane reads it (native_worker.
#: login_child) with its own Keychain reader locked; records a digest only, and whether its environment names the holder.
FANOUT_ASKS = r'''
import hashlib as _h, os as _o, sys as _s
_named = "BROTHER_LOGIN_HOLD_FD" in _o.environ
_s.path.insert(0, _o.environ["TEST_REAL_LOOP"])
import brother_login as _BL, native_worker as _NW
_BL.READER = _o.environ["TEST_DEAD_READER"]
try:
    _env, _fd = _NW.login_child(); _got = _h.sha256(_o.read(_fd, 65536)).hexdigest(); _o.close(_fd)
except _BL.Failure as _exc:
    _got = "FAILURE " + str(_exc)
with open(_o.environ["FAKE_FANOUT_RECORD"], "a") as _fh:
    _fh.write("fanout asked %s named %s\n" % (_got, _named))
_s.path.remove(_o.environ["TEST_REAL_LOOP"])
'''
#: A grade that passes every build of the round: grade_lane.sh's verdict line, and a completed grade per build.
GRADE_PASSES = ('#!/bin/sh\nmkdir -p "$1/grades"\nfor b in "$1"/out/*.json; do [ -e "$b" ] || continue; '
                'n=$(basename "$b" .json); n=${n%-build}; printf "PASS stub\\nexit=0\\n" > "$1/grades/$n.txt"; '
                'echo "$2 PASS $n"; done\n')
#: probe_wave as the unit runner starts it: asks the holder as an adversary's login would, then writes a CLEAN verdict.
PROBE_WAVE_ASKS = r'''import hashlib, os, sys
sys.path.insert(0, os.environ["TEST_REAL_LOOP"])
import brother_login as BL, native_worker as NW
BL.READER = os.environ["TEST_DEAD_READER"]
try:
    env, fd = NW.login_child(); got = hashlib.sha256(os.read(fd, 65536)).hexdigest(); os.close(fd)
except BL.Failure as exc:
    got = "FAILURE " + str(exc)
with open(os.environ["FAKE_FANOUT_RECORD"], "a") as fh:
    fh.write("probes asked %s\n" % got)
sub = sys.argv[3].strip("^$").replace("\\", "")
os.makedirs(os.path.join(sys.argv[2], "logs"), exist_ok=True)
with open(os.path.join(sys.argv[2], "logs", sub + ".done"), "w") as fh:
    fh.write("CLEAN\n")
'''


#: The unit runner's build plan, in process: asks the run's holder the way NW.login_child does, records a digest only.
PLAN_STUB = r'''import hashlib, os
def enabled(sub): return True
def plan(*a, **k):
    import brother_login as BL
    try:
        got = BL.ask_holder()
        said = hashlib.sha256(got.encode()).hexdigest() if got.startswith("sk-") else repr(got)
    except BL.Failure as exc:
        said = "FAILURE " + str(exc)
    with open(os.environ["FAKE_FANOUT_RECORD"], "a") as fh:
        fh.write("plan asked %s named %s\n" % (said, BL.HOLD_FD_VAR in os.environ))
    return None
'''


class FinisherHop(unittest.TestCase):
    """The driver's finisher starts after the driver, and so after the holder, has ended: it is never told one."""

    def test_the_finisher_never_names_the_holder(self):
        with open(os.path.join(HERE, "loop_until.sh")) as fh:
            text = fh.read()
        job = [l for l in text.splitlines() if l.strip().startswith("FIN_JOB=")]
        self.assertEqual(len(job), 1, job)
        self.assertIn("unset %s" % BL.HOLD_FD_VAR, job[0])

    def test_the_finisher_refuses_loudly_without_a_login(self):
        """A (round 1): the finisher starts after its run's holder has ended, so on the token lane it reads the login
        fresh. A Keychain that cannot be read then refuses the whole finisher once, before any model is called, saying
        the holder ended with the run; it never runs on to answer every candidate NOT SAVED in its own log."""
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="fin-login-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        home, runs = os.path.join(tmp, "home"), os.path.join(tmp, "unit-runs")
        os.makedirs(os.path.join(home, ".claude", "evidence"))
        os.makedirs(runs)
        dead = _write(os.path.join(tmp, "dead.sh"), "#!/bin/sh\nexit 9\n", 0o700)
        script = _write(os.path.join(tmp, "fin.py"), FIN_WRAP % {"here": HERE, "dead": dead})
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=home, SALVAGE_RUNS=runs, BROTHER_LOOP_TOKEN="on")
        p = subprocess.run([sys.executable, "-B", script], env=env, cwd=tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        self.assertEqual(p.returncode, 4, p.stdout)
        self.assertIn("FINISH REFUSED", p.stdout)
        self.assertIn("login holder ended with the run", p.stdout)

    def test_the_finisher_job_raises_the_alarm(self):
        """The finisher job itself (loop_until.sh's FIN_JOB, run as the driver runs it): a finisher that refuses puts
        one line on the loop alarm, so the owner hears of it outside the finisher's own log."""
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="fin-job-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        _write(os.path.join(tmp, "finish_run.py"), "import sys\nprint('FINISH REFUSED: stub')\nsys.exit(4)\n")
        with open(os.path.join(HERE, "loop_until.sh")) as fh:
            line = [l for l in fh.read().splitlines() if l.strip().startswith("FIN_JOB=")][0]
        job = line.split("FIN_JOB='", 1)[1].rsplit("'", 1)[0]
        gone = subprocess.Popen(["true"])
        gone.wait()
        log, alarm = os.path.join(tmp, "fin.log"), os.path.join(tmp, "ALARM.txt")
        p = subprocess.run(["bash", "-c", job, "finisher", str(gone.pid), "opus55", tmp, tmp, log, alarm],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        said = ""
        if os.path.exists(alarm):
            with open(alarm) as fh:
                said = fh.read()
        self.assertIn("finisher: refused or failed (exit 4)", said, p.stdout)

    def test_a_finisher_that_succeeds_raises_no_alarm(self):
        """S2 round 2 (survivor X7): the alarm line belongs to a refusal only; a finisher that exits 0 adds nothing."""
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="fin-job-ok-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        _write(os.path.join(tmp, "finish_run.py"), "print('FINISH saved 1')\n")
        with open(os.path.join(HERE, "loop_until.sh")) as fh:
            line = [l for l in fh.read().splitlines() if l.strip().startswith("FIN_JOB=")][0]
        job = line.split("FIN_JOB='", 1)[1].rsplit("'", 1)[0]
        gone = subprocess.Popen(["true"])
        gone.wait()
        log, alarm = os.path.join(tmp, "fin.log"), os.path.join(tmp, "ALARM.txt")
        _write(alarm, "LOOP FINISHED\n")
        p = subprocess.run(["bash", "-c", job, "finisher", str(gone.pid), "opus55", tmp, tmp, log, alarm],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        with open(alarm) as fh:
            said = fh.read()
        with open(log) as fh:
            self.assertIn("FINISH saved 1", fh.read(), p.stdout)
        self.assertEqual(said, "LOOP FINISHED\n", p.stdout)


#: finish_run.main as the finisher job runs it, with the trusted reader pointed at a locked stand in.
FIN_WRAP = r'''import sys
sys.path.insert(0, %(here)r)
import brother_login as BL
BL.READER = %(dead)r
import finish_run
sys.argv = ["finish_run.py", "--model", "opus55"]
sys.exit(finish_run.main())
'''


def _own_pair(case):
    """A served pair named in this process's environment; the environment and brother_login's process hold restored."""
    saved = os.environ.get(BL.HOLD_FD_VAR)
    case.addCleanup(lambda: os.environ.pop(BL.HOLD_FD_VAR, None) if saved is None
                    else os.environ.__setitem__(BL.HOLD_FD_VAR, saved))
    case.addCleanup(lambda: setattr(BL, "_HELD", ""))
    mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    case.addCleanup(mine.close)
    case.addCleanup(theirs.close)
    stop = threading.Event()
    case.addCleanup(stop.set)
    threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
    os.environ[BL.HOLD_FD_VAR] = str(theirs.fileno())
    return theirs


#: A probe stage child that writes adversary answers (or_fanout) or runs them (probe_build): records whether its
#: environment names the holder and whether the descriptor is open in it, and for the fan out the login it is served.
CHILD_REPORTS = r'''import hashlib, json, os, sys
named = os.environ.get("BROTHER_LOGIN_HOLD_FD", "")
try:
    os.fstat(int(os.environ["TEST_HOLD_N"])); fd = True
except OSError:
    fd = False
said = "%s named %s fd %s" % (WHO, bool(named), fd)
if WHO == "fanout":
    sys.path.insert(0, os.environ["TEST_REAL_LOOP"])
    import brother_login as BL
    try:
        got = BL.ask_holder()
        said += " asked " + (hashlib.sha256(got.encode()).hexdigest() if got.startswith("sk-") else repr(got))
    except BL.Failure as exc:
        said += " asked FAILURE " + str(exc)
    for j in json.load(open(sys.argv[1])):
        open(j["out"], "w").write("{}")
else:
    print("PROBES 1 run: 0 CRASH, 0 WRONG")
with open(os.environ["TEST_RECORD"], "a") as fh:
    fh.write(said + "\n")
'''


def _code_tree(root, fanout):
    core = os.path.join(root, "plugin", "runtime", "brother", "core")
    os.makedirs(core)
    for d in ("plugin", "plugin/runtime", "plugin/runtime/brother", "plugin/runtime/brother/core"):
        open(os.path.join(root, d, "__init__.py"), "w").close()
    _write(os.path.join(core, "or_fanout.py"), fanout)


class ProbeHops(unittest.TestCase):
    """S2 round 2 (item 1): the probe stage's Claude adversaries (BROTHER_ADVERSARY_MODEL) are served by the holder,
    and the model written probes they hand back never see it. probe_wave.py runs as a process; probe_round.main in
    process through its own entry fixture."""

    def test_probe_wave_hands_the_holder_to_its_fan_out_only(self):
        box = os.path.realpath(tempfile.mkdtemp(prefix="probe-wave-hold-"))
        self.addCleanup(shutil.rmtree, box, True)
        bind, tree, wave, code, home = (os.path.join(box, n) for n in ("bin", "tree", "wave", "code", "home"))
        for d in (bind, os.path.join(tree, "docs", "plan"), os.path.join(wave, "grades"), os.path.join(wave, "out"),
                  os.path.join(home, ".claude", "evidence")):
            os.makedirs(d)
        for name in ("probe_wave.py", "loop_hold.py", "loop_switches.py", "wave_wait.py", "plan_path.py", "loop_roles.py",
                     "brother_login.py"):
            shutil.copy(os.path.join(HERE, name), bind)
        stubs = {"model_router.py": "import os\nclass Refused(Exception): pass\ndef code_root(): return os.environ['TEST_CODE']\n",
                 "unit_ledger.py": "def grade_passed(path): return True\n",
                 "probe_brief.py": "import sys\nopen(sys.argv[4], 'w').write('probe brief')\n",
                 "grade_build.py": "def private_hits(text): return []\n",
                 "probe_build.py": "WHO = 'probe_build'\n" + CHILD_REPORTS}
        for name, body in stubs.items():
            _write(os.path.join(bind, name), body)
        _code_tree(code, "WHO = 'fanout'\n" + CHILD_REPORTS)
        _write(os.path.join(tree, "spec.md"), "# Z9\n")
        _write(os.path.join(tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"),
               json.dumps({"units": [{"id": "Z9", "spec": "spec.md", "sub_units": ["Z9.1"]}]}))
        _write(os.path.join(wave, "grades", "Z9.1-r0.txt"), "PASS\nexit=0\n")
        _write(os.path.join(wave, "out", "Z9.1-r0-build.json"), "{}")
        _write(os.path.join(home, ".brothersbe-private-names"), "")
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        record = os.path.join(box, "record")
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=home, BROTHER_PROBES="on", TEST_CODE=code, TEST_REAL_LOOP=HERE, TEST_RECORD=record,
                   TEST_HOLD_N=str(theirs.fileno()), PROBE_TIMEOUT="20", PROBE_ENOUGH="1",
                   BROTHER_PROBE_CACHE=os.path.join(box, "cache"), PYTHONDONTWRITEBYTECODE="1",
                   **{BL.HOLD_FD_VAR: str(theirs.fileno())})
        p = subprocess.run([sys.executable, "-B", os.path.join(bind, "probe_wave.py"), wave, os.path.join(box, "pw"),
                            "^Z9\\.1$"], cwd=tree, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=120, pass_fds=(theirs.fileno(),))
        with open(record) as fh:
            said = sorted(fh.read().splitlines())
        self.assertEqual(said, ["fanout named True fd True asked %s" % CANARY_SHA] + ["probe_build named False fd False"] * 2,
                         p.stdout)

    def test_probe_round_hands_the_holder_to_its_fan_out_only(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import test_probe_round as TPR
        P = TPR.P

        class Entry(TPR.ReprobeAtItsEntryPoint):
            def runTest(self):
                pass
        e = Entry()
        e.setUp()
        self.addCleanup(e.doCleanups)
        record = os.path.join(e.tmp, "record")
        _write(os.path.join(e.tmp, "code", "plugin", "runtime", "brother", "core", "or_fanout.py"), "WHO = 'fanout'\n" + CHILD_REPORTS)
        _write(os.path.join(e.tmp, "bin", "probe_build.py"), "WHO = 'probe_build'\n" + CHILD_REPORTS)
        theirs = _own_pair(self)
        from unittest import mock
        with mock.patch.dict(os.environ, {"TEST_RECORD": record, "TEST_REAL_LOOP": HERE, "TEST_HOLD_N": str(theirs.fileno())}):
            e.main(0)
        with open(record) as fh:
            said = sorted(fh.read().splitlines())
        self.assertEqual(said, ["fanout named True fd True asked %s" % CANARY_SHA] + ["probe_build named False fd False"] * 2)


class FanoutHop(unittest.TestCase):
    """or_fanout.main, the fan out every Claude role job of a round or a probe stage passes through (in process)."""

    def test_the_fan_out_keeps_the_hold_for_its_own_roles_and_tells_no_child(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
        from plugin.runtime.brother.core import or_fanout
        theirs = _own_pair(self)
        os.set_inheritable(theirs.fileno(), True)   # as a pass_fds spawn leaves it
        tmp = tempfile.mkdtemp(prefix="fanout-hold-")
        self.addCleanup(shutil.rmtree, tmp, True)
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            rc = or_fanout.main([os.path.join(tmp, "no-such-jobs.json")])
        self.assertEqual(rc, 2)
        self.assertNotIn(BL.HOLD_FD_VAR, os.environ, "no child of the fan out is told")
        self.assertFalse(os.get_inheritable(theirs.fileno()), "no child of the fan out inherits it")
        self.assertEqual(BL.ask_holder(), CANARY, "the fan out's own Claude role calls are served")


#: A program that records, for every start, whether descriptor 47 is open in it and whether the holder is named, then
#: runs the real program it stands in for. %(real)s is that program's path; "name" is how it was called.
FD_SHIM = '''#!/bin/sh
if [ -e /dev/fd/47 ]; then _f=yes; else _f=no; fi
printf '%%s fd=%%s name=%%s mark=%%s %%s\\n' "%(name)s" "$_f" "${BROTHER_LOGIN_HOLD_FD-unset}" "${BROTHER_LOGIN_HELD_RUN-unset}" "$(printf '%%s' "$*" | tr '\\n' ' ' | cut -c1-900)" >> "$HOME/calls/fds"
exec %(real)s "$@"
'''
FD_LINE = ('if [ -e /dev/fd/47 ]; then _f=yes; else _f=no; fi; printf \'%%s fd=%%s name=%%s mark=%%s\\n\' "%s" "$_f" '
           '"${BROTHER_LOGIN_HOLD_FD-unset}" "${BROTHER_LOGIN_HELD_RUN-unset}" >> "$HOME/calls/fds"\n')


def _hold_47(case):
    """The served pair's end at descriptor 47 in this process, for a child started with pass_fds=(47,)."""
    try:
        os.fstat(47)
        case.skipTest("NO-DATA: descriptor 47 is already in use in this test process")
    except OSError:
        pass   # sbe: allow-silent 47 is free, the state this case needs
    mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    case.addCleanup(mine.close)
    case.addCleanup(theirs.close)
    os.dup2(theirs.fileno(), 47)
    case.addCleanup(os.close, 47)


def _fd_lines(home):
    with open(os.path.join(home, "calls", "fds")) as fh:
        return [l for l in fh.read().splitlines() if l.strip()]


class DriverChildren(unittest.TestCase):
    """S2 round 2 (claim 8): loop_until.sh, driven for real through its lifecycle harness, hands the holder's
    descriptor and name to the pass and the login refresh, and to no other child: every python3, perl and git it
    starts, and every bash tool, is recorded. Red at d98550631: the worktree sentry (landing tree code) held it."""

    def test_only_the_pass_and_the_refresh_get_the_holder(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import test_loop_until_lifecycle as LU
        _hold_47(self)
        home = LU.make_home(lanes="1", pass_body="exit 42")
        self.addCleanup(shutil.rmtree, home, True)
        shim, bind = os.path.join(home, "shim"), os.path.join(home, ".claude", "bin")
        wt = os.path.join(home, "Brother", ".claude", "worktrees", "brother-unify-1.1")
        core = os.path.join(wt, "plugin", "runtime", "brother", "core")
        os.makedirs(core)   # the landing tree's own ledger, which the driver imports from it (the claim 8 import)
        for d in ("plugin", "plugin/runtime", "plugin/runtime/brother", "plugin/runtime/brother/core"):
            open(os.path.join(wt, d, "__init__.py"), "w").close()
        shutil.copy(os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "runtime", "brother", "core",
                                 "openrouter_ledger.py"), core)
        os.makedirs(os.path.join(wt, "scripts", "loop"))
        shutil.copy(os.path.join(HERE, "proof_ledger.py"), os.path.join(wt, "scripts", "loop"))
        for name in ("python3", "perl", "git"):
            real = shutil.which(name) if name != "python3" else sys.executable
            _write(os.path.join(shim, name), FD_SHIM % {"name": name, "real": real}, 0o755)
        # round 3 (finding 3): the harness's osascript stand in records too, so a driver that stops wrapping osascript
        # (whose `do shell script` children would hold the descriptor) turns this case red
        os.rename(os.path.join(shim, "osascript"), os.path.join(shim, "osascript-harness"))
        _write(os.path.join(shim, "osascript"), FD_SHIM % {"name": "osascript",
                                                           "real": os.path.join(shim, "osascript-harness")}, 0o755)
        for name in ("loop_guard.sh", "loop_pass.sh", "stop_loop.sh"):
            path = os.path.join(bind, name)
            with open(path) as fh:
                body = fh.read()
            _write(path, body.replace("#!/bin/bash\n", "#!/bin/bash\n" + FD_LINE % name, 1), 0o755)
        base = dict(LU.ROOMY, **{k: v for k, v in os.environ.items() if k not in LU.ESTATE and not k.startswith("BROTHER_")})
        env = dict(base, HOME=home, PATH=shim + os.pathsep + os.environ.get("PATH", ""),
                   BROTHER_LAUNCH_WORKTREE=wt, **{BL.HOLD_FD_VAR: "47", HELD_RUN: "1"})
        p = subprocess.run(["bash", LU.SCRIPT, LU.future_hhmm(), "1"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=120, pass_fds=(47,))
        lines = _fd_lines(home)
        given = [l for l in lines if "native_worker.py refresh" in l or "loop_pass.sh" in l]
        others = [l for l in lines if l not in given]
        self.assertTrue(any(l.startswith("loop_pass.sh fd=yes name=47 mark=1") for l in given), p.stdout + "\n".join(lines))
        self.assertTrue(any(l.startswith("python3 fd=yes name=47") and "native_worker.py refresh" in l for l in given),
                        p.stdout + "\n".join(lines))
        for l in given:
            self.assertIn(" fd=yes name=47", l)
        self.assertTrue(any("worktree_sentry.py claim" in l for l in others), "\n".join(lines))
        self.assertTrue(any(l.startswith("osascript ") for l in others), "\n".join(lines))
        self.assertGreater(len(others), 10, "\n".join(lines))
        for l in others:
            self.assertIn(" fd=no name=unset", l, l)


class PassChildren(unittest.TestCase):
    """S2 round 2 (claim 8, the same source one hop on): loop_pass.sh, driven for real through its code root harness,
    hands the holder to runner_pool.py alone; its sentry, sizing, diagnosis and lander children never get it."""

    def test_only_the_pool_gets_the_holder(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import test_loop_pass_code_root as TPC

        class Fixture(TPC.PassCodeRoot):
            def runTest(self):
                pass
        f = Fixture()
        f.setUp()
        self.addCleanup(f.tearDown)
        _hold_47(self)
        shim = os.path.join(f.box, "shim")
        os.makedirs(shim)
        _write(os.path.join(shim, "python3"), FD_SHIM % {"name": "python3", "real": sys.executable}, 0o755)
        os.makedirs(os.path.join(f.home, "calls"), exist_ok=True)
        base = {k: v for k, v in os.environ.items()
                if not k.startswith("BROTHER_") and k not in ("LOCAL_SLOTS", "WORKERS_PER_ROUND")}
        base.update(HOME=f.home, BROTHER_LAUNCH_WORKTREE=f.tree, BROTHER_SCOPE=".", BROTHER_CODE_ROOT=f.code,
                    BROTHER_TUNING="on", PATH=shim + os.pathsep + os.environ.get("PATH", ""), **{BL.HOLD_FD_VAR: "47", HELD_RUN: "1"})
        p = subprocess.run(["bash", TPC.PASS], cwd=f.tree, env=base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=120, pass_fds=(47,))
        time.sleep(1.0)
        lines = _fd_lines(f.home)
        pool = [l for l in lines if "runner_pool.py" in l]
        others = [l for l in lines if l not in pool]
        self.assertEqual(len(pool), 1, p.stdout + "\n".join(lines))
        self.assertIn(" fd=yes name=47", pool[0])
        self.assertTrue(any("worktree_sentry.py snapshot" in l for l in others), "\n".join(lines))
        # round 3 (finding 1): the held run's mark reaches the diagnosis, which then refuses a Claude model by name
        self.assertTrue([l for l in others if "diag_round.py" in l and " mark=1 " in l], "\n".join(lines))
        for l in others:
            self.assertIn(" fd=no name=unset", l, l)


class StopCounts(unittest.TestCase):
    """C (round 1): what a STOPPED line counts. loop_procs.owned over a written snapshot, no process table read."""

    def test_a_refresh_and_a_seat_worker_count_as_the_loops(self):
        import loop_procs as LP
        b = os.path.join(os.path.expanduser("~"), ".claude", "bin")   # as every run names it (a link, perhaps)
        snap = [dict(pid=10, ppid=1, pgid=10, command="python3 -B %s/native_worker.py refresh" % b),
                dict(pid=11, ppid=1, pgid=11, command="/usr/bin/python3 -B %s/native_worker.py jobs.json --repo r" % b),
                dict(pid=12, ppid=11, pgid=11, command="claude -p --model x"),
                dict(pid=13, ppid=1, pgid=13, command="python3 /elsewhere/native_worker.py refresh")]
        self.assertEqual(LP.owned(snap, LP.RUNNERS_RX, self_pid=1), {10, 11, 12})


# S2 ROUND 3 (2026-10-18): a held run's child that was not handed the holder, the native worker's own hop, the prelude
HELD_RUN = "BROTHER_LOGIN_HELD_RUN"   # brother_login.HELD_RUN_VAR, written out so a missing name reads red, not an error

#: A Claude role host the pass starts with no descriptor (diag_round's fan out arm), its trusted reader a working fake.
UNHANDED = r'''import os, sys
sys.path.insert(0, %(here)r)
import brother_login as BL
BL.READER = sys.argv[1]
import native_worker as NW
os.environ["BROTHER_LOOP_TOKEN"] = "on"
try:
    env, fd = NW.login_child()
    os.close(fd)
    print("served")
except BL.Failure as exc:
    print("refused " + str(exc))
'''

#: native_worker.main as its fan out spawn runs it (descriptor 47 named and open), then what its fuzz and children see.
NW_MAIN = r'''import contextlib, hashlib, io, os, sys, tempfile
sys.path.insert(0, %(here)r)
import brother_login as BL
BL.READER = sys.argv[1]
import native_worker as NW, land_apply as LA
with contextlib.redirect_stdout(io.StringIO()):
    rc = NW.main([])
print("rc %%d" %% rc)
print("named %%s" %% (BL.HOLD_FD_VAR in os.environ))
print("fuzz env named %%s" %% (BL.HOLD_FD_VAR in LA.suite_env(os.environ, tempfile.gettempdir())))
sys.stdout.flush()
os.system(sys.executable + " -c 'import os; print(\"system child fd \" + (\"OPEN\" if os.path.exists(\"/dev/fd/47\") else \"closed\"))'")
print("own roles served %%s" %% (hashlib.sha256(BL.ask_holder().encode()).hexdigest() == %(sha)r))
'''

#: A child of the sourced launch settings (the prelude before the driver): what it gets when it asks the holder.
PRELUDE_LEAK = r'''import hashlib, os, sys
sys.path.insert(0, %(here)r)
import brother_login as BL
try:
    got = BL.ask_holder()
    r = hashlib.sha256(got.encode()).hexdigest() if got else "EMPTY"
except BL.Failure as exc:
    r = "FAILURE " + str(exc)
with open(sys.argv[1], "w") as fh:
    fh.write(r)
'''


class HeldRun(unittest.TestCase):
    """Round 3 finding 1: a held run marks itself, and a Claude role host in it that was handed no holder (diag_round
    and its fan out, which loop_pass.sh starts with neither name nor descriptor) refuses naming the holder, never a
    quiet fresh Keychain read. Red at 12a64cf96: served by a fresh read (adv-s2r2-sec/diagprobe.py)."""

    def unhanded(self, **extra):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="held-run-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        calls = os.path.join(tmp, "reader-calls")
        reader = _write(os.path.join(tmp, "reader.sh"), READER % {
            "calls": calls, "locked": os.path.join(tmp, "locked"), "token": CANARY}, 0o700)
        script = _write(os.path.join(tmp, "unhanded.py"), UNHANDED % {"here": HERE})
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(extra)
        p = subprocess.run([sys.executable, "-B", script, reader], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        n = 0
        if os.path.exists(calls):
            with open(calls) as fh:
                n = len([l for l in fh if l.strip()])
        self.assertNotIn(CANARY, p.stdout)
        return p.stdout, n

    def test_the_holder_marks_its_run(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="held-mark-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "mark")
        BL.hold(tmp, [sys.executable, "-c", "import os, sys\nopen(sys.argv[1], 'w').write(os.environ.get(%r, 'unset'))"
                      % HELD_RUN, out], reader=lambda: CANARY.encode())
        with open(out) as fh:
            self.assertEqual(fh.read(), "1")

    def test_a_held_run_child_handed_no_holder_refuses_and_never_reads(self):
        out, reads = self.unhanded(**{HELD_RUN: "1"})
        self.assertTrue(out.startswith("refused "), out)
        self.assertIn("login holder", out)
        self.assertEqual(reads, 0, "the Keychain was read fresh inside a held run")

    def test_outside_a_held_run_the_fresh_read_stands(self):
        """Its own condition: no mark (an unheld run, a hand run), so the trusted reader is read as before."""
        out, reads = self.unhanded()
        self.assertEqual(out.strip(), "served", out)
        self.assertEqual(reads, 1)

    def test_the_rollback_reads_fresh_in_a_held_run(self):
        """BROTHER_LOOP_HOLD=off is the documented rollback: the fresh read, mark or not."""
        out, reads = self.unhanded(**{HELD_RUN: "1", "BROTHER_LOOP_HOLD": "off"})
        self.assertEqual(out.strip(), "served", out)
        self.assertEqual(reads, 1)

    def diag(self, model, marked):
        """The real diag_round.main, with a valid code root and every subprocess it would start recorded, not run."""
        import contextlib
        import importlib.util
        import io
        from unittest import mock
        spec = importlib.util.spec_from_file_location("diag_round_r3", os.path.join(os.path.dirname(HERE), "diag_round.py"))
        DR = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(DR)
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="diag-held-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        code = os.path.join(tmp, "code")
        _code_tree(code, "raise SystemExit('never run')\n")
        ran = []

        class _R(object):
            stdout, returncode = "", 0

        clear = (HELD_RUN, "BROTHER_DIAG_MODEL")
        saved = {k: os.environ.pop(k, None) for k in clear}

        def restore():
            for k in clear:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]
        self.addCleanup(restore)
        if marked:
            os.environ[HELD_RUN] = "1"
        if model:
            os.environ["BROTHER_DIAG_MODEL"] = model
        buf = io.StringIO()
        with mock.patch.object(DR.subprocess, "run", lambda *a, **k: ran.append(a) or _R()), contextlib.redirect_stdout(buf):
            rc = DR.main(["--code-root", code, "--out", os.path.join(tmp, "out")])
        return rc, buf.getvalue(), ran

    def test_a_held_run_refuses_a_claude_diagnosis_before_anything_is_asked(self):
        """The decision for diag_round (refuse, not served): on a held run a diagnosis model on the claude transport
        is refused by name before the brief marks any run asked and before any fan out starts."""
        rc, out, ran = self.diag("opus55", True)
        self.assertEqual(rc, 2, out)
        self.assertIn("login holder", out)
        self.assertEqual(ran, [], "nothing was started")

    def test_a_bridge_diagnosis_runs_on_a_held_run(self):
        rc, out, ran = self.diag("deepseek", True)
        self.assertTrue(ran, out)

    def test_an_unheld_run_diagnoses_as_before(self):
        rc, out, ran = self.diag("opus55", False)
        self.assertTrue(ran, out)

    def test_the_finisher_job_clears_the_mark(self):
        """The finisher outlives its run by design and reads fresh (round 1): its job drops the mark with the name."""
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="fin-mark-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        _write(os.path.join(tmp, "finish_run.py"), "import os\nprint('mark ' + os.environ.get(%r, 'unset'))\n" % HELD_RUN)
        with open(os.path.join(HERE, "loop_until.sh")) as fh:
            line = [l for l in fh.read().splitlines() if l.strip().startswith("FIN_JOB=")][0]
        job = line.split("FIN_JOB='", 1)[1].rsplit("'", 1)[0]
        gone = subprocess.Popen(["true"])
        gone.wait()
        log, alarm = os.path.join(tmp, "fin.log"), os.path.join(tmp, "ALARM.txt")
        p = subprocess.run(["bash", "-c", job, "finisher", str(gone.pid), "opus55", tmp, tmp, log, alarm],
                           env=dict(os.environ, **{HELD_RUN: "1"}), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout)
        with open(log) as fh:
            self.assertEqual(fh.read().strip(), "mark unset")


class NativeWorkerHop(unittest.TestCase):
    """Round 3 finding 2: native_worker applies the hop rule at its start, and the landing fuzz env names no holder."""

    def test_the_suite_env_never_names_the_holder(self):
        import loop_switches as LS
        self.assertNotIn(BL.HOLD_FD_VAR, LS.suite_env({BL.HOLD_FD_VAR: "47", "PATH": "/bin"}, "/tmp/h"))

    def test_native_worker_takes_the_hold_at_its_start(self):
        """Its fan out spawn hands it name and descriptor; after main starts, its environment names no holder, the
        fuzz env names none, a shell child has the descriptor closed, and its own seats are still served (take_hold's
        kept name). Red at 12a64cf96: named, the fuzz env named 47, the os.system child held 47 open."""
        try:
            os.fstat(47)
            self.skipTest("NO-DATA: descriptor 47 is already in use in this test process")
        except OSError:
            pass   # sbe: allow-silent 47 is free, the state this case needs
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        os.dup2(theirs.fileno(), 47)
        self.addCleanup(os.close, 47)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="nw-hop-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        dead = _write(os.path.join(tmp, "dead.sh"), "#!/bin/sh\nexit 9\n", 0o700)
        script = _write(os.path.join(tmp, "nw_main.py"), NW_MAIN % {"here": HERE, "sha": CANARY_SHA})
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env[BL.HOLD_FD_VAR] = "47"
        p = subprocess.run([sys.executable, "-B", script, dead], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=60, pass_fds=(47,))
        self.assertNotIn(CANARY, p.stdout)
        for line in ("rc 2", "named False", "fuzz env named False", "system child fd closed", "own roles served True"):
            self.assertIn(line, p.stdout.splitlines(), p.stdout)


class TakeHoldBroken(unittest.TestCase):
    """Round 3 finding 5 (DeepSeek lane L2, candidate 2): a hop name that names nothing open is kept as broken, never as
    a number that a later socket may take, so the process's own roles keep refusing."""

    def test_an_invalid_hop_name_is_not_reused_when_its_number_comes_back(self):
        saved = os.environ.get(BL.HOLD_FD_VAR)
        self.addCleanup(lambda: os.environ.pop(BL.HOLD_FD_VAR, None) if saved is None
                        else os.environ.__setitem__(BL.HOLD_FD_VAR, saved))
        self.addCleanup(setattr, BL, "_HELD", "")
        n = 200
        try:
            os.fstat(n)
            self.skipTest("NO-DATA: descriptor %d is open in this test process" % n)
        except OSError:
            pass   # sbe: allow-silent the number is free, the state this case needs
        os.environ[BL.HOLD_FD_VAR] = str(n)
        self.assertEqual(BL.take_hold(), ({BL.HOLD_FD_VAR: str(n)}, ()))
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(mine.close)
        self.addCleanup(theirs.close)
        os.dup2(theirs.fileno(), n)   # the number comes back as a live holder's socket
        self.addCleanup(os.close, n)
        stop = threading.Event()
        self.addCleanup(stop.set)
        threading.Thread(target=BL.serve, args=(mine, CANARY.encode(), lambda: not stop.is_set()), daemon=True).start()
        with self.assertRaises(BL.Failure) as cm:
            BL.ask_holder()
        self.assertIn("login holder", str(cm.exception))


class PreludeChild(WiredFixture):
    """Round 3 finding 5 (DeepSeek lane L4, candidate 1): start_run.sh sourced the intake's launch settings inside the
    holder's command, so a child of that prelude held the descriptor and could ask. The prelude now runs before the
    holder starts. Red at 12a64cf96: the prelude's child was served the login."""

    def test_a_child_of_the_launch_settings_is_handed_nothing(self):
        leak_out = os.path.join(self.tmp, "prelude-leak")
        leak = _write(os.path.join(self.tmp, "prelude_leak.py"), PRELUDE_LEAK % {"here": HERE})
        with open(self.stubs["stub_intake.py"]) as fh:
            body = fh.read()
        old = 'fh.write("export TEST_FROM_ENV=1\\n")'
        self.assertIn(old, body)
        intake = _write(os.path.join(self.tmp, "leak_intake.py"), body.replace(
            old, 'fh.write("export TEST_FROM_ENV=1\\nTEST_LEAK=$(%s -B %s %s)\\n")' % (sys.executable, leak, leak_out)))
        rc, out = self.go(START_RUN_INTAKE=intake)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.seat()["seat"], CANARY_SHA, "the driver's seat is still served")
        with open(leak_out) as fh:
            self.assertEqual(fh.read(), "EMPTY", "a child of the prelude was named the holder")
        self.assertNoCanaryOnDisk(out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
