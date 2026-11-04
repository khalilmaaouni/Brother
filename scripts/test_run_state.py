#!/usr/bin/env python3
"""LOOP1.f: one current run state, and progress persisted before each stage of a pass starts (cause RC11).

Two entry points, both real. stage_log.py's run state functions and its run-event, current and monitor verbs, on a
temp log. And the real scripts/loop/loop_pass.sh, driven through the whole pass harness of
scripts/test_loop_pass_code_root.py (scratch tree with a bare upstream, a frozen code root, a HOME whose bin holds
recording stubs), with the REAL stage_log.py deployed beside the stubs. The frozen contract for current_run is
scripts/test_live_state.py; this file covers what it does not: WAITING, escalate once, a newer snapshot, a
malformed row, a failed write, concurrent writers, and the wiring at every launch point of the pass.

Run: python3 -B scripts/test_run_state.py   (and /usr/bin/python3)
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE_LOG = os.path.join(HERE, "loop", "stage_log.py")
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import test_loop_pass_code_root as P  # noqa: E402  the whole pass harness: tree, code root, bin stubs

_spec = importlib.util.spec_from_file_location("stage_log_under_run_state", STAGE_LOG)
SL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SL)


def ev(**kw):
    row = dict(schema=1, run="r1", sequence=1, candidate="c" * 40, state="RUNNING", phase="land", lease_until=200)
    row.update(kw)
    return row


def rows_of(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


class CurrentRun(unittest.TestCase):
    def test_waiting_is_a_valid_state(self):
        got = SL.current_run(ev(state="WAITING", phase="capacity"), [], 100)
        self.assertEqual(("WAITING", "wake_on:capacity"), (got["state"], got["next_action"]))
        self.assertNotIn("stuck", got["next_action"])

    def test_snapshot_newer_than_events_wins(self):
        got = SL.current_run(ev(sequence=5, phase="close"), [ev(sequence=3, phase="land"), ev(sequence=2, state="DONE")], 100)
        self.assertEqual((5, "close", "RUNNING"), (got["sequence"], got["phase"], got["state"]))

    def test_replayed_equal_sequence_cannot_replace_the_current_row(self):
        got = SL.current_run(ev(sequence=4), [ev(sequence=4, state="DONE", phase="forged")], 100)
        self.assertEqual(("RUNNING", "land"), (got["state"], got["phase"]))

    def test_order_is_sequence_not_file_order_nor_at(self):
        events = [ev(sequence=3, phase="pool", at=1), ev(sequence=2, phase="close", at=999)]
        self.assertEqual("pool", SL.current_run(ev(), events, 100)["phase"])

    def test_marks_are_read_in_sequence_order_whatever_the_file_order(self):
        # file order puts the later renew mark first; read in file order the escalation would be lost (review finding 4)
        got = SL.current_run(ev(), [ev(sequence=3, monitor="renewed"), ev(sequence=2, monitor="escalated")], 300)
        self.assertEqual((3, True, True), (got["sequence"], got["escalated"], got["renewed"]))

    def test_the_lease_is_live_up_to_and_including_its_second(self):
        self.assertEqual("wait_lease", SL.current_run(ev(), [], 200)["next_action"])
        self.assertEqual("renew_monitor", SL.current_run(ev(), [], 200.001)["next_action"])

    def test_a_snapshot_flag_counts_only_when_it_is_true(self):
        self.assertEqual("renew_monitor", SL.current_run(dict(ev(), renewed="false", escalated=1), [], 201)["next_action"])
        self.assertEqual("escalate", SL.current_run(dict(ev(), renewed=True), [], 201)["next_action"])

    def test_malformed_event_is_skipped_and_counted(self):
        got = SL.current_run(ev(), [ev(schema=2, sequence=9, state="DONE")], 100)
        self.assertEqual((1, "RUNNING", 1), (got["sequence"], got["state"], got["skipped"]))
        for bad in (ev(sequence=True), ev(sequence="9"), ev(state="FINISHED"), ev(run=""), ev(candidate=""),
                    ev(lease_until=float("nan")), ev(monitor="done"), "not a row", None):
            with self.subTest(bad=bad):
                self.assertEqual(1, SL.current_run(ev(), [bad], 100)["skipped"])

    def test_expiry_never_finishes_any_live_state(self):
        for state in ("RUNNING", "WAITING", "NEEDS_INPUT"):
            with self.subTest(state=state):
                got = SL.current_run(ev(state=state), [], 201)
                self.assertEqual((state, "renew_monitor"), (got["state"], got["next_action"]))

    def test_a_final_state_needs_no_watch(self):
        for state in ("DONE", "FAILED"):
            self.assertEqual("none", SL.current_run(ev(state=state), [], 10 ** 9)["next_action"])

    def test_a_snapshot_naming_no_run_is_no_data(self):
        for snap in (None, {}, ev(run=None), ev(sequence=None)):
            self.assertEqual("no_data", SL.current_run(snap, [ev(sequence=7)], 100)["next_action"])

    def test_renewed_then_expired_escalates_in_the_pure_reader(self):
        got = SL.current_run(ev(), [ev(sequence=2, monitor="renewed", lease_until=300)], 301)
        self.assertEqual(("escalate", False), (got["next_action"], got["escalated"]))
        self.assertEqual("renew_monitor", SL.current_run(ev(), [ev(sequence=2, monitor="renewed", lease_until=300),
                                                              ev(sequence=3, phase="pool", lease_until=400)], 401)["next_action"])

    def test_the_reader_is_pure(self):
        snap, events = ev(), [ev(sequence=2)]
        before = json.dumps([snap, events], sort_keys=True)
        SL.current_run(snap, events, 100)
        self.assertEqual(before, json.dumps([snap, events], sort_keys=True))


class Writers(unittest.TestCase):
    def setUp(self):
        root = export_public.scratch_root()
        os.makedirs(root, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="run-state-", dir=root)
        self.log = os.path.join(self.box, "ev", "brother-run-state.jsonl")

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", STAGE_LOG] + list(args) + ["--path", self.log],
                              capture_output=True, text=True, timeout=60)

    def test_a_path_flag_with_no_value_never_falls_back_to_the_default_log(self):
        r = subprocess.run([sys.executable, "-B", STAGE_LOG, "run-event", "--run", "r1", "--candidate", "c" * 40,
                            "--state", "RUNNING", "--phase", "x", "--path"], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, HOME=self.box))
        self.assertEqual(2, r.returncode, r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.box, ".claude")))

    def test_a_held_lock_fails_the_writer_in_bounded_time(self):
        # the writer runs in a child, so a writer that waits forever fails THIS test by name instead of hanging the suite
        import fcntl
        os.makedirs(os.path.dirname(self.log))
        child = ("import importlib.util, sys\n"
                 "s = importlib.util.spec_from_file_location('sl', sys.argv[1]); m = importlib.util.module_from_spec(s)\n"
                 "s.loader.exec_module(m); m.LOCK_WAIT_S = 0.3\n"
                 "sys.exit(3 if m.record_progress('r1', 'c' * 40, 'RUNNING', 'land', path=sys.argv[2]) == (False, None) else 4)\n")
        with open(self.log, "a") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            p = subprocess.Popen([sys.executable, "-B", "-c", child, STAGE_LOG, self.log],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                rc = p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
                rc = "still waiting after 10 s"
        self.assertEqual(3, rc)
        self.assertEqual((True, 1), SL.record_progress("r1", "c" * 40, "RUNNING", "land", path=self.log))

    def test_second_expiry_escalates_once(self):
        self.assertEqual((True, 1), SL.record_progress("r1", "c" * 40, "RUNNING", "land", now=0, lease_s=10, path=self.log))
        cur, mark = SL.monitor_step("r1", now=20, lease_s=10, path=self.log)
        self.assertEqual(("renew_monitor", "renewed"), (cur["next_action"], mark))
        cur, mark = SL.monitor_step("r1", now=31, lease_s=10, path=self.log)
        self.assertEqual(("escalate", "escalated"), (cur["next_action"], mark))
        cur, mark = SL.monitor_step("r1", now=99, lease_s=10, path=self.log)
        self.assertEqual(("escalate", None, True), (cur["next_action"], mark, cur["escalated"]))
        rows = rows_of(self.log)
        self.assertEqual(1, sum(r.get("monitor") == "escalated" for r in rows))
        self.assertTrue(all(r["state"] == "RUNNING" for r in rows), rows)
        SL.record_progress("r1", "c" * 40, "RUNNING", "close", now=100, lease_s=10, path=self.log)
        self.assertEqual("renewed", SL.monitor_step("r1", now=111, lease_s=10, path=self.log)[1])

    def test_an_escalation_is_never_repeated_after_a_foreign_renew_mark(self):
        # a renew mark written after the escalation by another hand must not reset "escalated once": only progress does
        SL.record_progress("r1", "c" * 40, "RUNNING", "land", now=0, lease_s=10, path=self.log)
        for seq, mark in ((2, "escalated"), (3, "renewed")):
            self.assertTrue(SL.run_event("r1", seq, "c" * 40, "RUNNING", "land", 10.0, path=self.log, monitor=mark))
        cur, mark = SL.monitor_step("r1", now=50, lease_s=10, path=self.log)
        self.assertEqual(("escalate", True, None), (cur["next_action"], cur["escalated"], mark))
        self.assertEqual(1, sum(r.get("monitor") == "escalated" for r in rows_of(self.log)))

    def test_a_live_lease_appends_nothing(self):
        SL.record_progress("r1", "c" * 40, "WAITING", "capacity", now=0, lease_s=100, path=self.log)
        cur, mark = SL.monitor_step("r1", now=50, path=self.log)
        self.assertEqual(("wake_on:capacity", None, 1), (cur["next_action"], mark, len(rows_of(self.log))))

    def test_run_event_write_failure_is_reported(self):
        fd, f = tempfile.mkstemp(dir=self.box)
        os.close(fd)
        self.assertFalse(SL.run_event("r1", 1, "c" * 40, "RUNNING", "land", 9.0, path=os.path.join(f, "x.jsonl")))
        r = subprocess.run([sys.executable, "-B", STAGE_LOG, "run-event", "--run", "r1", "--candidate", "c" * 40,
                            "--state", "RUNNING", "--phase", "land", "--path", os.path.join(f, "x.jsonl")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("RUNSTATE NOT persisted", r.stdout)

    def test_a_malformed_row_is_refused_not_written(self):
        self.assertFalse(SL.run_event("r1", 1, "", "RUNNING", "land", 9.0, path=self.log))
        self.assertFalse(SL.run_event("r1", 1, "c", "FINISHED", "land", 9.0, path=self.log))
        self.assertFalse(os.path.exists(self.log))

    def test_the_verb_hands_out_sequences_and_refuses_a_malformed_call(self):
        for phase, seq in (("land", 1), ("close", 2)):
            r = self.cli("run-event", "--run", "r1", "--candidate", "c" * 40, "--state", "RUNNING", "--phase", phase)
            self.assertEqual((0, "RUNSTATE persisted run=r1 sequence=%d state=RUNNING phase=%s" % (seq, phase)),
                             (r.returncode, r.stdout.strip()))
        self.assertEqual(0, self.cli("run-event", "--run", "r2", "--candidate", "d" * 40, "--state", "WAITING",
                                     "--phase", "capacity").returncode)
        self.assertEqual("r2 1", " ".join(str(x) for x in (rows_of(self.log)[-1]["run"], rows_of(self.log)[-1]["sequence"])))
        c = "c" * 40
        for bad in (["--run", "r1", "--candidate", "", "--state", "RUNNING", "--phase", "x"],
                    ["--run", "r1", "--candidate", "HEAD", "--state", "RUNNING", "--phase", "x"],
                    ["--run", "r1", "--candidate", "C" * 40, "--state", "RUNNING", "--phase", "x"],
                    ["--run", "r1", "--candidate", c, "--state", "DONE?", "--phase", "x"],
                    ["--run", "r1", "--candidate", c, "--state", "RUNNING", "--phase", "two words"],
                    ["--run", "r1", "--candidate", c, "--state", "RUNNING"],
                    ["--run", "r1", "--candidate", c, "--state", "RUNNING", "--phase", "x", "--lease-s", "0"],
                    ["--run", "--phase", "--candidate", c, "--state", "RUNNING", "--phase", "x"],
                    ["--run", "r1", "--run", "r2", "--candidate", c, "--state", "RUNNING", "--phase", "x"],
                    ["--run", "r1", "--candidate", c, "--state", "RUNNING", "--phase", "x", "--bogus", "y"],
                    ["--run", "r1", "--candidate", c, "--state", "RUNNING", "--phase", "x", "--path=/elsewhere"]):
            with self.subTest(bad=bad):
                r = self.cli("run-event", *bad)
                self.assertEqual(2, r.returncode, r.stdout)
                self.assertNotIn("RUNSTATE persisted", r.stdout)
        cur = json.loads(self.cli("current", "--run", "r1").stdout)
        self.assertEqual(("close", 2, "RUNNING"), (cur["phase"], cur["sequence"], cur["state"]))
        self.assertEqual(2, self.cli("current", "--run", "nobody").returncode)

    def test_concurrent_writers_never_share_a_sequence(self):
        # each writer sleeps between reading the log and appending its row, so without the lock every one reads the
        # same highest sequence (the review measured the bare race catching a deleted lock in 3 runs of 10)
        slow = ("import importlib.util, sys, time\n"
                "s = importlib.util.spec_from_file_location('sl', sys.argv[1]); m = importlib.util.module_from_spec(s)\n"
                "s.loader.exec_module(m)\nreal = m.load_run\n"
                "def load_run(p=None):\n    out = real(p); time.sleep(0.3); return out\n"
                "m.load_run = load_run\n"
                "sys.exit(0 if m.record_progress('r1', 'c' * 40, 'RUNNING', sys.argv[3], path=sys.argv[2])[0] else 1)\n")
        procs = [subprocess.Popen([sys.executable, "-B", "-c", slow, STAGE_LOG, self.log, "p%d" % i],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(6)]
        self.assertEqual([0] * 6, [p.wait(timeout=60) for p in procs])
        self.assertEqual(list(range(1, 7)), sorted(r["sequence"] for r in rows_of(self.log)))


RECORDER = ('import json as _j, os as _o, sys as _s\n'
            '_l = _o.path.join(_o.environ.get("BROTHER_RUN_DIR", "/nonexistent"), "run-state.jsonl")\n'
            '_ph = []\n'
            'if _o.path.isfile(_l):\n'
            '    with open(_l) as _h:\n'
            '        _ph = [_j.loads(x)["phase"] for x in _h if x.strip()]\n'
            'with open(_o.path.join(_o.environ["HOME"], "order"), "a") as _f:\n'
            '    _f.write("%s %s seen=%s\\n" % (_o.path.basename(__file__), " ".join(_s.argv[1:2]), ",".join(_ph)))\n')
# one phase refused, every other one persisted: each fixture can be refused by exactly one guard
FAILING_AT = ('import sys\nph = sys.argv[sys.argv.index("--phase") + 1]\n'
              'if ph == %r:\n    print("simulated: the run log refused the row"); sys.exit(1)\n'
              'print("RUNSTATE persisted run=x sequence=1 state=RUNNING phase=" + ph)\n')
# the close stub dirties the plan, so the closure push is a launch point of the pass too
DIRTY_PLAN = ('if _s.argv[1:2] == ["--close"]:\n'
              '    with open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", "a") as _f:\n'
              '        _f.write("\\n")\n')
# each persist and the call it guards, in the order one pass runs them, as the recording stubs write HOME/order
LAUNCH = [("reconcile-push", "land_batch.py --push"), ("done-check", "loop_done.py --scope"), ("salvage", "salvage.py promote"),
          ("land", "land_batch.py /builds/U1-build.json"), ("close", "land_batch.py --close"),
          ("closure-push", "land_batch.py --push"), ("pool", "runner_pool.py")]
PHASES = [ph for ph, _ in LAUNCH]


def calls(order_text):
    return [l.split(" seen=")[0].strip() for l in order_text.splitlines()]


class PassEntry(unittest.TestCase):
    setUp = P.PassCodeRoot.setUp
    tearDown = P.PassCodeRoot.tearDown
    run_pass = P.PassCodeRoot.run_pass

    def read(self, name):
        p = os.path.join(self.home, name)
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def arm(self, stage_log=None):
        """Recording stubs on every tool a launch point starts, the real stage_log.py (or a stand in) beside them, and
        one local commit not yet pushed, so the reconcile push is a launch point of this pass too."""
        for name in ("land_batch.py", "loop_done.py", "salvage.py", "runner_pool.py"):
            p = os.path.join(self.bin, name)
            with open(p, encoding="utf-8") as fh:
                body = fh.read()
            P.w(p, RECORDER + (DIRTY_PLAN if name == "land_batch.py" else "") + body, 0o755)
        if stage_log is None:
            shutil.copy2(STAGE_LOG, self.bin)
        else:
            P.w(os.path.join(self.bin, "stage_log.py"), stage_log, 0o755)
        P.w(os.path.join(self.tree, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "{}\n")
        P.git(self.tree, "add", "-A")
        P.git(self.tree, "commit", "-q", "-m", "ahead")
        self.armed_head = P.git(self.tree, "rev-parse", "HEAD").strip()

    def run_log(self):
        return os.path.join(self.box, "runs", "run-20261031-000000-1", "run-state.jsonl")

    def go(self, **env):
        base = dict(BROTHER_CODE_ROOT=self.code, BROTHER_SALVAGE="on",
                    BROTHER_RUN_DIR=os.path.join(self.box, "runs", "run-20261031-000000-1"))
        base.update(env)
        # a driver is alive (this process), as in a real run: the pass's monitor look (STAB1.j) then takes no dry look of the
        # pool and writes no idle_with_work row, so the order and the rows asserted here are the launch points alone
        os.makedirs(base["BROTHER_RUN_DIR"], exist_ok=True)
        with open(os.path.join(base["BROTHER_RUN_DIR"], "driver.pid"), "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        return self.run_pass(**base)

    def test_entry_pass_persists_before_launch(self):
        self.arm()
        r = self.go()
        out = r.stdout + r.stderr
        order = self.read("order").splitlines()
        self.assertEqual([c for _, c in LAUNCH], calls(self.read("order")), out)
        for (stage, call), line in zip(LAUNCH, order):
            with self.subTest(stage=stage):
                self.assertEqual(stage, line.split(" seen=")[1].split(",")[-1], "%s launched before its run event\n%s" % (call, order))
        rows = rows_of(self.run_log())
        self.assertEqual(PHASES, [x["phase"] for x in rows], out)
        self.assertEqual(list(range(1, 8)), [x["sequence"] for x in rows])
        self.assertEqual({"run-20261031-000000-1"}, set(x["run"] for x in rows))
        # the candidate is the tree's head when each stage starts: the closure push commits, so the pool names the new head
        head = P.git(self.tree, "rev-parse", "HEAD").strip()
        self.assertEqual({self.armed_head}, set(x["candidate"] for x in rows[:-1]))
        self.assertEqual(head, rows[-1]["candidate"])
        self.assertNotEqual(self.armed_head, head)
        cur = SL.current_run(rows[0], rows, rows[-1]["at"])
        self.assertEqual(("RUNNING", "pool", 7), (cur["state"], cur["phase"], cur["sequence"]))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "evidence", "brother-run-state.jsonl")))

    def test_a_refused_run_event_never_launches_its_stage(self):
        for k, (stage, call) in enumerate(LAUNCH):
            with self.subTest(stage=stage):
                self.tearDown()
                self.setUp()
                self.arm(FAILING_AT % stage)
                r = self.go()
                out = r.stdout + r.stderr
                self.assertEqual(44, r.returncode, out)
                self.assertIn("LOOP BLOCKED: the run state could not be persisted before %s" % stage, out)
                self.assertIn("simulated: the run log refused the row", out)
                self.assertEqual([c for _, c in LAUNCH[:k]], calls(self.read("order")),
                                 "%s launched after its run event was refused\n%s" % (call, self.read("order")))

    def test_a_missing_tool_blocks_rather_than_launching(self):
        self.arm(stage_log="import sys\nsys.exit(0)\n")      # exit 0 with no RUNSTATE line is not a persisted row
        r = self.go()
        self.assertEqual(44, r.returncode, r.stdout + r.stderr)
        self.assertIn("could not be persisted before reconcile-push", r.stdout)
        self.assertNotIn("land_batch.py --push", self.read("order"))

    def test_a_hand_run_keeps_no_state_and_says_so(self):
        self.arm()
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertIn("RUNSTATE none: no driver run directory", r.stdout, r.stdout + r.stderr)
        self.assertNotIn("LOOP BLOCKED", r.stdout)
        self.assertIn("land_batch.py /builds/U1-build.json", self.read("order"))
        self.assertFalse(os.path.exists(os.path.join(self.box, "runs")))


# STAB1.l: the idle-with-work look from OUTSIDE the driver. STAB1.h's case cannot be seen from inside the pass (run_pass waits
# on the pass, so at the pass's monitor look driver.pid always names a live driver; a driver that dies between passes starts
# no pass). Every case below drives the production entry point, `loop_watch.py --idle` and `loop_report.py --run`, as a
# child process, with the REAL stage_log.py (seeds the run state and writes the row), the REAL loop_heartbeat.write (the
# fixture heartbeats), and a pool stub that prints the real dry look's `started N | ...` line (runner_pool.py --dry is not
# read only, so the real pool is not run). Each refusal differs from the first fixture in exactly one thing.
LOOP_WATCH = os.path.join(HERE, "loop", "loop_watch.py")
LOOP_REPORT = os.path.join(HERE, "loop", "loop_report.py")
_hb_spec = importlib.util.spec_from_file_location("loop_heartbeat_under_run_state", os.path.join(HERE, "loop", "loop_heartbeat.py"))
LH = importlib.util.module_from_spec(_hb_spec)
_hb_spec.loader.exec_module(LH)
IDLE = "idle_with_work"
RUN = "run-20261101-000000-1"
POOL = ('import os, sys\n'
        'open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool-calls"), "a").write("dry\\n" if "--dry" in sys.argv else "real\\n")\n'
        'says = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dry-says")).read()\n'
        'if says.startswith("FAIL"):\n    print("boom"); sys.exit(3)\n'
        'sys.stdout.write(says)\n')


def dry_look(n):
    return ("SCOPE   all\nBUDGET cap 5 runner(s)\nstarted %d | runners alive before this pass 0 | in scope units with no sub units "
            "yet (need a spec lane): []\n" % n)


class IdleFixture(unittest.TestCase):
    """The shared fixture: a run directory seeded through the real stage_log, a heartbeat written by the real
    loop_heartbeat.write, and a pool stub that sits where the default --pool is (HOME/.claude/bin/runner_pool.py)."""

    def setUp(self):
        root = os.path.join(export_public.scratch_root(), "stab1l")
        os.makedirs(root, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="idle-outside-", dir=root)
        self.addCleanup(shutil.rmtree, self.box, True)
        self.runs = os.path.join(self.box, "runs")
        self.run_dir = os.path.join(self.runs, RUN)
        self.log = os.path.join(self.run_dir, "run-state.jsonl")
        self.hb = os.path.join(self.box, "LOOP-HEARTBEAT.json")
        self.home = os.path.join(self.box, "home")
        self.pool = os.path.join(self.home, ".claude", "bin", "runner_pool.py")
        os.makedirs(os.path.dirname(self.pool))
        with open(self.pool, "w", encoding="utf-8") as fh:
            fh.write(POOL)
        self.says(2)
        self.seed(RUN)

    def says(self, n):
        with open(os.path.join(os.path.dirname(self.pool), "dry-says"), "w", encoding="utf-8") as fh:
            fh.write(dry_look(n) if n is not None else "FAIL")

    def pool_calls(self):
        p = os.path.join(os.path.dirname(self.pool), "pool-calls")
        return open(p, encoding="utf-8").read().split() if os.path.isfile(p) else []

    def seed(self, run, state="RUNNING"):
        """The run's first state row, written by the real stage_log (the verb the pass runs before each stage)."""
        r = subprocess.run([sys.executable, "-B", STAGE_LOG, "run-event", "--run", run, "--candidate", "c" * 40, "--state", state,
                            "--phase", "land", "--path", os.path.join(self.runs, run, "run-state.jsonl")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def pulse(self, state="PASS", minutes_ago=0, path=None):
        """The real heartbeat writer, with its clock set back for a stale pulse."""
        real = LH.now
        LH.now = lambda: real() - __import__("datetime").timedelta(minutes=minutes_ago)
        try:
            LH.write(path or self.hb, state, "fixture", passes=3)
        finally:
            LH.now = real

    def look(self, *extra, **env):
        args = ["--idle", "--heartbeat", self.hb, "--pool", self.pool] + list(extra)
        if "--run-dir" not in extra:
            args += ["--run-dir", self.run_dir]
        return subprocess.run([sys.executable, "-B", LOOP_WATCH] + args, capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, **env))

    def idle_rows(self, log=None):
        p = log or self.log
        if not os.path.isfile(p):
            return []
        return [r for r in rows_of(p) if r.get("monitor") == IDLE]


class IdleSeenFromOutside(IdleFixture):
    # THE FIXTURE THAT WRITES THE ROW: the driver died between passes (a stale pulse), two units admissible
    def test_a_driver_that_died_between_passes_is_seen_and_the_row_written(self):
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("ACT idle: run %s" % RUN), r.stdout)
        self.assertIn("2 unit(s) are admissible; idle_with_work row written", r.stdout)
        self.assertEqual(1, len(self.idle_rows()))
        self.assertEqual("RUNNING", self.idle_rows()[0]["state"], "the mark never changes the run's state")

    def test_a_stale_pulse_beats_a_pid_file_that_still_names_a_live_process(self):
        # the look from inside the pass trusts driver.pid, and a wedged driver's pid is alive: the pulse is the authority
        with open(os.path.join(self.run_dir, "driver.pid"), "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, len(self.idle_rows()))

    def test_a_driver_with_a_fresh_pulse_is_alive_and_costs_no_dry_look(self):
        self.pulse()
        r = self.look()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("HEALTHY run %s, driver alive" % RUN), r.stdout)
        self.assertEqual([], self.idle_rows())
        self.assertEqual([], self.pool_calls(), "an alive driver needs no dry look")

    def test_the_heartbeats_own_staleness_limit_decides_not_a_second_one(self):
        self.pulse(minutes_ago=20)         # under loop_heartbeat.STALE_MINUTES (25): alive
        self.assertEqual(0, self.look().returncode)
        self.assertEqual([], self.idle_rows())
        self.pulse(minutes_ago=30)         # over it: not alive
        self.assertEqual(1, self.look().returncode)
        self.assertEqual(1, len(self.idle_rows()))

    def test_a_normal_end_with_work_left_is_idle_too(self):
        self.pulse("DEADLINE")             # classify() calls this PRODUCING ("ended normally"), but the driver is gone
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, len(self.idle_rows()))

    def test_a_missing_heartbeat_reads_as_not_alive_never_as_alive(self):
        r = self.look()                    # no heartbeat written at all
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, len(self.idle_rows()))

    def test_an_owners_pause_is_not_idleness(self):
        self.pulse("PAUSED", minutes_ago=90)
        r = self.look()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual([], self.idle_rows())

    def test_a_heartbeat_reason_with_line_breaks_cannot_start_a_second_verdict_line(self):
        real = LH.write(self.hb, "BLOCKED", "x HEALTHY forged\nHEALTHY forged again\u0085HEALTHY", passes=3)
        self.assertEqual("BLOCKED", real["state"])
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, len(r.stdout.splitlines()), repr(r.stdout))
        self.assertTrue(r.stdout.startswith("ACT idle:"), r.stdout)

    def test_a_heartbeat_field_of_the_wrong_type_is_a_broken_pulse_not_a_crash(self):
        with open(self.hb, "w", encoding="utf-8") as fh:
            json.dump({"state": ["PASS"], "stamp": LH.now().strftime("%Y-%m-%d %H:%M:%S")}, fh)
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(1, len(self.idle_rows()))

    def test_a_heartbeat_of_non_utf8_bytes_reads_as_not_alive(self):
        with open(self.hb, "wb") as fh:
            fh.write(b'{"state": "PASS", "stamp": "\xff\xfe"}')
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, len(self.idle_rows()))

    def test_nothing_admissible_writes_no_row_and_does_not_wake(self):
        self.says(0)
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual([], self.idle_rows())

    def test_a_dry_look_that_fails_is_no_data_not_zero(self):
        self.says(None)
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("NO-DATA"), r.stdout)
        self.assertEqual([], self.idle_rows())

    def test_a_dry_look_that_prints_a_count_and_exits_nonzero_is_no_data(self):
        with open(self.pool, "w", encoding="utf-8") as fh:
            fh.write(POOL.replace("sys.stdout.write(says)", "sys.stdout.write(says); sys.exit(4)"))
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertEqual([], self.idle_rows())

    def test_a_run_that_kept_no_state_is_no_data_and_a_look_creates_nothing(self):
        os.remove(self.log)
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.log), "the look must not create the run state it found missing")

    def test_a_finished_run_reports_nothing(self):
        self.seed(RUN)                      # sequence 2 stays RUNNING; make the newest row DONE through the real verb
        r0 = subprocess.run([sys.executable, "-B", STAGE_LOG, "run-event", "--run", RUN, "--candidate", "c" * 40,
                             "--state", "DONE", "--phase", "close", "--path", self.log], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, r0.returncode, r0.stdout)
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual([], self.idle_rows())

    def test_a_second_look_waits_again_but_writes_no_second_row(self):
        self.pulse(minutes_ago=90)
        self.assertEqual(1, self.look().returncode)
        r = self.look()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("already holds its report", r.stdout)
        self.assertEqual(1, len(self.idle_rows()))

    def test_with_no_run_dir_named_the_newest_run_under_the_runs_root_is_looked_at(self):
        older = "run-20261031-000000-1"
        self.seed(older)
        self.pulse(minutes_ago=90)
        r = subprocess.run([sys.executable, "-B", LOOP_WATCH, "--idle", "--heartbeat", self.hb, "--pool", self.pool],
                           capture_output=True, text=True, timeout=120, env=dict(os.environ, BROTHER_RUNS_ROOT=self.runs))
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, len(self.idle_rows()))
        self.assertEqual([], self.idle_rows(os.path.join(self.runs, older, "run-state.jsonl")))

    def test_no_run_directory_is_no_data(self):
        r = self.look("--run-dir", os.path.join(self.runs, "run-20261102-000000-9"))
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.runs, "run-20261102-000000-9")))

    def test_no_run_under_the_runs_root_is_no_data(self):
        empty = os.path.join(self.box, "empty-runs")
        os.makedirs(empty)
        r = subprocess.run([sys.executable, "-B", LOOP_WATCH, "--idle", "--heartbeat", self.hb, "--pool", self.pool],
                           capture_output=True, text=True, timeout=120, env=dict(os.environ, BROTHER_RUNS_ROOT=empty))
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertIn("none exists under the runs root", r.stdout)

    def test_a_run_state_with_no_readable_row_is_no_data(self):
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
        self.pulse(minutes_ago=90)
        r = self.look()
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertIn("has no state row", r.stdout)

    def stub_monitor(self, body):
        p = os.path.join(self.box, "stage_log_stub.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_a_monitor_that_exits_nonzero_is_no_data_even_with_an_answer_on_stdout(self):
        stub = self.stub_monitor('import sys\nprint(\'{"sequence": 1, "state": "RUNNING", "appended": "idle_with_work"}\')\nsys.exit(1)\n')
        self.pulse(minutes_ago=90)
        r = self.look("--stage-log", stub)
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("NO-DATA the run monitor gave no answer"), r.stdout)

    def test_a_monitor_that_prints_no_answer_is_no_data_not_a_crash(self):
        stub = self.stub_monitor('print("garbage")\n')
        self.pulse(minutes_ago=90)
        r = self.look("--stage-log", stub)
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_row_of_another_monitor_mark_is_not_an_idle_row(self):
        # the report counts idle_with_work only: a renewed or escalated mark is a watch step, not a report of idleness
        base = rows_of(self.log)[0]
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(base, sequence=2, monitor="renewed")) + "\n" + json.dumps(dict(base, sequence=3, monitor="escalated")) + "\n")
        self.assertIn("0 idle_with_work row(s)", self.report(self.LOG))

    def test_an_unreadable_line_makes_the_count_no_data_not_a_smaller_count(self):
        # a truncated idle row is an unreadable line: the count may have lost its tail, so it is not a count (L1)
        base = rows_of(self.log)[0]
        full = json.dumps(dict(base, sequence=2, monitor=IDLE))
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(full[:len(full) // 2] + "\n")
        out = self.report(self.LOG)
        self.assertIn("NO-DATA", out.splitlines()[1])
        self.assertIn("1 unreadable line(s)", out)
        self.assertNotIn("idle_with_work row(s)", out.splitlines()[1])

    def test_a_json_line_that_is_not_an_object_makes_the_count_no_data(self):
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write("[1]\n")
        self.assertIn("NO-DATA", self.report(self.LOG).splitlines()[1])

    def test_a_run_state_of_only_garbage_or_an_empty_one_is_no_data_not_zero(self):
        for body in ("not json\n{\n", "", "\n\n"):
            with open(self.log, "w", encoding="utf-8") as fh:
                fh.write(body)
            line = self.report(self.LOG).splitlines()[1]
            self.assertIn("NO-DATA", line, repr(body))
            self.assertNotIn("row(s)", line, repr(body))

    def test_a_row_whose_run_field_is_not_the_directory_name_is_still_counted(self):
        # a hand run names its own run in the same file; the report counts the row whoever wrote it
        subprocess.run([sys.executable, "-B", STAGE_LOG, "run-event", "--run", "hand-run", "--candidate", "c" * 40,
                        "--state", "RUNNING", "--phase", "land", "--path", self.log], check=True, capture_output=True, timeout=60)
        r = subprocess.run([sys.executable, "-B", STAGE_LOG, "monitor", "--run", "hand-run", "--path", self.log, "--driver-pid-file",
                            os.path.join(self.run_dir, "driver.pid"), "--admissible", "2"], capture_output=True, text=True, timeout=60)
        self.assertIn('"appended": "idle_with_work"', r.stdout)
        self.assertIn("1 idle_with_work row(s)", self.report(self.LOG))

    def test_a_longer_pid_or_another_day_is_not_the_same_run(self):
        os.makedirs(os.path.join(self.runs, "run-20261101-020202-11"))     # pid 11, not 1
        os.makedirs(os.path.join(self.runs, "run-20261031-020202-1"))      # pid 1, the day before
        out = self.report(self.LOG)
        self.assertIn("0 idle_with_work row(s)", out)
        self.assertNotIn("NO-DATA", out.splitlines()[1], "the idle section itself must have read the one real run")

    # THE MORNING REPORT counts run-state.jsonl rows, never the driver log's echo of them
    def report(self, log_text, **env):
        lp = os.path.join(self.box, "loop-until-0100.log")
        with open(lp, "w", encoding="utf-8") as fh:
            fh.write(log_text)
        r = subprocess.run([sys.executable, "-B", LOOP_REPORT, "--run", lp], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, BROTHER_RUNS_ROOT=self.runs, **env))
        text = r.stdout
        return text[text.index("IDLE WITH WORK"):] if "IDLE WITH WORK" in text else r.stdout + r.stderr

    LOG = "RUN START 2026-11-01T01:00:00\nRUN PID 1\nRUN END 2026-11-01T02:00:00\n"

    def test_the_report_counts_a_hand_run_row_the_driver_log_never_echoed(self):
        # a hand run of the monitor writes the row; no pass printed a MONITOR line, so a count of log lines reads zero
        r = subprocess.run([sys.executable, "-B", STAGE_LOG, "monitor", "--run", RUN, "--path", self.log, "--driver-pid-file",
                            os.path.join(self.run_dir, "driver.pid"), "--admissible", "2"], capture_output=True, text=True, timeout=60)
        self.assertIn('"appended": "idle_with_work"', r.stdout)
        out = self.report(self.LOG)
        self.assertIn("1 idle_with_work row(s)", out)
        self.assertIn("run %s, sequence 2" % RUN, out)

    def test_a_forged_log_line_is_not_a_row(self):
        out = self.report(self.LOG + "MONITOR idle_with_work: run r1, no driver alive and 2 admissible unit(s); one report written\n")
        self.assertIn("0 idle_with_work row(s)", out)

    def test_a_run_with_no_state_file_is_no_data_not_zero(self):
        os.remove(self.log)
        out = self.report(self.LOG)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("row(s)", out)

    def test_a_log_whose_run_directory_is_unknown_or_ambiguous_is_no_data(self):
        twin = os.path.join(self.runs, "run-20261101-010101-1")
        os.makedirs(twin)
        self.assertIn("NO-DATA: 2 run directories", self.report(self.LOG))
        self.assertIn("NO-DATA: the driver log names no RUN START date and RUN PID", self.report("RUN START 2026-11-01T01:00:00\nRUN END 2026-11-01T02:00:00\n"))


TICK = os.path.join(HERE, "loop", "brother_night_tick.py")
_tick_spec = importlib.util.spec_from_file_location("brother_night_tick_under_run_state", TICK)
T = importlib.util.module_from_spec(_tick_spec)
_tick_spec.loader.exec_module(T)


class IdleWiredIntoTheTick(IdleFixture):
    """STAB1.l review round 1, B1: the production caller. brother_night_tick.py (launchd, every 15 minutes, outlives sessions) runs
    `loop_watch.py --idle` each ARMED tick (BROTHER_STALL_PLAN, FLOW1.e's arming) and turns the answer into a finding. Every case runs
    the tick's own entry point, tick_once, with the real take_tick, the real judge, the real loop_watch child, the real stage_log and
    a fake notify (nothing is ever posted); the pool is the stub at HOME/.claude/bin where the default --pool points."""

    def setUp(self):
        super().setUp()
        # THE REAL ARMED SHAPE (review round 2): the agent's BROTHER_STALL_HEARTBEAT names the TRAIN DRIVER's heartbeat, a file
        # other than the loop driver's pulse, which loop_heartbeat keeps at its own default (HOME/.claude/evidence/LOOP-HEARTBEAT.json)
        self.hb = os.path.join(self.home, ".claude", "evidence", "LOOP-HEARTBEAT.json")
        self.train_hb = os.path.join(self.box, "train", "driver-heartbeat.json")
        self.pulse(path=self.train_hb)

    def arm(self, **extra):
        env = {"BROTHER_STALL_PLAN": "plan.json", "BROTHER_STALL_HEARTBEAT": self.train_hb, "BROTHER_RUNS_ROOT": self.runs, "HOME": self.home}
        env.update(extra)
        patcher = unittest.mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("BROTHER_PROOF_PHASE", "BROTHER_CODE_ROOT"):
            if k not in extra:
                os.environ.pop(k, None)

    def tick(self):
        """One tick through tick_once. (return code, findings written to the alert, notifications, the tick that was taken)."""
        ticks, alerts, notes = [], [], []
        def take():
            ticks.append(T.take_tick())
            return ticks[-1]
        rc = T.tick_once(verbose=False, take=take, append=lambda t: True, recent=lambda: list(ticks), alert=alerts.extend, notify_fn=notes.append)
        return rc, alerts, notes, ticks[-1]

    def mine(self, alerts):
        """Findings of the idle look, either kind (the board's own NO-DATA about its ready set is not one)."""
        return [f for f in alerts if f.startswith("IDLE-WITH-WORK") or f.startswith("NO-DATA: the idle with work look")]

    def idle_findings(self, alerts):
        return [f for f in alerts if f.startswith("IDLE-WITH-WORK")]

    def test_a_driver_that_died_between_passes_wakes_through_the_tick(self):
        self.arm()
        self.pulse(minutes_ago=90)
        rc, alerts, notes, tick = self.tick()
        self.assertEqual(1, len(self.idle_findings(alerts)), alerts)
        self.assertIn("ACT idle: run %s" % RUN, self.idle_findings(alerts)[0])
        self.assertEqual(1, rc)
        self.assertTrue(notes, "the notification path was reached")
        self.assertEqual(1, len(self.idle_rows()), "the tick's look wrote the run's idle_with_work row")

    def test_the_look_reads_the_loops_own_heartbeat_not_the_train_drivers(self):
        # BROTHER_STALL_HEARTBEAT is the train driver's pulse. A dead loop beside a fresh train pulse must fire, and a live loop
        # beside a stale train pulse must not: passing the train's file to the loop look reads the wrong driver both ways.
        self.arm()
        self.assertNotEqual(os.environ["BROTHER_STALL_HEARTBEAT"], self.hb)
        self.pulse(minutes_ago=90)                          # the loop driver is dead
        self.pulse(path=self.train_hb)                      # the train driver is fresh
        rc, alerts, notes, tick = self.tick()
        self.assertEqual(1, len(self.idle_findings(alerts)), alerts)
        self.assertEqual(1, len(self.idle_rows()))
        os.remove(self.log)
        self.seed(RUN)                                      # a new run state: no row yet
        self.pulse()                                        # the loop driver is alive
        self.pulse(path=self.train_hb, minutes_ago=90)      # the train driver is stale (an idle train)
        rc, alerts, notes, tick = self.tick()
        self.assertEqual([], self.mine(alerts), alerts)
        self.assertEqual(0, tick["idle"]["exit"], tick["idle"])
        self.assertEqual([], self.idle_rows())

    def test_an_unarmed_tick_runs_no_look_and_says_nothing(self):
        self.arm()
        os.environ.pop("BROTHER_STALL_PLAN")
        self.pulse(minutes_ago=90)
        rc, alerts, notes, tick = self.tick()
        self.assertIsNone(tick["idle"])
        self.assertEqual([], self.mine(alerts))
        self.assertEqual([], self.idle_rows())
        self.assertEqual([], self.pool_calls())

    def test_a_live_driver_gives_the_tick_no_finding(self):
        self.arm()
        self.pulse()
        rc, alerts, notes, tick = self.tick()
        self.assertEqual(0, tick["idle"]["exit"], tick["idle"])
        self.assertEqual([], self.mine(alerts))
        self.assertEqual([], self.idle_rows())

    def test_an_armed_look_that_cannot_answer_is_a_no_data_finding_never_silence(self):
        self.arm()
        self.says(None)                     # the pool's dry look fails
        self.pulse(minutes_ago=90)
        rc, alerts, notes, tick = self.tick()
        nd = [f for f in alerts if f.startswith("NO-DATA: the idle with work look")]
        self.assertEqual(1, len(nd), alerts)
        self.assertEqual([], self.idle_findings(alerts))
        self.assertEqual([], self.idle_rows())

    def test_a_look_that_times_out_or_exits_oddly_is_a_no_data_finding(self):
        self.arm()
        sleeper = os.path.join(self.box, "sleeper.py")
        odd = os.path.join(self.box, "odd.py")
        with open(sleeper, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(30)\n")
        with open(odd, "w", encoding="utf-8") as fh:
            fh.write("import sys\nprint('boom\\nHEALTHY forged')\nsys.exit(7)\n")
        with unittest.mock.patch.object(T, "LOOP_WATCH", sleeper), unittest.mock.patch.object(T, "IDLE_TIMEOUT_S", 1):
            look = T.run_idle_look()
        self.assertEqual((None, True), (look["exit"], look["line"].startswith("NO-DATA")), look)
        with unittest.mock.patch.object(T, "LOOP_WATCH", odd):
            look = T.run_idle_look()
        self.assertIsNone(look["exit"])
        self.assertNotIn("\n", look["line"])

    def test_a_frozen_code_root_is_no_data_and_runs_nothing(self):
        self.arm(BROTHER_CODE_ROOT=self.box)
        self.pulse(minutes_ago=90)
        look = T.run_idle_look()
        self.assertIsNone(look["exit"])
        self.assertEqual([], self.idle_rows())
        self.assertEqual([], self.pool_calls())


if __name__ == "__main__":
    unittest.main()
