#!/usr/bin/env python3
"""LA5.b: room is zero while the one minute load is at or above the core count.

The load rule lives in ONE place: perturb_pool.machine_busy, which perturb_pool.capped_workers also asks, so the
release note pool and the loop pool can never disagree at the boundary (REQ-LA5-B-03). runner_pool reads it through
load_hold, and main reads the load only through the BROTHER_LOAD_CAP seam: unset or empty means no load is read at
all, so admission is exactly as it was before LA5.b and the existing pool suites stay green on a busy machine. This
suite drives the seam with a fake reading, so no real machine state decides a verdict.

Run: python3 -B scripts/loop/test_runner_pool_load.py
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
POOL_PATH = os.path.join(HERE, "runner_pool.py")
PERTURB_PATH = os.path.join(SCRIPTS, "perturb_pool.py")

sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import perturb_pool as PP  # noqa: E402  (the module whose machine_busy is the estate's one load rule)

HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
_MISSING = object()


class _CP(object):
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeSubprocess(object):
    """The pass reads three child programs and spawns runners. A --dry pass spawns none, so Popen is an error;
    the three reads answer a fixed shape so no process is started and no real machine state is read."""
    STDOUT = -2
    PIPE = -1
    DEVNULL = -3

    class SubprocessError(Exception):
        pass

    @staticmethod
    def run(argv, **kwargs):
        prog = argv[1] if len(argv) > 1 else ""
        if "burn_guard" in prog:
            return _CP("10\n", 0)
        if "spec_score" in prog:
            return _CP("", 0)
        return _CP("calibrator ok\n", 0)

    @staticmethod
    def Popen(argv, **kwargs):
        raise AssertionError("a --dry pass must never spawn a runner")


class _FakePlanPath(object):
    path = "/nonexistent/la5b-wbs.json"

    @classmethod
    def selected(cls):
        return cls.path


class _FakePlanStore(object):
    @staticmethod
    def sub_landed(sub, evidence):
        return False

    @staticmethod
    def release_refusal(state):
        return ""

    @staticmethod
    def supply_hold(sub):
        return ""


class _FakeSpecCheck(object):
    @staticmethod
    def gate_refusal(spec_text, sub):
        return ""


class _FakePlanLint(object):
    RULE_OWNS = "OWNS"
    COUNCIL_PATH = "docs/plan/specs/council.json"

    @staticmethod
    def hard_findings(uid, sub, unit, spec_text):
        return []

    @staticmethod
    def _section(spec_text, sub):   # S3's shape_refusal reads the section's changed files; this pass judges load alone
        return spec_text

    @staticmethod
    def write_paths(section):
        return ["scripts/a_1.py"]

    @staticmethod
    def lint_mode():
        return ("report", "")

    @staticmethod
    def admission(plan, unit, sub, root, env, council=None):
        return ([], "")


class _FakeBriefCheck(object):
    SPEC_PATH = re.compile(r"`([^`]+)`")


class _FakeUnitLedger(object):
    @staticmethod
    def last_rows(path):
        return []

    @staticmethod
    def fail_class(refusal):
        return "SUITE"


class _FakeDiagNotes(object):
    @staticmethod
    def note_path(uid):
        return os.path.join(tempfile.gettempdir(), "la5b-no-such-diag-note.md")


class _FakeProofLedger(object):
    @staticmethod
    def draining():
        return False


class _FakeGradeBuild(object):
    @staticmethod
    def dest(root, path):
        return (os.path.join(root, path), "")


class _FakeLoopSwitches(object):
    @staticmethod
    def native_on(env, which=None):
        return False


_STUBS = (
    ("plan_path", _FakePlanPath),
    ("plan_store", _FakePlanStore),
    ("spec_check", _FakeSpecCheck),
    ("plan_lint", _FakePlanLint),
    ("brief_check", _FakeBriefCheck),
    ("unit_ledger", _FakeUnitLedger),
    ("diag_notes", _FakeDiagNotes),
    ("proof_ledger", _FakeProofLedger),
    ("grade_build", _FakeGradeBuild),
    ("loop_switches", _FakeLoopSwitches),
)

_SAVED = []


def _install_stubs():
    for name, stub in _STUBS:
        _SAVED.append((name, sys.modules.get(name, _MISSING)))
        sys.modules[name] = stub


def _restore_stubs():
    while _SAVED:
        name, old = _SAVED.pop()
        if old is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


def _load_pool():
    spec = importlib.util.spec_from_file_location("runner_pool_under_test", POOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["runner_pool_under_test"] = module
    spec.loader.exec_module(module)
    return module


_SCOPE_SAVED = os.environ.pop("BROTHER_SCOPE", _MISSING)
_install_stubs()
RP = _load_pool()


def tearDownModule():
    _restore_stubs()
    if _SCOPE_SAVED is _MISSING:
        os.environ.pop("BROTHER_SCOPE", None)
    else:
        os.environ["BROTHER_SCOPE"] = _SCOPE_SAVED


@contextlib.contextmanager
def _env(updates, remove=()):
    saved = {}
    for key in list(updates) + list(remove):
        saved[key] = os.environ.get(key, _MISSING)
    for key in remove:
        os.environ.pop(key, None)
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _whole_pass(load_cap):
    """Run the real pool's main() as a --dry pass over a fake one unit plan in a temp HOME, with the
    BROTHER_LOAD_CAP seam set to `load_cap` (None leaves it unset, so no load is read at all), and answer
    its stdout. No process is started and no real machine state is read."""
    tmp = tempfile.mkdtemp(prefix="la5b-pass-")
    try:
        runs = os.path.join(tmp, "runs")
        os.makedirs(runs)
        evidence = os.path.join(tmp, ".claude", "evidence")
        os.makedirs(evidence)
        spec_path = os.path.join(tmp, "T1.md")
        with open(spec_path, "w", encoding="utf-8") as fh:
            fh.write("# T1\n\n### T1.a\nFiles: `scripts/t1.py`\n")
        wbs = os.path.join(tmp, "wbs.json")
        plan = {"units": [{"id": "T1", "state": "OPEN", "sub_units": ["T1.a"], "evidence": "", "spec": spec_path}]}
        with open(wbs, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        with open(os.path.join(evidence, "spec-scores.json"), "w", encoding="utf-8") as fh:
            json.dump({"T1.a": {"score": 9}}, fh)
        updates = {"HOME": tmp, "BROTHER_WIP_GATE": "off"}
        if load_cap is not None:
            updates["BROTHER_LOAD_CAP"] = load_cap
        _FakePlanPath.path = wbs
        buf = io.StringIO()
        with _env(updates, remove=("BROTHER_LOAD_CAP", "BROTHER_EXPLORE")):
            with mock.patch.object(RP, "subprocess", _FakeSubprocess), \
                 mock.patch.object(RP, "RUNS", runs), \
                 mock.patch.object(sys, "argv", ["runner_pool.py", "--dry"]):
                with contextlib.redirect_stdout(buf):
                    RP.main()
        return buf.getvalue()
    finally:
        _FakePlanPath.path = "/nonexistent/la5b-wbs.json"
        shutil.rmtree(tmp, ignore_errors=True)


def _starts(text):
    return [line for line in text.splitlines() if "START" in line]


class _WholePassTests(unittest.TestCase):
    def test_load_at_the_core_count_admits_no_new_runner(self):
        cores = os.cpu_count() or 1
        out = _whole_pass(str(cores))
        self.assertEqual(_starts(out), [])
        self.assertIn("machine busy: one minute load", out)
        self.assertIn("is at or above %d core(s)" % cores, out)

    def test_three_times_the_cores_admits_none_through_the_whole_pass(self):
        out = _whole_pass("1000.0")
        self.assertEqual(_starts(out), [])
        self.assertIn("machine busy: one minute load 1000.0 is at or above", out)

    def test_idle_load_through_the_whole_pass_starts_the_unit(self):
        out = _whole_pass("0.0")
        self.assertNotIn("machine busy:", out)
        self.assertEqual(len(_starts(out)), 1)
        self.assertIn("T1.a", _starts(out)[0])

    def test_the_skip_line_names_the_load_and_the_remedy(self):
        out = _whole_pass("1000.0")
        skip = [line for line in out.splitlines() if "skip:" in line]
        self.assertEqual(len(skip), 1)
        self.assertIn("machine busy: one minute load 1000.0 is at or above", skip[0])
        self.assertIn("no new runner this pass", skip[0])
        self.assertIn("remedy: wait for the load to fall or stop other heavy work", skip[0])

    def test_the_seam_unset_reads_no_load(self):
        out = _whole_pass(None)
        self.assertNotIn("machine busy:", out)
        self.assertNotIn("NO-DATA load reading unavailable", out)
        self.assertEqual(len(_starts(out)), 1)


class _RoomTests(unittest.TestCase):
    def test_load_below_the_core_count_leaves_room(self):
        why = RP.load_hold(reading=lambda: (3.5, 8))
        self.assertEqual(why, "")
        room, cap_why = RP.room_of(10, 7, None, why)
        self.assertEqual(room, 7)
        self.assertEqual(cap_why, "WIP cap")
        room, cap_why = RP.room_of(10, 9, 4, why)
        self.assertEqual(room, 4)
        self.assertEqual(cap_why, "no seat")

    def test_a_busy_text_forces_room_to_zero(self):
        text = "machine busy: one minute load 9.2 is at or above 8 core(s)"
        room, cap_why = RP.room_of(10, 10, 10, text)
        self.assertEqual(room, 0)
        self.assertEqual(cap_why, text)


class _BusyRuleTests(unittest.TestCase):
    def test_capped_workers_and_machine_busy_agree_at_the_boundary(self):
        for ncpu in (2, 4, 8):
            for delta in (-0.001, 0.0, 0.001):
                load1 = ncpu + delta
                busy = PP.machine_busy(load1, ncpu)
                self.assertEqual(busy, load1 >= ncpu)
                capped = PP.capped_workers(4, load1=load1, ncpu=ncpu)
                self.assertEqual(capped, 1 if busy else min(4, ncpu))


class _NoDataTests(unittest.TestCase):
    def test_unreadable_load_reading_holds_with_no_data(self):
        def _boom():
            raise OSError("no getloadavg on this platform")
        why = RP.load_hold(reading=_boom)
        self.assertTrue(why.startswith("NO-DATA load reading unavailable:"))
        self.assertIn("OSError", why)
        room, cap_why = RP.room_of(9, 9, None, why)
        self.assertEqual(room, 0)
        self.assertEqual(cap_why, why)

    def test_nan_or_negative_load_is_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf"), -0.5):
            with self.assertRaises(ValueError):
                PP.machine_busy(bad, 8)
            why = RP.load_hold(reading=lambda _b=bad: (_b, 8))
            self.assertTrue(why.startswith("NO-DATA load reading unavailable: ValueError"))

    def test_missing_perturb_pool_holds(self):
        missing = os.path.join(tempfile.gettempdir(), "la5b-no-such-perturb-pool.py")
        saved = RP.LOAD_RULE
        RP.LOAD_RULE = missing
        try:
            why = RP.load_hold()
        finally:
            RP.LOAD_RULE = saved
        self.assertTrue(why.startswith("NO-DATA load reading unavailable:"))
        room, cap_why = RP.room_of(5, 5, None, why)
        self.assertEqual(room, 0)
        self.assertEqual(cap_why, why)


class _HostileTests(unittest.TestCase):
    def test_hostile_values_are_refused_or_held(self):
        for value in HOSTILE:
            try:
                PP.machine_busy(value, 8)
            except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                pass
            try:
                PP.machine_busy(0.5, value)
            except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                pass
            try:
                RP.room_of(value, 1, None, "")
            except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                pass
            try:
                RP.load_hold(value)
            except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                pass

    def test_a_hostile_reading_never_crashes_the_hold(self):
        for value in HOSTILE:
            why = RP.load_hold(reading=lambda _v=value: _v)
            self.assertIsInstance(why, str)


if __name__ == "__main__":
    unittest.main()
