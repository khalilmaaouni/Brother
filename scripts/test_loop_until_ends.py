#!/usr/bin/env python3
"""Lane D2: five driver defects an audit found on hub main (2026-09-27, findings E1 to E5), each driven through the REAL
scripts/loop/loop_until.sh with the lifecycle suite's recording stubs (scripts/test_loop_until_lifecycle.py).

E1 money: a running driver re-read the intake with the START rules (a pair record's remaining coverage, a plain record's
   age), so hours into a run the read failed, no BUDGET_USD line came back, and an owner's budget cut was ignored with no
   intervention recorded. The cases run the real loop_intake.py; the funding stub rewrites the record after the driver's
   start read, which is exactly the owner's `loop_intake.py budget` hours later.
E2 safety: a runner stop that failed (STOP INCOMPLETE) still ended DEADLINE at exit 0, which the real handoff accepts.
E3 safety: a signal to the driver alone ended INTERRUPTED (or STOPPED) without stopping the runners, so detached work
   outlived the receipt; and a second signal during an end started a second end.
E4 evidence: a pass exit outside 0 and 42 to 45 fell through as an ordinary pass and a later deadline returned 0.
E5 evidence: BUDGET, DISK and STOPPED were missing from the heartbeat's terminal set, so a stopped driver read alive.

One condition per fixture; each guard has a case that goes red when the guard is removed.
Run: python3 -B scripts/test_loop_until_ends.py        (one case: ... Ends.test_name)
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, HERE)
sys.path.insert(0, LOOP)
import test_loop_until_lifecycle as L  # noqa: E402  the harness: stub HOME, run(), the order recorder
import loop_heartbeat as H  # noqa: E402
import proof_launch  # noqa: E402


def tearDownModule():
    for h in L._HOMES:
        shutil.rmtree(h, ignore_errors=True)


def _bin(home, name):
    return os.path.join(home, ".claude", "bin", name)


# ONE PROVEN BINDING (2026-10-01): the real intake refuses a record with no reachability proof, and an empty proof is no
# proof, so an E1 record that must START carries one bridge binding (no program fingerprint to drift, no breaker open).
REACH = [{"role": "worker", "transport": "bridge", "model": "deepseek", "model_id": "deepseek/deepseek-v4.1-flash",
          "program": "bridge", "fingerprint": "fixture", "version": "fixture"}]


def _record(home):
    return os.path.join(home, ".claude", "evidence", "loop-intake", "CURRENT.json")


def _real_intake(home, record, rewrite_on_first_burn=None):
    """The real loop_intake.py behind the bin path, a record in the scratch evidence, and (optionally) a funding stub that
    rewrites the record once, on its FIRST call: the driver's start read has happened by then, the loop's first read
    has not. `rewrite_on_first_burn` is a dict of fields merged into the record."""
    os.makedirs(os.path.dirname(_record(home)), exist_ok=True)
    with open(_record(home), "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    L._w(_bin(home, "loop_intake.py"),
         "import runpy, sys\nsys.path.insert(0, %r)\nrunpy.run_path(%r, run_name='__main__')\n" % (LOOP, os.path.join(LOOP, "loop_intake.py")))
    if rewrite_on_first_burn is not None:
        src = open(_bin(home, "burn_guard.py"), encoding="utf-8").read().split("\n", 1)
        L._w(_bin(home, "burn_guard.py"), src[0] + "\nimport json as _j, os as _o\n"
             "_c = _o.path.join(_o.environ['HOME'], 'calls', 'burn')\n"
             "if not _o.path.isfile(_c):\n"
             "    _p = %r\n    _r = _j.load(open(_p)); _r.update(%r); _j.dump(_r, open(_p, 'w'))\n" % (_record(home), rewrite_on_first_burn)
             + src[1], 0o755)


def _real_heartbeat(home):
    shutil.copy2(os.path.join(LOOP, "loop_heartbeat.py"), _bin(home, "loop_heartbeat.py"))


def _heartbeat_check(home):
    r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "loop_heartbeat.py"), "check", "--path",
                        os.path.join(home, ".claude", "evidence", "LOOP-HEARTBEAT.json")], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def _receipt(home):
    dirs = L.run_dirs(home)
    if len(dirs) != 1:
        return dirs, {}
    p = os.path.join(dirs[0], "receipt", "receipt.json")
    return dirs, (json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {})


def _handoff(home, rc):
    """The real proof_launch handoff on this run's exit code and receipt: what the pair launcher would decide."""
    dirs, _ = _receipt(home)
    pair = os.path.join(home, "pair-fixture")
    os.mkdir(pair)
    proof_launch.write_pair(pair, dirs[0], os.path.join(home, "run-RC-fixture"), "e" * 64, "f" * 64, "2099-01-01T00:00:00+00:00")
    rec = os.path.join(dirs[0], "receipt", "receipt.json")
    proof_launch.record_result(pair, "RB", rc, rec if os.path.isfile(rec) else None)
    return proof_launch.handoff(pair)[0]


