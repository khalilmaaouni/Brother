#!/usr/bin/env python3
"""D31.a: a diagnosis note newer than the attempt's START re-seats a parked sub unit (register row L18).

The parked test compared a fact with the run folder's LAST write, which moves until the run writes STATUS at its
end, so a note written while the attempt ran never re-seated it (2026-10-14: note 13h46, start 13h44, quarantine
13h56). This suite lives beside the module it tests and is the sub unit's own done check. It loads runner_pool.py
with sys.modules stand ins for its sibling modules, in a temp HOME, so no process is started and no network is touched.

Run: python3 -B scripts/loop/test_runner_pool_note_attempt.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(HERE, "runner_pool.py")

_ROOT = None
_HOME = None
_WBS = None
_POOL = None
_SAVED = {}
_ENV_SAVED = {}
_FACT = [0.0]   # the fact time the stubbed parked_fact answers for the whole pass test


class _CP(object):
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
    def fail_class(self, note):
        return "SUITE"

    def last_rows(self, path):
        return {}


class _FakeLoopSwitches(object):
    def native_on(self, env, which=None):
        return False


class _Stat(object):
    """A stand in for os.stat_result carrying only the fields a test names."""
    def __init__(self, **fields):
        self.__dict__.update(fields)


def _setup():
    global _ROOT, _HOME, _WBS
    if _ROOT is not None:
        return
    _ROOT = tempfile.mkdtemp(prefix="d31-note-attempt-")
    _HOME = os.path.join(_ROOT, "home")
    os.makedirs(os.path.join(_HOME, ".claude", "evidence"))
    spec = os.path.join(_ROOT, "ZZ1.md")
    with open(spec, "w", encoding="utf-8") as fh:
        fh.write("# ZZ1\n\nDone check: `python3 -B -m unittest nothing`\n")
    _WBS = os.path.join(_ROOT, "wbs.json")
    with open(_WBS, "w", encoding="utf-8") as fh:
        json.dump({"units": [{"id": "ZZ1", "state": "OPEN", "spec": spec, "sub_units": ["ZZ1.a"], "evidence": ""}]}, fh)
    with open(os.path.join(_HOME, ".claude", "evidence", "spec-scores.json"), "w", encoding="utf-8") as fh:
        json.dump({"ZZ1.a": {"score": 9}}, fh)
    for name, value in (("HOME", _HOME), ("BROTHER_SCOPE", "."), ("BROTHER_WIP", "5")):
        _ENV_SAVED[name] = os.environ.get(name)
        os.environ[name] = value
    for name in ("BROTHER_PIN_MODEL", "BROTHER_NATIVE_SEATS", "WORKERS_PER_ROUND", "BROTHER_LOAD_CAP", "BROTHER_WIP_GATE"):
        _ENV_SAVED[name] = os.environ.pop(name, None)


def _load():
    """runner_pool.py with stand ins for every step of a pass EXCEPT the parked test under check: still_parked and
    _start_time are the real ones, parked_fact answers _FACT[0]."""
    global _POOL
    if _POOL is not None:
        return _POOL
    _setup()
    for name, module in (("plan_path", _FakePlanPath()), ("plan_store", _FakePlanStore()),
                         ("plan_lint", _FakePlanLint()), ("spec_check", _FakeSpecCheck()),
                         ("brief_check", _FakeBriefCheck()), ("unit_ledger", _FakeUnitLedger()),
                         ("proof_ledger", _FakeProofLedger()), ("loop_switches", _FakeLoopSwitches())):
        _SAVED[name] = sys.modules.get(name)
        sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("runner_pool_under_d31", POOL_PATH)
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
    module.parked_fact = lambda *a, **k: _FACT[0]
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
    for name, value in _ENV_SAVED.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if _ROOT is not None:
        shutil.rmtree(_ROOT, ignore_errors=True)


def _run_folder(parent, word, mtime):
    d = os.path.join(parent, "ZZ1.a-134400")
    os.makedirs(d)
    with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
        fh.write(word + " test\n")
    os.utime(d, (mtime, mtime))
    return d


class StartTimeTests(unittest.TestCase):
    def setUp(self):
        self.rp = _load()
        self.tmp = tempfile.mkdtemp(prefix="d31-unit-", dir=_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_note_written_during_the_attempt_reseats(self):
        # the 10-14 shape: start T, note T+120, last write (quarantine) T+720
        t = 1000000.0
        d = _run_folder(self.tmp, "EXHAUSTED", t + 720)
        fake = lambda p: _Stat(st_birthtime=t, st_mtime=t + 720)
        word = self.rp.status_word(os.path.join(d, "STATUS"))
        self.assertTrue(self.rp.still_parked(word, self.rp._dir_time(d), t + 120), "the base reading parks it")
        self.assertFalse(self.rp.still_parked(word, self.rp._start_time(d, stat=fake), t + 120))

    def test_a_note_older_than_the_start_still_parks(self):
        t = 1000000.0
        d = _run_folder(self.tmp, "EXHAUSTED", t + 720)
        fake = lambda p: _Stat(st_birthtime=t, st_mtime=t + 720)
        for word in ("EXHAUSTED", "WITHHELD", "CONFIG_WAIT"):
            self.assertTrue(self.rp.still_parked(word, self.rp._start_time(d, stat=fake), t - 1))
            self.assertTrue(self.rp.still_parked(word, self.rp._start_time(d, stat=fake), t))

    def test_no_birth_time_reads_the_folder_mtime(self):
        d = _run_folder(self.tmp, "EXHAUSTED", 1234567.0)
        for born in (None, 0, 0.0, -5.0, float("nan"), float("inf"), float("-inf"), True, "1000", [1]):
            with self.subTest(born=born):
                fake = (lambda p: _Stat(st_mtime=1.0)) if born is None else (lambda p, b=born: _Stat(st_birthtime=b))
                self.assertEqual(self.rp._start_time(d, stat=fake), 1234567.0)

    def test_an_unreadable_folder_parks(self):
        def boom(p):
            raise OSError("unreadable")
        self.assertEqual(self.rp._start_time(os.path.join(self.tmp, "gone"), stat=boom), 0.0)
        self.assertEqual(self.rp._start_time(os.path.join(self.tmp, "no-such-folder")), 0.0)
        self.assertTrue(self.rp.still_parked("EXHAUSTED", self.rp._start_time(os.path.join(self.tmp, "gone"), stat=boom), time.time()))

    def test_hostile_paths_are_refused(self):
        for bad in (None, "", 7, 1.5, True, b"x", [], ["x"], {}, object()):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    self.rp._start_time(bad)
        for bad in ("x", 7, [], {}):
            with self.subTest(stat=bad):
                with self.assertRaises(ValueError):
                    self.rp._start_time(self.tmp, stat=bad)

    def test_the_real_stat_answers_a_finite_start(self):
        v = self.rp._start_time(self.tmp)
        self.assertIsInstance(v, float)
        self.assertGreater(v, 0.0)


class WholePassTests(unittest.TestCase):
    def setUp(self):
        self.rp = _load()
        self.runs = tempfile.mkdtemp(prefix="d31-runs-", dir=_ROOT)

    def tearDown(self):
        shutil.rmtree(self.runs, ignore_errors=True)

    def _pass(self):
        buf = io.StringIO()
        saved_runs, saved_argv = self.rp.RUNS, sys.argv
        self.rp.RUNS = self.runs
        sys.argv = ["runner_pool.py", "--dry"]
        try:
            with contextlib.redirect_stdout(buf):
                self.rp.main()
        finally:
            self.rp.RUNS, sys.argv = saved_runs, saved_argv
        return buf.getvalue()

    def _folder(self):
        d = _run_folder(self.runs, "EXHAUSTED", time.time() + 720)
        st = os.stat(d)
        born = getattr(st, "st_birthtime", None)
        if not isinstance(born, (int, float)) or born <= 0:
            self.skipTest("NO-DATA: this platform reports no folder birth time; _start_time falls back to the mtime by design")
        return born

    def test_dry_pass_reseats_a_unit_with_a_note_from_during_its_run(self):
        born = self._folder()
        _FACT[0] = born + 120   # after the start, before the folder's last write
        out = self._pass()
        self.assertNotIn("needs a fact", out)
        self.assertIn("ZZ1.a", out)
        self.assertIn("RETRY", out)

    def test_dry_pass_still_parks_a_note_from_before_the_start(self):
        born = self._folder()
        _FACT[0] = born - 60
        out = self._pass()
        self.assertIn("last run of ZZ1.a ended EXHAUSTED: needs a fact", out)
        self.assertNotIn("RETRY", out)


if __name__ == "__main__":
    unittest.main()
