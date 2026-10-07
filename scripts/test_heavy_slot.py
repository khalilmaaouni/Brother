"""Tests for heavy_slot.py and for the two gates that enter through it.

Real processes and real file locks, never mocks: the property under test is
that two processes on one machine do not run heavy work at the same time, and
a mock of flock would prove nothing about that. Every test uses its own slot
directory, so the machine's live pool is never touched and a busy machine
cannot make this suite wait. Each guard has its own fixture that only that
guard can refuse.
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
SLOT = os.path.join(HERE, "heavy_slot.py")
sys.path.insert(0, HERE)
import heavy_slot  # noqa: E402  (its load ceiling and start count are read directly by TheLoadCeiling)

import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager can copy this test without scripts/tmp_sandbox.py beside it.
    # The sandbox is hygiene, not the subject.
    sys.stderr.write("test_heavy_slot: tmp_sandbox.py not found; temp trees may be left behind\n")

# Writes "<start> <end>" epoch seconds to argv[1] around a sleep of argv[2].
TIMED = ("import sys,time; s=time.time(); time.sleep(float(sys.argv[2])); "
         "open(sys.argv[1],'w').write('%f %f' % (s, time.time()))")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="heavy-slot-test-")
        self.slots = os.path.join(self.tmp, "slots")
        self.holders = []

    def tearDown(self):
        for proc in self.holders:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass  # sbe: allow-silent the holder already ended
            proc.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, n=1, **extra):
        env = dict(os.environ)
        env.pop("BROTHER_HEAVY_SLOT_HELD", None)
        env.pop("BROTHER_HEAVY_SLOT", None)
        env["BROTHER_SLOT_DIR"] = self.slots
        env["LOCAL_SLOTS"] = str(n)
        # the machine's real load is not this file's subject: pin it idle unless a case sets its own reading
        env["BROTHER_LOAD_READING_FILE"] = self.load_file(0.0, name="load-idle")
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def load_file(self, value, name="load"):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(str(value))
        return path

    def timed(self, name, seconds):
        return [sys.executable, SLOT, sys.executable, "-c", TIMED,
                os.path.join(self.tmp, name), str(seconds)]

    def span(self, name):
        with open(os.path.join(self.tmp, name)) as fh:
            start, end = fh.read().split()
        return float(start), float(end)

    def hold(self, n=1, seconds=30):
        """Occupy one slot with a long-running holder and wait until it holds."""
        proc = subprocess.Popen(
            [sys.executable, SLOT, sys.executable, "-c", "import time; time.sleep(%d)" % seconds],
            env=self.env(n), start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.holders.append(proc)
        deadline = time.time() + 10
        while time.time() < deadline:
            for i in range(n):
                path = os.path.join(self.slots, "slot-%d" % i)
                if os.path.exists(path):
                    with open(path) as fh:
                        if ("pid %d" % proc.pid) in fh.read():
                            return proc
            time.sleep(0.05)
        self.fail("holder never took a slot")

    def run_slot(self, cmd, env, timeout=20):
        started = time.time()
        proc = subprocess.run([sys.executable, SLOT] + cmd, env=env, timeout=timeout,
                              capture_output=True, text=True)
        return proc.returncode, proc.stderr, time.time() - started


class TheQueue(Base):
    def test_a_second_holder_waits_until_the_first_has_finished(self):
        env = self.env(n=1)
        first = subprocess.Popen(self.timed("a", 1.2), env=env)
        time.sleep(0.3)
        second = subprocess.Popen(self.timed("b", 0.1), env=env, stderr=subprocess.PIPE)
        self.assertEqual(first.wait(timeout=20), 0)
        self.assertEqual(second.wait(timeout=20), 0)
        _, a_end = self.span("a")
        b_start, _ = self.span("b")
        self.assertGreaterEqual(b_start, a_end - 0.05,
                                "the second run started before the first ended: the slot did not hold")

    def test_two_slots_let_two_runs_overlap(self):
        env = self.env(n=2)
        first = subprocess.Popen(self.timed("a", 1.2), env=env)
        time.sleep(0.3)
        second = subprocess.Popen(self.timed("b", 0.1), env=env)
        first.wait(timeout=20)
        second.wait(timeout=20)
        _, a_end = self.span("a")
        b_start, _ = self.span("b")
        self.assertLess(b_start, a_end, "two free slots still serialised the runs")

    def test_the_exit_code_comes_back_unchanged(self):
        code, _, _ = self.run_slot([sys.executable, "-c", "raise SystemExit(3)"], self.env())
        self.assertEqual(code, 3)

    def test_a_released_slot_names_nobody(self):
        code, _, _ = self.run_slot([sys.executable, "-c", "pass"], self.env())
        self.assertEqual(code, 0)
        with open(os.path.join(self.slots, "slot-0")) as fh:
            self.assertEqual(fh.read(), "", "a free slot still carries its last holder's label")

    def test_a_missing_command_is_127(self):
        code, err, _ = self.run_slot(["/nonexistent/heavy-slot-command"], self.env())
        self.assertEqual(code, 127)
        self.assertIn("cannot start", err)

    def test_no_command_is_a_usage_error(self):
        code, err, _ = self.run_slot([], self.env())
        self.assertEqual(code, 2)
        self.assertIn("usage", err)


class TheEdges(Base):
    def test_a_nested_run_does_not_wait_behind_its_parent(self):
        self.hold()
        code, _, elapsed = self.run_slot(
            [sys.executable, "-c", "pass"], self.env(BROTHER_HEAVY_SLOT_HELD="0",
                                                     BROTHER_HEAVY_SLOT_WAIT=15))
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 5, "a nested run waited for a second slot")

    def test_off_runs_at_once_when_every_slot_is_busy(self):
        self.hold()
        code, _, elapsed = self.run_slot(
            [sys.executable, "-c", "pass"], self.env(BROTHER_HEAVY_SLOT="off",
                                                     BROTHER_HEAVY_SLOT_WAIT=15))
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 5, "BROTHER_HEAVY_SLOT=off still queued")

    def waits(self):
        with open(os.path.join(self.slots, "waits.jsonl")) as fh:
            return [json.loads(line) for line in fh]

    def test_an_admission_records_its_wait(self):
        code, _, _ = self.run_slot([sys.executable, "-c", "pass"], self.env())
        self.assertEqual(code, 0)
        rows = self.waits()
        self.assertEqual([r["outcome"] for r in rows], ["admitted"])
        self.assertGreaterEqual(rows[0]["waited_s"], 0)

    def test_an_unqueued_run_records_its_wait_too(self):
        self.hold()
        self.run_slot([sys.executable, "-c", "pass"], self.env(BROTHER_HEAVY_SLOT_WAIT=0.5))
        rows = [r for r in self.waits() if r["outcome"] == "unqueued"]
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["waited_s"], 0.5)

    def test_a_full_pool_past_the_bound_runs_unqueued_and_says_so(self):
        self.hold()
        code, err, elapsed = self.run_slot(
            [sys.executable, "-c", "pass"], self.env(BROTHER_HEAVY_SLOT_WAIT=0.5))
        self.assertEqual(code, 0)
        self.assertIn("waiting for 1 of 1 slots", err)
        self.assertIn("UNQUEUED", err)
        self.assertLess(elapsed, 10)

    def test_an_unusable_slot_directory_runs_unqueued_and_says_so(self):
        blocker = os.path.join(self.tmp, "not-a-dir")
        open(blocker, "w").close()
        env = self.env()
        env["BROTHER_SLOT_DIR"] = os.path.join(blocker, "slots")
        code, err, _ = self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertEqual(code, 0)
        self.assertIn("unusable", err)
        self.assertIn("UNQUEUED", err)

    def test_a_killed_holder_frees_its_slot_by_itself(self):
        holder = self.hold()
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait()
        code, err, elapsed = self.run_slot(
            [sys.executable, "-c", "pass"], self.env(BROTHER_HEAVY_SLOT_WAIT=15))
        self.assertEqual(code, 0)
        self.assertNotIn("UNQUEUED", err)
        self.assertLess(elapsed, 5, "a dead holder's slot was not freed")

    def test_sigterm_reaches_the_child_and_its_code_comes_back(self):
        marker = os.path.join(self.tmp, "got-term")
        ready = os.path.join(self.tmp, "ready")
        child = ("import signal,sys,time\n"
                 "def h(*a):\n    open(%r,'w').write('got'); sys.exit(7)\n"
                 "signal.signal(signal.SIGTERM, h)\n"
                 "open(%r,'w').write('ready')\n"
                 "time.sleep(20)\n") % (marker, ready)
        proc = subprocess.Popen([sys.executable, SLOT, sys.executable, "-c", child],
                                env=self.env())
        deadline = time.time() + 10
        while not os.path.exists(ready) and time.time() < deadline:
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=10), 7)
        self.assertTrue(os.path.exists(marker), "the child never saw SIGTERM")

    def test_a_bad_slot_count_falls_back_and_says_so(self):
        code, err, _ = self.run_slot([sys.executable, "-c", "pass"],
                                     self.env(n="zero"))
        self.assertEqual(code, 0)
        self.assertIn("not a positive integer", err)


class TheLoadCeiling(Base):
    """Owner 2026-09-27: "optimize it to perfection to not be a hog". One reading per case, through the file seam."""

    def ceiling(self):
        return float(max(1, (os.cpu_count() or 2) - heavy_slot.resource_gate.CORE_RESERVE))

    def test_a_load_with_room_for_the_run_starts_at_once(self):
        env = self.env(1, BROTHER_LOAD_READING_FILE=self.load_file(self.ceiling() - 1))
        code, err, secs = self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertEqual(code, 0)
        self.assertNotIn("load ceiling", err)
        self.assertLess(secs, 5)

    def test_a_load_at_the_ceiling_waits_and_says_why(self):
        env = self.env(1, BROTHER_LOAD_READING_FILE=self.load_file(self.ceiling()), BROTHER_HEAVY_SLOT_WAIT=2)
        code, err, secs = self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertEqual(code, 0)
        self.assertIn("would pass the ceiling", err)
        self.assertIn("UNQUEUED", err)
        self.assertGreaterEqual(secs, 2)

    def test_a_falling_load_admits_the_waiter(self):
        path = self.load_file(self.ceiling() + 3)
        env = self.env(1, BROTHER_LOAD_READING_FILE=path, BROTHER_HEAVY_SLOT_WAIT=30)
        proc = subprocess.Popen([sys.executable, SLOT, sys.executable, "-c", "pass"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(2)
        self.assertIsNone(proc.poll(), "it started over the ceiling")
        self.load_file(0.0)
        out, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("UNQUEUED", err)

    def test_a_run_admitted_seconds_ago_counts_before_load1_shows_it(self):
        # load1 leaves room for exactly one run; slot 1 carries a start 5 s old, so this run (taking slot 0) must wait
        os.makedirs(self.slots, exist_ok=True)
        with open(os.path.join(self.slots, "slot-1"), "w") as fh:
            fh.write("pid 1 start %d: earlier run\n" % (time.time() - 5))
        env = self.env(2, BROTHER_LOAD_READING_FILE=self.load_file(self.ceiling() - 1), BROTHER_HEAVY_SLOT_WAIT=2)
        code, err, _secs = self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertIn("1 run(s) admitted", err)
        self.assertIn("UNQUEUED", err)

    def test_an_old_start_is_already_in_load1_and_is_not_counted_twice(self):
        os.makedirs(self.slots, exist_ok=True)
        with open(os.path.join(self.slots, "slot-1"), "w") as fh:
            fh.write("pid 1 start %d: earlier run\n" % (time.time() - 300))
        env = self.env(2, BROTHER_LOAD_READING_FILE=self.load_file(self.ceiling() - 1))
        code, err, secs = self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertNotIn("load ceiling", err)
        self.assertLess(secs, 5)

    def test_an_unreadable_load_waits_and_is_never_read_as_idle(self):
        env = self.env(1, BROTHER_LOAD_READING_FILE=self.load_file("not a number"), BROTHER_HEAVY_SLOT_WAIT=2)
        code, err, secs = self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertIn("load unreadable", err)
        self.assertGreaterEqual(secs, 2)

    def test_a_waiter_that_backs_off_leaves_no_start_behind(self):
        env = self.env(1, BROTHER_LOAD_READING_FILE=self.load_file(self.ceiling() + 3), BROTHER_HEAVY_SLOT_WAIT=2)
        self.run_slot([sys.executable, "-c", "pass"], env)
        self.assertEqual(heavy_slot.recent_starts(self.slots, 1), 0)

    def test_the_ceiling_can_be_lowered_never_raised(self):
        top = self.ceiling()
        self.assertEqual(heavy_slot.load_ceiling({}), top)
        self.assertEqual(heavy_slot.load_ceiling({"BROTHER_LOAD_CEILING": "1.5"}), 1.5)
        self.assertEqual(heavy_slot.load_ceiling({"BROTHER_LOAD_CEILING": str(top + 10)}), top)
        self.assertEqual(heavy_slot.load_ceiling({"BROTHER_LOAD_CEILING": "zero"}), top)

    def test_an_admitted_run_starts_at_a_lower_priority(self):
        code, err, _ = self.run_slot(
            [sys.executable, "-c", "import os, sys; sys.exit(0 if os.nice(0) == %d else 1)" % min(19, os.nice(0) + 5)],
            self.env(1))
        self.assertEqual(code, 0, err)


class TheMeasuredSignal(unittest.TestCase):
    """The ceiling is judged on cores measured busy, not on load1, wherever the kernel says (2026-09-27: load1 read
    5.10 on this M3 while ticks and top both said 3.4 to 4.5 cores busy, so a load1 gate held four idle cores back)."""

    def setUp(self):
        self.saved = heavy_slot.resource_gate.cpu_ticks

    def tearDown(self):
        heavy_slot.resource_gate.cpu_ticks = self.saved

    def feed(self, readings):
        it = iter(readings)
        heavy_slot.resource_gate.cpu_ticks = lambda: next(it)

    def test_busy_cores_come_from_the_tick_difference(self):
        self.assertEqual(heavy_slot.busy_between((100, 1000), (150, 1100), 8), 4.0)
        self.assertIsNone(heavy_slot.busy_between((100, 1000), (150, 1000), 8))
        self.assertIsNone(heavy_slot.busy_between(None, (150, 1100), 8))

    def test_the_measured_figure_is_used_and_uncovers_a_new_run_for_15_s(self):
        self.feed([(100, 800)])
        load, basis, window = heavy_slot.machine_load({}, {"ticks": (0, 0)})
        self.assertEqual((basis, window), ("cores busy", heavy_slot.RECENT_TICKS_S))
        self.assertAlmostEqual(load, 100 / 800.0 * (os.cpu_count() or 1))

    def test_the_busiest_of_the_last_three_samples_is_judged(self):
        cores = os.cpu_count() or 1
        self.feed([(80, 100), (90, 200), (100, 300), (110, 400)])   # busy fractions 0.8 then 0.1, 0.1, 0.1
        state = {"ticks": (0, 0)}
        got = [heavy_slot.machine_load({}, state)[0] for _ in range(4)]
        self.assertAlmostEqual(got[2], 0.8 * cores)       # the 0.8 second is still inside the window
        self.assertAlmostEqual(got[3], 0.1 * cores)       # and has left it one sample later

    def test_no_ticks_falls_back_to_load1_and_its_minute(self):
        self.feed([None])
        _load, basis, window = heavy_slot.machine_load({}, {"ticks": None})
        self.assertEqual((basis, window), ("load1", heavy_slot.RECENT_S))

    def test_the_seam_file_wins_over_the_kernel(self):
        self.feed([])   # never consulted
        path = tempfile.mkstemp(prefix="load-seam-")[1]
        self.addCleanup(os.remove, path)
        with open(path, "w") as fh:
            fh.write("2.5")
        self.assertEqual(heavy_slot.machine_load({"BROTHER_LOAD_READING_FILE": path}, {}), (2.5, "load1", heavy_slot.RECENT_S))

    def test_this_machine_reports_its_ticks(self):
        t = self.saved()
        if t is None:
            self.skipTest("NO-DATA: this platform gives no CPU ticks; the gate falls back to load1 here")
        self.assertTrue(0 <= t[0] <= t[1], t)


class TheWeight(Base):
    """ACC2: a command that runs k things side by side takes k slots, all or
    nothing, so a parallel gate cannot re-create the pile-up the queue removed."""

    HELD = [sys.executable, "-c", "import os; print(os.environ.get('BROTHER_HEAVY_SLOT_HELD'))"]

    def held_by(self, env):
        proc = subprocess.run([sys.executable, SLOT] + self.HELD, env=env, timeout=20,
                              capture_output=True, text=True)
        return proc.stdout.strip(), proc.stderr

    def test_a_weight_takes_that_many_slots(self):
        out, _ = self.held_by(self.env(n=3, BROTHER_HEAVY_SLOT_WEIGHT=2))
        self.assertEqual(out, "0,1")

    def test_a_weighted_run_never_starts_on_part_of_its_slots(self):
        self.hold(n=2)
        code, err, _ = self.run_slot([sys.executable, "-c", "pass"],
                                     self.env(n=2, BROTHER_HEAVY_SLOT_WEIGHT=2,
                                              BROTHER_HEAVY_SLOT_WAIT=0.8))
        self.assertEqual(code, 0)
        self.assertIn("waiting for 2 of 2 slots", err)
        self.assertIn("UNQUEUED", err, "it started holding one slot of the two it needs")

    def test_a_weighted_waiter_holds_nothing_while_it_waits(self):
        self.hold(n=2)
        waiter = subprocess.Popen([sys.executable, SLOT, sys.executable, "-c", "pass"],
                                  env=self.env(n=2, BROTHER_HEAVY_SLOT_WEIGHT=2,
                                               BROTHER_HEAVY_SLOT_WAIT=20),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.holders.append(waiter)
        time.sleep(0.5)
        code, err, elapsed = self.run_slot([sys.executable, "-c", "pass"],
                                           self.env(n=2, BROTHER_HEAVY_SLOT_WAIT=15))
        self.assertEqual(code, 0)
        self.assertNotIn("UNQUEUED", err)
        self.assertLess(elapsed, 6, "a waiting weighted run kept the free slot to itself")

    def test_a_weight_above_the_pool_is_capped(self):
        out, err = self.held_by(self.env(n=1, BROTHER_HEAVY_SLOT_WEIGHT=3))
        self.assertEqual(out, "0")
        self.assertIn("taking all 1", err)

    def test_a_bad_weight_falls_back_to_one_and_says_so(self):
        out, err = self.held_by(self.env(n=2, BROTHER_HEAVY_SLOT_WEIGHT="many"))
        self.assertEqual(out, "0")
        self.assertIn("not a positive integer", err)


class TheGatesEnterThroughIt(Base):
    """The control lives at the gates' entry points, so it is tested there: the
    fixture runs the gates' OWN opening lines, cut from the real files."""

    def fixture(self, gate, with_slot=True):
        with open(os.path.join(HERE, gate)) as fh:
            source = fh.read()
        wiring = 'exec python3 scripts/heavy_slot.py sh "scripts/%s" "$@"\nfi\n' % gate
        self.assertIn(wiring, source, "%s no longer enters through heavy_slot.py" % gate)
        head = source[:source.index(wiring) + len(wiring)]
        root = tempfile.mkdtemp(dir=self.tmp)
        scripts = os.path.join(root, "scripts")
        os.makedirs(scripts)
        if with_slot:
            for name in ("heavy_slot.py", "brother_paths.py", "resource_gate.py"):
                shutil.copyfile(os.path.join(HERE, name), os.path.join(scripts, name))
        path = os.path.join(scripts, gate)
        with open(path, "w") as fh:
            fh.write(head + 'echo "held=${BROTHER_HEAVY_SLOT_HELD:-none}"\n')
        return path

    def gate(self, path, env):
        proc = subprocess.run(["sh", path], env=env, capture_output=True, text=True, timeout=20)
        return proc.stdout, proc.stderr

    def test_both_gates_run_holding_a_slot(self):
        for gate in ("required_fast.sh", "check_all.sh"):
            with self.subTest(gate=gate):
                out, _ = self.gate(self.fixture(gate), self.env())
                self.assertIn("held=0", out)

    def test_a_busy_machine_makes_the_gate_wait_in_line(self):
        self.hold()
        out, err = self.gate(self.fixture("required_fast.sh"),
                             self.env(BROTHER_HEAVY_SLOT_WAIT=0.5))
        self.assertIn("waiting for 1 of 1 slots", err)
        self.assertIn("held=unqueued", out)

    def test_the_fast_gate_reserves_one_slot_per_check_it_runs_at_once(self):
        out, _ = self.gate(self.fixture("required_fast.sh"),
                           self.env(n=4, REQUIRED_FAST_JOBS=3))
        self.assertIn("held=0,1,2", out)

    def test_a_fixture_copy_without_the_queue_runs_straight_through(self):
        out, _ = self.gate(self.fixture("required_fast.sh", with_slot=False), self.env())
        self.assertIn("held=none", out)


if __name__ == "__main__":
    unittest.main()