def _low_disk_after_start(home):
    """A df stub: roomy for the start's check, under the floor once the funding stub has been read (the loop runs)."""
    L._w(os.path.join(home, "shim", "df"),
         '#!/bin/bash\n'
         'echo "Filesystem 1024-blocks Used Available Capacity"\n'
         'if [ -f "$HOME/calls/burn" ]; then echo "/dev/x 99999999 1 1000 1%"; else echo "/dev/x 99999999 1 9999999 1%"; fi\n', 0o755)


def _pass_counter(home, first_body, later_body="exit 0"):
    """A pass stub whose first call runs `first_body` and every later call `later_body`; each call is recorded."""
    L._w(_bin(home, "loop_pass.sh"),
         '#!/bin/bash\nn=$(cat "$HOME/calls/pass" 2>/dev/null | wc -l | tr -d " ")\necho call >> "$HOME/calls/pass"\n'
         'if [ "$n" = 0 ]; then\n%s\nexit $?\nfi\n%s\n' % (first_body, later_body), 0o755)


def _env(home):
    base = dict(L.ROOMY, **{k: v for k, v in os.environ.items() if k not in L.ESTATE})
    return dict(base, HOME=home, PATH=os.path.join(home, "shim") + os.pathsep + os.environ.get("PATH", ""),
                BROTHER_LAUNCH_WORKTREE=os.path.join(home, "Brother", ".claude", "worktrees", "brother-unify-1.1"))


def _log(home):
    return "".join(L.ev(home, n) or "" for n in os.listdir(os.path.join(home, ".claude", "evidence")) if n.startswith("loop-until-"))


def _start(home, script, args):
    return subprocess.Popen(["bash", script] + args, env=_env(home), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)


def _finish(p, seconds=90):
    try:
        out, _ = p.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
    return p.returncode, out or ""


END = ["proof-ending", "proof-settle", "stop_loop --runners-only", "clock", "proof-end", "write"]


