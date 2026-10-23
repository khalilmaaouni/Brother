#!/usr/bin/env python3
"""LA5.a: the room is also capped by the native seats divided by the workers per round, the BUDGET line
prints the seat count, and an unreadable seat count or workers value admits nothing (REQ-LA5-A-01 to A-06).
This suite lives beside the module it tests and is the sub unit's own done check. It loads runner_pool.py
with sys.modules stand ins for its sibling modules, so no process is started and no network is touched."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(HERE, "runner_pool.py")

_ROOT = None
_HOME = None
_WBS = None
_POOL = None
_SAVED = {}

HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())


class _CP(object):
    """A stand in for subprocess.CompletedProcess."""
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeSubprocess(object):
    """The pool's subprocess use, replaced: this suite starts no process at all."""
    SubprocessError = Exception

    def run(self, argv, **kw):
        if any("burn_guard" in str(x) for x in argv):
            return _CP("5\n", "", 0)
        return _CP("", "", 0)

    def Popen(self, argv, **kw):
        raise AssertionError("a --dry pass starts no runner")


class _FakePlanPath(object):
    def selected(self):
        return _WBS


class _FakePlanStore(object):
    def sub_landed(self, sub, evidence=""):
        return False

    def release_refusal(self, state):
        return None

    def supply_hold(self, sub):
        return ""


class _FakeSpecCheck(object):
    def gate_refusal(self, text, sub):
        return ""


class _FakePlanLint(object):
    COUNCIL_PATH = "/nonexistent/council.json"

    def lint_mode(self):
        return ("report", "")

    def admission(self, plan, unit, sub, root, env, council=None):
        return ([], "")


class _FakeBriefCheck(object):
    SPEC_PATH = None


class _FakeProofLedger(object):
    def draining(self):
        return False


class _FakeUnitLedger(object):
    """The two names runner_pool reads from unit_ledger: fail_class is imported at module level, so a
    stand in without it would break the load with ImportError rather than exercising the seat arithmetic."""
    def fail_class(self, note):
        return "SUITE"

    def last_rows(self, path):
        return {}


class _FakeLoopSwitches(object):
    """What the pool asks before it loads the Claude seat machinery: on when BROTHER_PIN_MODEL is set."""
    def native_on(self, env, which=None):
        try:
            pin = env.get("BROTHER_PIN_MODEL")
        except AttributeError:
            return False
        return bool(isinstance(pin, str) and pin.strip())


class _FakeNativeWorker(object):
    """The contract of native_worker.seats(): unset means DEFAULT_SEATS, anything else refuses ValueError."""
    DEFAULT_SEATS = 2

    def seats(self, env=None):
        source = os.environ if env is None else env
        raw = str(source.get("BROTHER_NATIVE_SEATS", "")).strip()
        if not raw:
            return self.DEFAULT_SEATS
        if not raw.isdigit() or int(raw) < 1:
            raise ValueError("BROTHER_NATIVE_SEATS must be a positive whole number, not %r" % raw)
        return int(raw)