class Ends(unittest.TestCase):
    # ---------------------------------------------------------------- E1: the running driver reads the current budget
    def _cut_mid_run(self, record, rewrite):
        h = L.make_home(lanes="1", pass_body='echo call >> "$HOME/calls/pass"; exit 42')
        _real_intake(h, record, rewrite)
        r = L.run(h, ["23:59", "0"])
        kinds = [e["kind"] for e in L._events(h)]
        return h, r, kinds

    def test_a_budget_cut_mid_pair_applies_at_once_and_is_recorded(self):
        """E1, the audit's shape: a READY pair record covering the pair at the start; by the first pass it covers only
        10 h (as seven hours into the pair) and the owner has cut the budget to 0.50. The driver reads 0.50, records a
        budget-change, and ends BUDGET on the 0.60 already spent: it runs no further pass."""
        now = time.time()
        iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(s))
        rec = {"verdict": "READY", "at": "t", "epoch": now, "reach": REACH, "budget_usd": 10.0, "deadline": "23:59", "pair": True,
               "pair_until": iso(now + 17 * 3600)}
        h, r, kinds = self._cut_mid_run(rec, {"budget_usd": 0.5, "pair_until": iso(now + 10 * 3600)})
        self.assertEqual(r.returncode, 3, r.stdout[-600:])
        self.assertIn("LOOP BUDGET at", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertEqual(kinds.count("budget-change"), 1, kinds)
        self.assertEqual(L.called(h, "pass"), "")

    def test_a_budget_cut_on_a_record_past_its_start_age_applies_too(self):
        """E1, the same source through a plain record: past the start rule's 6 h age, the running driver still reads
        the figures, so the cut to 0.50 ends the run BUDGET instead of a pass on the stale 10.00."""
        now = time.time()
        rec = {"verdict": "READY", "at": "t", "epoch": now, "reach": REACH, "budget_usd": 10.0, "deadline": "23:59"}
        h, r, kinds = self._cut_mid_run(rec, {"budget_usd": 0.5, "epoch": now - 7 * 3600})
        self.assertEqual(r.returncode, 3, r.stdout[-600:])
        self.assertEqual(kinds.count("budget-change"), 1, kinds)

    def test_an_unreadable_budget_holds_and_runs_no_pass(self):
        """E1: an unreadable budget HOLDS (money fails closed): no pass runs while it cannot be read. Two shapes, one
        condition each: the record's budget is text, or the intake answers nothing at all (it crashed). The lease renewal
        inside the hold makes the budget readable again at 0.50, and the run then ends BUDGET. The base kept the last
        good 10.00 and ran the pass."""
        for label in ("text budget", "no answer"):
            with self.subTest(label):
                now = time.time()
                h = L.make_home(lanes="1", pass_body='echo call >> "$HOME/calls/pass"; exit 42')
                _real_intake(h, {"verdict": "READY", "at": "t", "epoch": now, "reach": REACH, "budget_usd": 10.0, "deadline": "23:59"},
                             {"budget_usd": "ten dollars"} if label == "text budget" else {})
                wrapper = open(_bin(h, "loop_intake.py"), encoding="utf-8").read()
                L._w(_bin(h, "loop_intake.real"), wrapper)
                if label == "no answer":       # from the funding stub's first call on, the intake prints nothing, exit 1
                    src = open(_bin(h, "burn_guard.py"), encoding="utf-8").read().split("\n", 1)
                    L._w(_bin(h, "burn_guard.py"), src[0] + "\nimport os as _o\nif not _o.path.isfile(_o.path.join(_o.environ['HOME'], 'calls', 'burn')):\n"
                         "    open(%r, 'w').write('import sys\\nsys.exit(1)\\n')\n" % _bin(h, "loop_intake.py") + src[1], 0o755)
                L._w(_bin(h, "loop_guard.sh"),
                     '#!/bin/bash\necho "$*" >> "$HOME/calls/guard"\n'
                     'if [ "$1" = renew ]; then cp "%s" "%s"; python3 -c "import json,sys; p=sys.argv[1]; r=json.load(open(p)); r[\'budget_usd\']=0.5; '
                     'json.dump(r, open(p, \'w\'))" "%s"; fi\nexit 0\n' % (_bin(h, "loop_intake.real"), _bin(h, "loop_intake.py"), _record(h)), 0o755)
                r = L.run(h, ["23:59", "0"])
                self.assertEqual(r.returncode, 3, r.stdout[-600:])
                self.assertIn("HOLD: the intake's budget cannot be read", r.stdout)
                self.assertEqual(L.called(h, "pass"), "")

    # ---------------------------------------------------------------- E2: a failed runner stop fails the end
    def test_a_failed_runner_stop_fails_a_deadline_end_and_the_handoff_refuses(self):
        """E2: the stop names survivors (exit 1), cannot read the process table (exit 2), or is missing (127): the end
        is a failure (exit 1), the receipt and the alarm say why, the heartbeat reads ALARM, and the real handoff
        refuses RC. ONE condition per subcase: the stop's outcome; the deadline arrives before any pass."""
        for label, body in (("survivors", 'echo "STOP INCOMPLETE: 1 process(es) still alive"; exit 1'),
                            ("unreadable", 'echo "NO-DATA: the loop\'s processes could not be read"; exit 2'),
                            ("missing", None)):
            with self.subTest(label):
                h = L.make_home(lanes="1", pass_body="exit 0")
                L._intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
                _real_heartbeat(h)
                if body is None:
                    os.remove(_bin(h, "stop_loop.sh"))
                else:
                    L._w(_bin(h, "stop_loop.sh"), '#!/bin/bash\necho "$*" >> "$HOME/calls/stop_loop"\n%s\n' % body, 0o755)
                r = L.run(h, ["23:59", "0"])
                _, receipt = _receipt(h)
                self.assertEqual(r.returncode, 1, r.stdout[-600:])
                self.assertEqual(receipt.get("end_state"), "DEADLINE")
                self.assertIn("the runner stop failed", receipt.get("end_reason", ""))
                self.assertIn("the runner stop failed", L.ev(h, "LOOP-ALARM.txt") or "")
                self.assertEqual(_heartbeat_check(h)[0], 1, _heartbeat_check(h))
                self.assertFalse(_handoff(h, r.returncode))
                self.assertTrue(L._poll(lambda: any("DEADLINE END FAILED" in a and "as critical" in a for a in L._alerts(h)), 10), L._alerts(h))

    def test_a_failed_runner_stop_keeps_the_run_scratch(self):
        """E2 and Lane C's item: runners may still be alive after a stop that failed (STOP INCOMPLETE, exit 1) or could
        not read the process table (NO-DATA, exit 2), so the run scratch they write into is kept, at a deadline and at a
        disk end. pgrep is shadowed (no runner seen) so the stop's own outcome is the one condition."""
        for state, stop in (("DEADLINE", 1), ("DEADLINE", 2), ("DISK", 1), ("DISK", 2)):
            with self.subTest(state=state, stop=stop):
                h = L.make_home(lanes="1", pass_body="exit 0")
                L._w(os.path.join(h, "shim", "pgrep"), "#!/bin/bash\nexit 1\n", 0o755)
                L._w(_bin(h, "stop_loop.sh"), '#!/bin/bash\necho "%s"\nexit %d\n'
                     % ("STOP INCOMPLETE: 1 process(es) still alive" if stop == 1 else "NO-DATA: the process table could not be read", stop), 0o755)
                env = _env(h)
                if state == "DEADLINE":
                    L._intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
                else:
                    _low_disk_after_start(h)
                    env.pop("BROTHER_DISK_FREE_KB")
                r = subprocess.run(["bash", L.SCRIPT, "23:59", "0"], env=env, capture_output=True, text=True, timeout=90)
                scratch = os.path.join(h, ".claude", "brother-scratch")
                self.assertEqual(r.returncode, 1, r.stdout[-600:])
                self.assertIn("LOOP %s at" % state, L.ev(h, "LOOP-ALARM.txt") or "")
                self.assertTrue([d for d in os.listdir(scratch) if d.startswith("run-")], "the run scratch was removed")

    def test_a_process_that_only_names_a_runner_never_keeps_the_scratch(self):
        """Lane C's item: the deadline end kept the run scratch whenever `pgrep -f unit_runner.py` matched, and that
        matches any command line that merely mentions the name (an editor, a grep, a test). The scratch now follows the
        runner stop's own verdict, which reads ownership through loop_procs (the tool as the executable, or an
        interpreter's script or -m module): a stop that confirmed nothing alive removes it. ONE condition: a live
        process whose arguments name unit_runner.py while the stop reports nothing of the loop alive."""
        h = L.make_home(lanes="1", pass_body="exit 0")
        L._intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
        mention = subprocess.Popen(["bash", "-c", "sleep 60", "unit_runner.py"], start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            r = L.run(h, ["23:59", "0"])
        finally:
            os.killpg(mention.pid, signal.SIGKILL)
            mention.wait()
        scratch = os.path.join(h, ".claude", "brother-scratch")
        self.assertEqual(r.returncode, 0, r.stdout[-600:])
        self.assertEqual([d for d in os.listdir(scratch) if d.startswith("run-")], [], "the run scratch was kept")

    def test_an_end_snapshot_that_fails_fails_the_end(self):
        """A proof-end that fails leaves no end snapshot: the end is a failure (exit 1) and the alarm says which step."""
        h = L.make_home(lanes="1")
        d = L._order_driver(h, prelude='sys.exit(2) if sys.argv[1:2] == ["proof-end"] else None\n')
        r = L.run(h, ["23:59", "0"], script=d)
        self.assertEqual(r.returncode, 1, r.stdout[-600:])
        self.assertIn("the end snapshot was not taken", L.ev(h, "LOOP-ALARM.txt") or "")

    def test_a_complete_runner_stop_keeps_a_clean_deadline_end(self):
        """The control for E2: the same deadline with a stop that succeeds is exit 0, the heartbeat reads a normal end,
        and the real handoff allows RC."""
        h = L.make_home(lanes="1", pass_body="exit 0")
        L._intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
        _real_heartbeat(h)
        r = L.run(h, ["23:59", "0"])
        self.assertEqual(r.returncode, 0, r.stdout[-600:])
        self.assertEqual(_heartbeat_check(h)[0], 0, _heartbeat_check(h))
        self.assertTrue(_handoff(h, r.returncode))

    # ---------------------------------------------------------------- E3: every signal runs the whole end
    def _signal_in_gap(self, stop_request):
        h = L.make_home(lanes="1", pass_body="exit 0")
        d = L._order_driver(h)
        p = _start(h, d, ["23:59", "30"])
        L._poll(lambda: "pass 1 done" in _log(h), 60)
        if stop_request:
            with open(os.path.join(h, ".claude", "evidence", "LOOP-STOP-REQUEST.txt"), "w") as f:
                f.write("%d owner stop (fixture)\n" % p.pid)
        sent = time.time()
        os.kill(p.pid, signal.SIGTERM)        # the driver ALONE: nobody else stops its runners
        L._poll(lambda: "proof-ending" in L.called(h, "order"), 30)
        began = time.time() - sent             # the TERM lands as the gap's first 10 s sleep starts
        rc, out = _finish(p)
        return h, rc, out, began

    def test_a_term_to_the_driver_alone_stops_runners_before_the_receipt(self):
        """E3: a TERM to the driver alone (no stop request) ends INTERRUPTED through the whole end order: ending, settle,
        the runner stop, clock, end snapshot, receipt."""
        h, rc, out, began = self._signal_in_gap(False)
        self.assertEqual(rc, 130, out[-600:])
        self.assertIn("LOOP INTERRUPTED at", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertEqual(L._end_order(h), END)
        self.assertNotIn("the running pass", _log(h))   # X2: no pass runs in the gap, so no pid is signalled

    def test_a_signal_in_the_gap_starts_the_end_at_once(self):
        """E3, bounded waits: the gap sleeps in 10 s slices, and bash runs a trap only after its foreground command, so
        a TERM waited out the slice before the end began (stop_loop.sh KILLs a driver still alive 12 s after its TERM).
        The sleep is interruptible: the end begins within 5 s of the TERM (measured from the first end step)."""
        h, rc, out, began = self._signal_in_gap(False)
        self.assertEqual(rc, 130, out[-600:])
        self.assertLess(began, 5.0, "the end began %.1f s after the TERM" % began)

    def test_an_owner_stop_stops_runners_before_the_receipt(self):
        """E3: an owner stop (the request names this driver) ends STOPPED through the same end order."""
        h, rc, out, _ = self._signal_in_gap(True)
        self.assertEqual(rc, 0, out[-600:])
        self.assertIn("LOOP STOPPED at", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertEqual(L._end_order(h), END)

    def test_a_signal_during_an_end_never_starts_a_second_end(self):
        """E3: a TERM arriving while the deadline end settles is noted and the end in progress completes once: one
        ending, one receipt, exit 0. The base ran a second end inside the first."""
        h = L.make_home(lanes="1", pass_body="exit 0")
        L._intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
        d = L._order_driver(h, prelude='import time\ntime.sleep(3) if sys.argv[1:2] == ["proof-settle"] else None\n')
        p = _start(h, d, ["23:59", "0"])
        L._poll(lambda: "proof-settle" in L.called(h, "order"), 60)
        os.kill(p.pid, signal.SIGTERM)
        rc, out = _finish(p)
        order = L._end_order(h)                 # the deadline change is recorded first (proof-event), then the one end
        self.assertEqual(rc, 0, out[-600:])
        self.assertEqual(order[-len(END):], END)
        self.assertEqual((order.count("proof-ending"), order.count("write")), (1, 1), order)
        self.assertIn("LOOP DEADLINE at", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertIn("a signal arrived during the end", _log(h))

    # ---------------------------------------------------------------- X2 finding 2: a stop reaches the end mid pass
    # The pass ran in the foreground and bash runs a trap only after its foreground command, so a TERM to the driver
    # alone (stop_loop.sh signals a driver that leads no group alone, as in the pair launcher) waited for the whole pass;
    # a pass longer than the stop grace was KILLed before the end, and no receipt was written. The stage below stands for
    # the pass's running step (a landing, the pool): it records its pid and sleeps far past every bound here.
    STAGE = ('python3 -c \'import os, signal, time\n%sopen(os.environ["HOME"] + "/calls/stage.pid", "w").write(str(os.getpid()))\n'
             'time.sleep(40)\'')

    def _term_in_pass(self, pass_body):
        h = L.make_home(lanes="1", pass_body=pass_body)
        d = L._order_driver(h)
        p = _start(h, d, ["23:59", "30"])
        stage = os.path.join(h, "calls", "stage.pid")
        self.assertTrue(L._poll(lambda: os.path.isfile(stage) and os.path.getsize(stage) > 0, 60), "the pass never started")
        pid = int(open(stage).read())
        self.addCleanup(self._reap, pid)
        with open(os.path.join(h, ".claude", "evidence", "LOOP-STOP-REQUEST.txt"), "w") as f:
            f.write("%d owner stop (fixture)\n" % p.pid)
        sent = time.time()
        os.kill(p.pid, signal.SIGTERM)        # the driver ALONE, mid pass
        L._poll(lambda: "proof-ending" in L.called(h, "order"), 15)
        began = time.time() - sent
        rc, out = _finish(p)
        return h, rc, out, began, pid

    @staticmethod
    def _alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def _reap(self, pid):
        """Only this fixture's own stage, identified by its command, is ever signalled here."""
        r = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        if "stage.pid" in r.stdout:
            os.kill(pid, signal.SIGKILL)

    def test_an_owner_stop_during_a_long_pass_reaches_the_end_and_the_receipt(self):
        """X2 finding 2: a TERM during a 40 s pass starts the end within a few seconds, in the one end order, and the run
        ends STOPPED with its receipt. The base waited out the pass (a pass of minutes outlived the stop grace)."""
        h, rc, out, began, _ = self._term_in_pass(self.STAGE % "")
        self.assertLess(began, 8.0, "the end began %.1f s after the TERM" % began)
        self.assertEqual(rc, 0, out[-600:])
        self.assertEqual(L._end_order(h), END)
        self.assertEqual(_receipt(h)[1].get("end_state"), "STOPPED", _receipt(h))

    def test_a_stop_during_a_pass_stops_the_step_the_pass_is_running(self):
        """X2 finding 2: the stop is forwarded to the pass's whole process group, so the step it is running (its own
        child) is stopped before the end, never left working past the receipt. A TERM to the pass's pid alone kills the
        pass shell and orphans that child."""
        h, rc, out, began, pid = self._term_in_pass(self.STAGE % "")
        self.assertEqual(rc, 0, out[-600:])
        self.assertTrue(L._poll(lambda: not self._alive(pid), 3), "the pass's running step outlived the end")
        self.assertIn("was stopped before the end", _log(h))

    def test_a_pass_that_ignores_the_stop_is_killed_after_a_bounded_wait(self):
        """X2 finding 2: a pass (and its step) that ignore TERM are KILLed after the bounded wait, and the end still
        begins within a few seconds: the forward can never hold the end past the stop grace."""
        body = "trap '' TERM\n" + self.STAGE % "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        h, rc, out, began, pid = self._term_in_pass(body)
        self.assertLess(began, 8.0, "the end began %.1f s after the TERM" % began)
        self.assertEqual(rc, 0, out[-600:])
        self.assertTrue(L._poll(lambda: not self._alive(pid), 3), "a pass that ignored TERM outlived the end")
        self.assertIn("outlived its TERM", _log(h))
        self.assertEqual(_receipt(h)[1].get("end_state"), "STOPPED", _receipt(h))

    def test_a_second_signal_while_the_pass_is_stopped_ends_once(self):
        """X2 finding 2: a signal arriving while the stop is forwarded to the pass never starts a second end (bash runs
        no trap inside the trap that is forwarding it)."""
        body = "trap '' TERM\n" + self.STAGE % "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        h = L.make_home(lanes="1", pass_body=body)
        d = L._order_driver(h)
        p = _start(h, d, ["23:59", "30"])
        stage = os.path.join(h, "calls", "stage.pid")
        self.assertTrue(L._poll(lambda: os.path.isfile(stage) and os.path.getsize(stage) > 0, 60), "the pass never started")
        self.addCleanup(self._reap, int(open(stage).read()))
        os.kill(p.pid, signal.SIGTERM)
        time.sleep(1.0)                       # inside the 3 s the forward waits on a pass that ignores TERM
        os.kill(p.pid, signal.SIGTERM)
        rc, out = _finish(p)
        order = L._end_order(h)
        self.assertEqual(rc, 130, out[-600:])
        self.assertEqual((order.count("proof-ending"), order.count("write")), (1, 1), order)
        self.assertEqual(order[-len(END):], END)

    def test_the_pass_keeps_the_signal_dispositions_of_a_foreground_pass(self):
        """X2 finding 2: bash starts a background command with INT and QUIT ignored, and everything the pass starts
        (the runners included) would inherit that; the pass still sees both at their defaults."""
        body = ('python3 -c \'import os, signal\nopen(os.environ["HOME"] + "/calls/dispositions", "w").write('
                '"%s %s" % (signal.getsignal(signal.SIGINT) is signal.default_int_handler, '
                'signal.getsignal(signal.SIGQUIT) == signal.SIG_DFL))\'\nexit 42')
        h = L.make_home(lanes="1", pass_body=body)
        r = L.run(h, ["23:59", "1"])
        self.assertEqual(r.returncode, 42, r.stdout[-600:])
        self.assertEqual(L.called(h, "dispositions"), "True True")

    # ---------------------------------------------------------------- E4: an unexpected pass exit is a fault
    def test_an_unexpected_pass_exit_fails_the_end(self):
        """E4: one pass exits 1, 2, 126, 127 or 137 (a crash, a usage error, a script that cannot run, a missing
        command, a KILL) and the next read moves the deadline into the past: the fault is recorded, the end is a
        failure (exit 1) naming it, and the real handoff refuses RC. The base ended DEADLINE at exit 0."""
        for want, body in ((1, "exit 1"), (2, "exit 2"), (126, "exit 126"), (127, "no_such_command_lane_d2"), (137, "kill -KILL $$")):
            with self.subTest(want):
                h = L.make_home(lanes="1")
                _pass_counter(h, body)
                L._intake_seq(h, [("10.00", "23:59"), ("10.00", "23:59"), ("10.00", "00:01")])
                r = L.run(h, ["23:59", "0"])
                _, receipt = _receipt(h)
                self.assertEqual(r.returncode, 1, r.stdout[-600:])
                self.assertIn("PASS FAULT: pass 1 exited %d" % want, r.stdout)
                self.assertEqual(receipt.get("end_state"), "DEADLINE")
                self.assertIn("1 pass fault(s)", receipt.get("end_reason", ""))
                self.assertFalse(_handoff(h, r.returncode))

    def test_an_ordinary_pass_exit_is_no_fault(self):
        """The control for E4: a pass exiting 0 and then the deadline is a clean end, exit 0, no fault named."""
        h = L.make_home(lanes="1")
        _pass_counter(h, "exit 0")
        L._intake_seq(h, [("10.00", "23:59"), ("10.00", "23:59"), ("10.00", "00:01")])
        r = L.run(h, ["23:59", "0"])
        self.assertEqual(r.returncode, 0, r.stdout[-600:])
        self.assertNotIn("PASS FAULT", r.stdout)

    def test_a_pass_that_did_nothing_under_an_owner_pause_does_not_end_the_run(self):
        """Run B, 2026-09-28: a pause written mid pass held every runner, the pass exited 45, and the driver ended the
        run UNPRODUCTIVE. Under a pause, exit 45 is the pause working: the driver waits, resumes, and runs on."""
        h = L.make_home(lanes="1")
        pause = os.path.join(h, ".claude", "evidence", "LOOP-PAUSE.txt")
        _pass_counter(h, 'echo "owner pause" > "%s"; (sleep 4; rm -f "%s") & exit 45' % (pause, pause), "exit 42")
        try:
            r = L.run(h, ["23:59", "0"], timeout=90)
        except subprocess.TimeoutExpired:
            self.fail("the driver never resumed after the pause was lifted")
        self.assertEqual(r.returncode, 42, r.stdout[-800:])
        self.assertIn("HELD: pass exit 45 is not terminal", r.stdout)
        self.assertNotIn("UNPRODUCTIVE", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertEqual(len(L.called(h, "pass").splitlines()), 2)

    def test_a_pass_that_did_nothing_with_no_pause_still_ends_unproductive(self):
        h = L.make_home(lanes="1")
        _pass_counter(h, "exit 45", "exit 42")
        r = L.run(h, ["23:59", "0"], timeout=60)
        self.assertEqual(r.returncode, 45, r.stdout[-600:])
        self.assertEqual(len(L.called(h, "pass").splitlines()), 1)

    def _finisher_stub(self, h):
        L._w(_bin(h, "finish_run.py"),
             'import os, sys\nopen(os.path.join(os.environ["HOME"], "calls", "finish"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n',
             0o755)
        return os.path.join(h, "calls", "finish")

    def _wait_for(self, path, seconds=25):
        end = time.time() + seconds
        while time.time() < end:
            if os.path.isfile(path):
                return open(path).read()
            time.sleep(0.5)
        return None

    def test_a_finished_run_starts_the_finisher_after_the_driver_exits(self):
        """Owner standard 2026-09-22: after every run the finisher runs, always. Run B ended three times on 2026-09-28
        and it never ran: nothing started it. The driver now schedules it, and it starts only after the driver is gone."""
        h = L.make_home(lanes="1")
        _pass_counter(h, "exit 42")
        calls = self._finisher_stub(h)
        r = L.run(h, ["23:59", "0"], extra=dict(BROTHER_FINISHER_MODEL="astra", BROTHER_FINISHER="on"))   # off by default since plan E step 2d
        self.assertEqual(r.returncode, 42, r.stdout[-600:])
        self.assertIn("FINISHER scheduled: astra", r.stdout)
        got = self._wait_for(calls)
        self.assertIsNotNone(got, "the finisher never started after the driver exited")
        self.assertIn("--model astra", got)
        self.assertIn("finisher: scheduled with astra", L.ev(h, "LOOP-ALARM.txt") or "")

    def test_no_finisher_chosen_is_said_and_nothing_starts(self):
        h = L.make_home(lanes="1")
        _pass_counter(h, "exit 42")
        calls = self._finisher_stub(h)
        r = L.run(h, ["23:59", "0"], drop=("BROTHER_FINISHER_MODEL",))
        self.assertEqual(r.returncode, 42, r.stdout[-600:])
        self.assertIn("FINISHER skipped: no finisher was chosen at intake", r.stdout)
        self.assertIsNone(self._wait_for(calls, seconds=4))

    def test_a_recurring_pass_fault_ends_the_run(self):
        """E4, the bound: a pass that faults every time is retried, and on the third fault the run ends BLOCKED as a
        failure (exit 1) after exactly three passes. The base looped until its deadline."""
        h = L.make_home(lanes="1")
        _pass_counter(h, "exit 1", "exit 1")
        try:
            r = L.run(h, ["23:59", "0"], timeout=45)
        except subprocess.TimeoutExpired:
            self.fail("the driver kept running passes that fault")
        self.assertEqual(r.returncode, 1, r.stdout[-600:])
        self.assertIn("LOOP BLOCKED at", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertEqual(len(L.called(h, "pass").splitlines()), 3)

    # ---------------------------------------------------------------- the end report: written with a cost unknown
    def _report_end(self, report_rc):
        """A BUDGET end whose detached report job runs at once (BROTHER_REPORT_DELAY_S=0) with loop_report exiting
        `report_rc`; returns the alarm text and the report text once the job has written to one of them."""
        h = L.make_home(lanes="1", budget="0.50", report_rc=report_rc)
        L._w(_bin(h, "salvage.py"), 'print("SALVAGE LIST FIXTURE")\n', 0o755)
        r = L.run(h, ["23:59", "0"], extra=dict(BROTHER_REPORT_DELAY_S="0"))
        self.assertEqual(r.returncode, 3, r.stdout[-600:])
        path = next((l[len("report: "):].strip() for l in (L.ev(h, "LOOP-ALARM.txt") or "").splitlines() if l.startswith("report: ")), "")
        done = lambda: "SALVAGE LIST FIXTURE" in (open(path).read() if os.path.isfile(path) else "") or "report:" in (L.ev(h, "LOOP-ALARM.txt") or "").split(path)[-1]
        L._poll(done, 20)
        time.sleep(1.0)                         # the job's last append lands just after the first
        return L.ev(h, "LOOP-ALARM.txt") or "", open(path).read() if os.path.isfile(path) else ""

    def test_a_report_written_with_a_cost_unknown_is_kept_and_named(self):
        """Lane G's loop_report.py exits 3 when it wrote the report but a cost is unknown (null, missing, NaN or
        negative). That is not "FAILED to generate": the report is kept with its salvage and journal sections, and the
        alarm says a cost is unknown, so the report never reads as a clean spend."""
        alarm, report = self._report_end(3)
        self.assertIn("a cost is unknown", alarm)
        self.assertNotIn("FAILED to generate", alarm)
        self.assertIn("STANDARD REPORT BODY", report)
        self.assertIn("SALVAGE LIST FIXTURE", report)

    def test_a_report_that_fails_is_still_a_failure(self):
        """The control: any other non zero report exit is still "FAILED to generate", and names its exit code."""
        alarm, _ = self._report_end(1)
        self.assertIn("report: FAILED to generate (exit 1)", alarm)
        self.assertNotIn("a cost is unknown", alarm)

    def test_a_clean_report_names_nothing(self):
        """The control: exit 0 is a clean report: neither a failure nor an unknown cost is written to the alarm."""
        alarm, report = self._report_end(0)
        self.assertNotIn("FAILED to generate", alarm)
        self.assertNotIn("a cost is unknown", alarm)
        self.assertIn("SALVAGE LIST FIXTURE", report)

    # ---------------------------------------------------------------- E5: every end the driver writes is terminal
    def test_a_budget_end_reads_alarm_on_the_heartbeat(self):
        """E5: the real heartbeat after a BUDGET end reads ALARM (exit 1), never alive."""
        h = L.make_home(lanes="1", budget="0.50")
        _real_heartbeat(h)
        r = L.run(h, ["23:59", "0"])
        self.assertEqual(r.returncode, 3, r.stdout[-600:])
        self.assertEqual(_heartbeat_check(h)[0], 1, _heartbeat_check(h))

    def test_a_disk_end_reads_alarm_on_the_heartbeat(self):
        """E5: free disk roomy at the start and under the floor once the loop runs (a df stub that reads low after the
        start's funding read): the run ends DISK and the real heartbeat reads ALARM (exit 1)."""
        h = L.make_home(lanes="1")
        _real_heartbeat(h)
        _low_disk_after_start(h)
        env = {k: v for k, v in _env(h).items() if k != "BROTHER_DISK_FREE_KB"}     # the df stub is the reading
        r = subprocess.run(["bash", L.SCRIPT, "23:59", "0"], env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 3, r.stdout[-600:])
        self.assertIn("LOOP DISK at", L.ev(h, "LOOP-ALARM.txt") or "")
        self.assertEqual(_heartbeat_check(h)[0], 1, _heartbeat_check(h))
        scratch = os.path.join(h, ".claude", "brother-scratch")      # a disk end frees its scratch once the stop confirmed
        self.assertEqual([d for d in os.listdir(scratch) if d.startswith("run-")], [], "a disk end kept its run scratch")

    def test_the_heartbeat_selftest_can_fail(self):
        """E5: the heartbeat's selftest checks its end states, so it must be able to go red, seen from outside (a check
        cannot see its own exit code inverted by running itself): a copy with BUDGET dropped from TERMINAL prints FAILED
        at exit 1, and a copy whose cases raise prints "FAILED before it could finish" at exit 1, never exit 0."""
        src = open(os.path.join(LOOP, "loop_heartbeat.py"), encoding="utf-8").read()
        ref = "    ref = datetime.datetime(2026, 9, 21, 20, 0, 0)\n"
        for label, old, new, want in (("a broken end state", '"UNFUNDED", "BUDGET",', '"UNFUNDED",', "FAILED: a BUDGET end is ALARM"),
                                      ("a case that raises", ref, ref + '    raise RuntimeError("fixture")\n', "FAILED before it could finish")):
            with self.subTest(label):
                self.assertEqual(src.count(old), 1, old)
                with tempfile.TemporaryDirectory() as d:
                    copy = os.path.join(d, "loop_heartbeat.py")
                    with open(copy, "w", encoding="utf-8") as fh:
                        fh.write(src.replace(old, new))
                    r = subprocess.run([sys.executable, "-B", copy, "--selftest"], capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(want, r.stdout)

    def test_every_state_the_driver_writes_is_known_to_the_heartbeat(self):
        """E5, the one list: every state loop_until.sh raises, and REFUSED, is in the heartbeat's TERMINAL; the states it
        writes while running are not; and the ends the driver announces as normal are exactly the heartbeat's good ends.
        A state added to the driver and not to the heartbeat fails here, before it can read as alive."""
        import re
        src = open(L.SCRIPT, encoding="utf-8").read()
        raised = set(re.findall(r"\braise ([A-Z]+) ", src))
        written = set(re.findall(r"loop_heartbeat\.py write --state ([A-Z]+) ", src))
        normal = re.search(r"\n    ([A-Z|]+)\) \( osascript -e \"display alert \\\"brother\.loop \$1: normal end", src)
        self.assertTrue(raised >= {"DEADLINE", "BUDGET", "DISK", "UNFUNDED", "STOPPED", "INTERRUPTED", "FINISHED"}, raised)
        self.assertEqual(raised - H.TERMINAL, set())
        self.assertIn("REFUSED", written)
        self.assertEqual({s for s in written if s != "REFUSED"} & H.TERMINAL, set())
        self.assertIsNotNone(normal)
        self.assertEqual(set(normal.group(1).split("|")), H.GOOD_TERMINAL)


if __name__ == "__main__":
    unittest.main()