def _setup():
    global _ROOT, _HOME, _WBS
    if _ROOT is not None:
        return
    _ROOT = tempfile.mkdtemp(prefix="la5a-seats-")
    _HOME = os.path.join(_ROOT, "home")
    os.makedirs(os.path.join(_HOME, ".claude", "evidence"))
    specs = os.path.join(_ROOT, "specs")
    os.makedirs(specs)
    units = []
    scores = {}
    for letter in ("UA", "UB", "UC", "UD"):
        sub = letter + ".a"
        spec = os.path.join(specs, letter + ".md")
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write("# %s\n\nDone check: `python3 -B -m unittest nothing`\n" % letter)
        units.append({"id": letter, "state": "OPEN", "spec": spec, "sub_units": [sub], "evidence": ""})
        scores[sub] = {"score": 9}
    _WBS = os.path.join(_ROOT, "wbs.json")
    with open(_WBS, "w", encoding="utf-8") as fh:
        json.dump({"units": units}, fh)
    with open(os.path.join(_HOME, ".claude", "evidence", "spec-scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh)
    os.environ["HOME"] = _HOME
    os.environ["BROTHER_SCOPE"] = "."
    # The grader's own environment may carry BROTHER_WIP=2; the room must be decided by the caps under test.
    os.environ["BROTHER_WIP"] = "5"
    for name in ("BROTHER_PIN_MODEL", "BROTHER_NATIVE_SEATS", "WORKERS_PER_ROUND"):
        os.environ.pop(name, None)


def _load():
    global _POOL
    if _POOL is not None:
        return _POOL
    _setup()
    for name, module in (("plan_path", _FakePlanPath()), ("plan_store", _FakePlanStore()),
                         ("plan_lint", _FakePlanLint()), ("spec_check", _FakeSpecCheck()),
                         ("brief_check", _FakeBriefCheck()), ("unit_ledger", _FakeUnitLedger()),
                         ("proof_ledger", _FakeProofLedger()), ("loop_switches", _FakeLoopSwitches()),
                         ("native_worker", _FakeNativeWorker())):
        _SAVED[name] = sys.modules.get(name)
        sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("runner_pool_under_la5a", POOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.subprocess = _FakeSubprocess()
    module.reconcile_dead = lambda d, pid: None
    module._pid_alive = lambda pid: False
    module.alive_runners = lambda runs, plan: []
    module.remaining_of = lambda u: 0
    module.busy_paths = lambda alive, spec_of: ({}, [])
    module.hold_reason = lambda sub: None
    module.waits_to_land = lambda word: False
    module._real_still_parked = module.still_parked
    module._real_parked_fact = module.parked_fact
    module.still_parked = lambda *a, **k: False
    module.parked_fact = lambda *a, **k: 0.0
    module.sunk = lambda rec: False
    module.reseated_too_soon = lambda word, at, now: False
    module.ranked = lambda units, rec, ip, explore, buildable: list(units)
    module.unit_record = lambda rows: {}
    module.touch_set = lambda sub, spec: set()
    module.write_set = lambda sub, spec: []
    module.reviewed_route = lambda ws: ""
    module.touch_conflict = lambda ts, busy, unknown: ""
    module.admissible = lambda plan, sub: ""
    module.finish_first_hold = lambda *a, **k: ""
    module.wip_mode = lambda value: "report"
    module.wip_reading = lambda ref, main_ref: ([], 0)
    module.wip_line = lambda rows, code, mode: "WIP     test"
    module.write_wip_record = lambda rows, code, mode: None
    _POOL = module
    return module


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    if _ROOT and os.path.isdir(_ROOT):
        shutil.rmtree(_ROOT, ignore_errors=True)


class _PoolCase(unittest.TestCase):
    def setUp(self):
        self.pool = _load()

    def _run_pool(self, extra=None):
        saved = {}
        for key, value in (extra or {}).items():
            saved[key] = os.environ.get(key)
            os.environ[key] = value
        argv = sys.argv
        buf = io.StringIO()
        try:
            sys.argv = ["runner_pool.py", "--dry"]
            with contextlib.redirect_stdout(buf):
                self.pool.main()
        finally:
            sys.argv = argv
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return buf.getvalue()


class _RoomOfTests(_PoolCase):
    def test_room_of_names_the_smaller_cap(self):
        pool = self.pool
        self.assertEqual(pool.room_of(5, 5, None), (5, "budget cap"))
        self.assertEqual(pool.room_of(3, 5, None), (3, "budget cap"))
        self.assertEqual(pool.room_of(5, 3, None), (3, "WIP cap"))
        self.assertEqual(pool.room_of(3, 3, None), (3, "budget cap"))
        self.assertEqual(pool.room_of(5, 5, 3), (3, "no seat"))
        self.assertEqual(pool.room_of(3, 5, 5), (3, "budget cap"))
        self.assertEqual(pool.room_of(5, 3, 5), (3, "WIP cap"))
        self.assertEqual(pool.room_of(5, 3, 3), (3, "WIP cap"))
        self.assertEqual(pool.room_of(-2, 5, 5), (0, "budget cap"))
        self.assertEqual(pool.room_of(5, -2, 5), (0, "WIP cap"))
        self.assertEqual(pool.room_of(5, 5, -2), (0, "no seat"))
        self.assertEqual(pool.room_of(0, 0, 0), (0, "budget cap"))
        self.assertEqual(pool.room_of(5, 5, 5), (5, "budget cap"))

    def test_room_of_refuses_non_integers(self):
        pool = self.pool
        bads = (0.5, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
        for bad in (None,) + bads:
            with self.assertRaises(ValueError):
                pool.room_of(bad, 5, 1)
            with self.assertRaises(ValueError):
                pool.room_of(5, bad, 1)
        for bad in bads:
            with self.assertRaises(ValueError):
                pool.room_of(5, 5, bad)
        for flag in (True, False):
            with self.assertRaises(ValueError):
                pool.room_of(flag, 5, 1)
            with self.assertRaises(ValueError):
                pool.room_of(5, flag, 1)
            with self.assertRaises(ValueError):
                pool.room_of(5, 5, flag)
        self.assertEqual(pool.room_of(5, 5, None), (5, "budget cap"))


class _SeatCapTests(_PoolCase):
    def test_fewer_seats_than_workers_still_admits_one_runner(self):
        env = {"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "2", "WORKERS_PER_ROUND": "6"}
        self.assertEqual(self.pool.seat_cap(env), 1)
        out = self._run_pool({"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "2",
                              "WORKERS_PER_ROUND": "6"})
        self.assertEqual(out.count(" START"), 1, out)
        self.assertIn("skip: no seat", out)
        self.assertIn("room for 1", out)

    def test_seat_cap_answers_none_when_the_pass_is_not_native(self):
        self.assertIsNone(self.pool.seat_cap({"BROTHER_PIN_MODEL": ""}))
        self.assertIsNone(self.pool.seat_cap({}))
        self.assertIsNone(self.pool.seat_cap({"BROTHER_PIN_MODEL": "   "}))
        self.assertEqual(self.pool.seat_cap({"BROTHER_PIN_MODEL": "opus55"}), 1)


class _PoolMainSeatTests(_PoolCase):
    def test_four_seats_two_workers_admit_two_runners_and_refuse_the_third_as_no_seat(self):
        out = self._run_pool({"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "4",
                              "WORKERS_PER_ROUND": "2"})
        self.assertEqual(out.count(" START"), 2, out)
        self.assertIn("skip: no seat", out)
        self.assertIn("room for 2", out)

    def test_two_seats_two_workers_admit_one_runner(self):
        out = self._run_pool({"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "2",
                              "WORKERS_PER_ROUND": "2"})
        self.assertEqual(out.count(" START"), 1, out)
        self.assertIn("skip: no seat", out)
        self.assertIn("room for 1", out)

    def test_native_off_leaves_the_budget_cap_alone(self):
        out = self._run_pool({"BROTHER_PIN_MODEL": ""})
        self.assertIn("native seats off", out)
        self.assertEqual(out.count(" START"), 4, out)
        self.assertNotIn("skip: no seat", out)
        self.assertIn("room for 5", out)

    def test_unreadable_seat_count_admits_nothing_and_says_so(self):
        out = self._run_pool({"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "abc",
                              "WORKERS_PER_ROUND": "2"})
        self.assertIn("native seats NO-DATA", out)
        self.assertNotIn(" START", out)
        self.assertIn("room for 0", out)

    def test_unreadable_workers_per_round_admits_nothing(self):
        out = self._run_pool({"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "4",
                              "WORKERS_PER_ROUND": "abc"})
        self.assertIn("native seats NO-DATA", out)
        self.assertNotIn(" START", out)
        self.assertIn("room for 0", out)

    def test_budget_line_prints_the_seat_count(self):
        out = self._run_pool({"BROTHER_PIN_MODEL": "opus55", "BROTHER_NATIVE_SEATS": "4",
                              "WORKERS_PER_ROUND": "2"})
        self.assertIn("native seats 4, 2 worker(s) per round, seat cap 2 runner(s)", out)
        self.assertIn("room for 2", out)


class _HostileInputTests(_PoolCase):
    def test_new_functions_never_crash_on_hostile_input(self):
        pool = self.pool
        for value in HOSTILE:
            for args in ((value,), (value, value)):
                try:
                    pool.seat_cap(*args)
                except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                    pass
            for args in ((value, value), (value, value, value)):
                try:
                    pool.room_of(*args)
                except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                    pass


class _SeatStampTests(_PoolCase):
    """I3.a: the driver writes a seat root stamp under a driver-controlled root and the pass names it in the
    process environment (REQ-I3-A01 to A-05)."""

    def setUp(self):
        super().setUp()
        self._root = None
        self._saved = {}
        for name in ("BROTHER_SEAT_ROOT", "BROTHER_LAUNCH_STAMP"):
            self._saved[name] = os.environ.get(name)
            os.environ.pop(name, None)
        self._root = tempfile.mkdtemp(prefix="i3a-seat-")
        self._seat = os.path.join(self._root, "seat-root")
        os.makedirs(self._seat)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self._root and os.path.isdir(self._root):
            shutil.rmtree(self._root, ignore_errors=True)
        super().tearDown()

    def test_the_writer_sets_the_stamp_from_the_seat_environment(self):
        seat = os.path.realpath(self._seat)
        env = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_PIN_MODEL": "opus55"}
        path = self.pool.write_launch_stamp(env)
        self.assertEqual(path, os.path.join(seat, "launch-stamp"))
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.pool.seat_fingerprint(env))
        # A caller that passed its own mapping leaves the process environment alone.
        self.assertIsNone(os.environ.get("BROTHER_LAUNCH_STAMP"))
        # The pass itself writes the same stamp through main() and names it in the process environment.
        expected = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_PIN_MODEL": "opus55",
                    "BROTHER_NATIVE_SEATS": "4", "WORKERS_PER_ROUND": "2"}
        os.unlink(path)
        out = self._run_pool(expected)
        self.assertIn("BUDGET", out)
        self.assertTrue(os.path.isfile(path), out)
        self.assertEqual(os.environ.get("BROTHER_LAUNCH_STAMP"), path)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.pool.seat_fingerprint(expected))

    def test_the_writer_leaves_the_mtime_alone_when_the_seat_root_is_unchanged(self):
        env = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_PIN_MODEL": "opus55"}
        path = self.pool.write_launch_stamp(env)
        self.assertTrue(path)
        os.utime(path, (946684800.0, 946684800.0))
        before = self.pool.launch_stamp_time(env=env)
        self.assertEqual(before, 946684800.0)
        # The same launch environment writes nothing, so the mtime does not move.
        self.assertEqual(self.pool.write_launch_stamp(env), path)
        self.assertEqual(self.pool.launch_stamp_time(env=env), before)
        # A change to one of the four launch names is a new fact and moves it.
        changed = dict(env)
        changed["BROTHER_PIN_MODEL"] = "sonnet"
        self.assertEqual(self.pool.write_launch_stamp(changed), path)
        self.assertGreater(self.pool.launch_stamp_time(env=env), before)

    def test_the_reader_refuses_a_stamp_outside_the_seat_root(self):
        seat = os.path.realpath(self._seat)
        sibling = self._seat + "-evil"
        os.makedirs(sibling)
        for outside in (os.path.join(self._root, "outside", "launch-stamp"),
                        os.path.join(sibling, "launch-stamp")):
            env = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_LAUNCH_STAMP": outside}
            self.assertEqual(self.pool.launch_stamp_path(env), "", outside)
        # A symlink under the root that resolves outside it is refused too.
        target = os.path.join(self._root, "elsewhere")
        os.makedirs(target)
        link = os.path.join(self._seat, "escape")
        os.symlink(target, link)
        env_link = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_LAUNCH_STAMP": link}
        self.assertEqual(self.pool.launch_stamp_path(env_link), "")
        # A relative name under the root is accepted.
        env_rel = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_LAUNCH_STAMP": "launch-stamp-2"}
        self.assertEqual(self.pool.launch_stamp_path(env_rel), os.path.join(seat, "launch-stamp-2"))
        # A seat root that is the runs directory is not driver controlled.
        env_runs = {"BROTHER_SEAT_ROOT": self.pool.RUNS}
        self.assertEqual(self.pool.seat_root(env_runs), "")
        self.assertEqual(self.pool.launch_stamp_path(env_runs), "")
        # A value that is not a string falls back to the default under the driver's evidence area.
        self.assertTrue(self.pool.seat_root({"BROTHER_SEAT_ROOT": []}))

    def test_the_reader_answers_zero_on_every_doubtful_input(self):
        env = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_PIN_MODEL": "opus55"}
        path = self.pool.write_launch_stamp(env)
        self.assertTrue(path)
        env_runs = {"BROTHER_SEAT_ROOT": self.pool.RUNS}
        self.assertEqual(self.pool.launch_stamp_path(env_runs), "")
        self.assertEqual(self.pool.launch_stamp_time(env=env_runs), 0.0)
        env_missing = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_LAUNCH_STAMP": "no-such-stamp"}
        self.assertEqual(self.pool.launch_stamp_time(env=env_missing), 0.0)
        adir = os.path.join(self._seat, "adir")
        os.makedirs(adir)
        env_dir = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_LAUNCH_STAMP": adir}
        self.assertEqual(self.pool.launch_stamp_time(env=env_dir), 0.0)

        def raises_os(p):
            raise OSError("no")

        def raises_value(p):
            raise ValueError("no")

        def raises_type(p):
            raise TypeError("no")

        def not_a_number(p):
            return "not-a-number"

        for mtime in (raises_os, raises_value, raises_type, not_a_number, None, 0):
            self.assertEqual(self.pool.launch_stamp_time(mtime, env=env), 0.0, "mtime %r" % (mtime,))

    def test_the_reader_answers_the_stamp_mtime(self):
        env = {"BROTHER_SEAT_ROOT": self._seat, "BROTHER_PIN_MODEL": "opus55"}
        path = self.pool.write_launch_stamp(env)
        self.assertTrue(path)
        os.utime(path, (1234567890.0, 1234567890.0))
        answer = self.pool.launch_stamp_time(env=env)
        self.assertIsInstance(answer, float)
        self.assertEqual(answer, 1234567890.0)
        seen = []

        def fake(p):
            seen.append(p)
            return 777.0

        self.assertEqual(self.pool.launch_stamp_time(fake, env=env), 777.0)
        self.assertEqual(seen, [path])

    def test_the_new_functions_refuse_hostile_input(self):
        for value in HOSTILE:
            for fn, arity in ((self.pool.seat_root, 1), (self.pool.launch_stamp_path, 1),
                              (self.pool.seat_fingerprint, 1), (self.pool.write_launch_stamp, 1),
                              (self.pool.launch_stamp_time, 2)):
                try:
                    fn(*([value] * arity))
                except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                    pass


class _ParkedFactSeatTests(_PoolCase):
    """I3.b: a seat root stamp newer than the park readmits a WITHHELD unit and only that, through the real
    parked_fact and still_parked (REQ-I3-B01 to B04)."""

    def setUp(self) -> None:
        super().setUp()
        self._fact_time = self.pool.fact_time
        self._stamp_time = self.pool.launch_stamp_time
        self._stamp_env_name = self.pool.LAUNCH_STAMP_ENV
        self._stamp_env = os.environ.get(self._stamp_env_name)
        os.environ.pop(self._stamp_env_name, None)

    def tearDown(self) -> None:
        self.pool.fact_time = self._fact_time
        self.pool.launch_stamp_time = self._stamp_time
        if self._stamp_env is None:
            os.environ.pop(self._stamp_env_name, None)
        else:
            os.environ[self._stamp_env_name] = self._stamp_env
        super().tearDown()

    def test_a_changed_seat_root_readmits_a_withheld_unit(self) -> None:
        self.pool.fact_time = lambda *a, **k: 100.0
        self.pool.launch_stamp_time = lambda *a, **k: 200.0
        self.assertEqual(self.pool._real_parked_fact("WITHHELD", "UA.a"), 200.0)
        self.assertIs(self.pool._real_still_parked("WITHHELD", 100.0, 200.0), False)

    def test_an_exhausted_unit_stays_parked_when_only_the_stamp_changes(self) -> None:
        self.pool.fact_time = lambda *a, **k: 100.0
        self.pool.launch_stamp_time = lambda *a, **k: 200.0
        self.assertEqual(self.pool._real_parked_fact("EXHAUSTED", "UA.a"), 100.0)
        self.assertIs(self.pool._real_still_parked("EXHAUSTED", 100.0, 100.0), True)

    def test_a_still_parked_answer_comes_from_the_real_function(self) -> None:
        self.assertIsNot(self.pool.parked_fact, self.pool._real_parked_fact)
        self.assertIs(self.pool._real_still_parked("WITHHELD", 300.0, 200.0), True)

    def test_seat_root_fact_refuses_a_non_number(self) -> None:
        for value in (True, False, "100", None, float("nan"), [], {}):
            with self.assertRaises(ValueError):
                self.pool.seat_root_fact("WITHHELD", value)
        self.pool.launch_stamp_time = lambda *a, **k: 200.0
        self.assertEqual(self.pool.seat_root_fact("EXHAUSTED", 100.0), 100.0)
        self.assertEqual(self.pool.seat_root_fact("WITHHELD", 100.0), 200.0)


if __name__ == "__main__":
    unittest.main()
