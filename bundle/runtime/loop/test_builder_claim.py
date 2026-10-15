#!/usr/bin/env python3
"""LC1.a: builder_claim writes and releases a runner shaped claim the pool reads.

The suite lives beside the module it tests. It loads runner_pool.py with sys.modules stand ins for its sibling
modules, so no process is started, and every fixture is built in a temporary directory.

Run: python3 -B scripts/loop/test_builder_claim.py
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import builder_claim as BC  # noqa: E402  (the module under test, beside this file)

POOL_PATH = os.path.join(HERE, "runner_pool.py")
_MISSING = object()
HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())


class _CP(object):
    """A stand in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeSubprocess(object):
    """The pool's subprocess use, replaced: this suite starts no process at all."""

    class SubprocessError(Exception):
        pass

    def run(self, argv, **kw):
        joined = " ".join(str(item) for item in argv)
        if "burn_guard" in joined:
            return _CP("10\n", 0)
        if "spec_score" in joined:
            return _CP("", 0)
        return _CP("calibrator ok\n", 0)

    def Popen(self, argv, **kw):
        raise AssertionError("a --dry pass never starts a runner")


class _FakePlanPath(object):
    """plan_path.selected(): the plan file this suite writes, never a real repository document."""

    path = "/nonexistent/lc1a-wbs.json"

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
    COUNCIL_PATH = "/nonexistent/lc1a-council.json"

    @staticmethod
    def hard_findings(uid, sub, unit, spec_text):
        return []

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
    def fail_class(refusal):
        return "SUITE"

    @staticmethod
    def last_rows(path):
        return []


class _FakeDiagNotes(object):

    @staticmethod
    def note_path(uid):
        return "/nonexistent/lc1a-diag-note.md"


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
    module.subprocess = _FakeSubprocess()
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


class _Fixture(unittest.TestCase):
    """A runs directory and a spec file of its own, in a temporary directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lc1a-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(self.runs)
        self.spec = os.path.join(self.tmp, "T1.md")
        with open(self.spec, "w", encoding="utf-8") as fh:
            fh.write("# T1\n\n### T1.a\nFiles: `scripts/t1.py`\n")
        self.plan = {"units": [{"id": "T1", "sub_units": ["T1.a"]}]}


class ClaimTests(_Fixture):

    def test_claim_writes_a_runner_shaped_folder_alive_runners_reads(self):
        folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)
        self.assertTrue(os.path.isdir(folder))
        self.assertTrue(os.path.isfile(os.path.join(folder, BC.CLAIM_FILE)))
        self.assertFalse(os.path.exists(os.path.join(folder, "STATUS")))
        with open(os.path.join(folder, "PID"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "%d\n" % os.getpid())
        with open(os.path.join(folder, BC.CLAIM_FILE), encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual(record["state"], "CLAIMED")
        self.assertEqual(record["sub"], "T1.a")
        self.assertEqual(record["builder"], "b-17")
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["branch"], "lx1-a2")
        self.assertTrue(record["at"] > 0)
        self.assertIn(("T1", "T1.a"), RP.alive_runners(self.runs, self.plan))

    def test_busy_paths_marks_the_claimed_specs_files_busy(self):
        BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)
        alive = RP.alive_runners(self.runs, self.plan)
        self.assertIn(("T1", "T1.a"), alive)
        busy, unknown = RP.busy_paths(sorted(alive), {"T1": self.spec}, touch=RP.touch_set)
        self.assertEqual(unknown, [])
        self.assertEqual(busy.get("scripts/t1.py"), "T1.a")

    def test_release_removes_the_claim_and_the_sub_unit_is_free_again(self):
        folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)
        self.assertEqual(BC.claim_hold(self.runs, "T1.a"),
                         "claimed by builder b-17 (pid %d, branch lx1-a2)" % os.getpid())
        self.assertTrue(BC.release(folder, runs_dir=self.runs))
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(BC.claim_hold(self.runs, "T1.a"), "")
        self.assertEqual(RP.alive_runners(self.runs, self.plan), set())

    def test_release_twice_is_idempotent(self):
        folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)
        self.assertTrue(BC.release(folder, runs_dir=self.runs))
        self.assertFalse(BC.release(folder, runs_dir=self.runs))

    def test_a_dead_pid_claim_is_reconciled_like_a_dead_runner(self):
        folder = BC.claim("T1.a", "b-17", 999999, "lx1-a2", runs_dir=self.runs)
        self.assertEqual(BC.claim_hold(self.runs, "T1.a", pid_alive=lambda pid: False), "")
        self.assertEqual(RP.reconcile_dead(folder, lambda pid: False), "STALLED")
        with open(os.path.join(folder, "STATUS"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "STALLED dead runner pid 999999")

    def test_an_unreadable_claim_record_holds_never_frees(self):
        folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)
        payloads = [
            "",
            "{\"state\": \"CLAIMED\"",
            "[]",
            "{\"state\": \"CLAIMED\", \"sub\": \"T1.a\"}",
            json.dumps({"state": "OPEN", "sub": "T1.a", "builder": "b-17", "pid": os.getpid(),
                        "branch": "lx1-a2", "at": 0.0}),
            json.dumps({"state": "CLAIMED", "sub": "T1.b", "builder": "b-17", "pid": os.getpid(),
                        "branch": "lx1-a2", "at": 0.0}),
        ]
        for payload in payloads:
            with self.subTest(payload=payload[:24]):
                with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
                    fh.write(payload)
                text = BC.claim_hold(self.runs, "T1.a")
                self.assertTrue(text.startswith("NO-DATA claim record"), text)
                self.assertIn(folder, text)
                self.assertIn("remedy:", text)
        with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"state": "CLAIMED", "sub": "T1.a", "builder": "b-17", "pid": os.getpid(),
                                 "branch": "lx1-a2", "at": 0.0}))
        with open(os.path.join(folder, "PID"), "w", encoding="utf-8") as fh:
            fh.write("123\n")
        text = BC.claim_hold(self.runs, "T1.a")
        self.assertTrue(text.startswith("NO-DATA claim record"), text)
        self.assertIn(folder, text)

    def test_a_claim_older_than_six_hours_still_holds(self):
        BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs, now=0.0)
        text = BC.claim_hold(self.runs, "T1.a")
        self.assertTrue(text.startswith("claimed by builder b-17"), text)
        self.assertTrue(text.endswith("older than 6 hours"), text)

    def test_a_second_claim_of_the_same_sub_is_refused(self):
        BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)
        with self.assertRaises(ValueError):
            BC.claim("T1.a", "b-18", os.getpid(), "other", runs_dir=self.runs)
        second = os.path.join(self.tmp, "runs2")
        os.makedirs(second)
        open_dir = os.path.join(second, "T1.a-120000")
        os.makedirs(open_dir)
        with open(os.path.join(open_dir, "PID"), "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        with self.assertRaises(ValueError):
            BC.claim("T1.a", "b-19", os.getpid(), "other", runs_dir=second)

    def test_release_refuses_a_path_outside_the_runs_directory(self):
        outside = os.path.join(self.tmp, "T1.a-123456")
        os.makedirs(outside)
        with open(os.path.join(outside, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(outside, "PID"), "w", encoding="utf-8") as fh:
            fh.write("1\n")
        with self.assertRaises(ValueError):
            BC.release(outside, runs_dir=self.runs)
        self.assertTrue(os.path.isdir(outside))

    def test_release_refuses_a_runners_own_folder(self):
        runner = os.path.join(self.runs, "T1.a-120000")
        os.makedirs(runner)
        with open(os.path.join(runner, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(runner, "RUN-TAG"), "w", encoding="utf-8") as fh:
            fh.write("unit_runner\n")
        with self.assertRaises(ValueError):
            BC.release(runner, runs_dir=self.runs)

    def test_cli_claim_check_and_release_round_trip(self):
        saved = BC.DEFAULT_RUNS
        BC.DEFAULT_RUNS = self.runs
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = BC.main(["claim", "T1.a", "b-17", str(os.getpid()), "lx1-a2"])
            self.assertEqual(rc, 0)
            folder = buf.getvalue().strip()
            self.assertTrue(os.path.isdir(folder))
            self.assertEqual(os.path.dirname(folder), self.runs)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = BC.main(["check", "T1.a"])
            self.assertEqual(rc, 1)
            self.assertIn("claimed by builder b-17", buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = BC.main(["release", folder])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().strip(), "released")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = BC.main(["check", "T1.a"])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().strip(), "free")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = BC.main(["release", folder])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().strip(), "already released")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = BC.main(["nonsense"])
            self.assertEqual(rc, 2)
            self.assertIn("REFUSED", buf.getvalue())
        finally:
            BC.DEFAULT_RUNS = saved


class HostileTests(unittest.TestCase):
    """Every public function is called with each hostile value in each positional parameter: the call either
    refuses with ValueError (SystemExit for the command line) or answers, and never raises anything else."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lc1a-hostile-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(self.runs)
        self._cwd = os.getcwd()
        self.addCleanup(os.chdir, self._cwd)
        os.chdir(self.tmp)
        self._argv = sys.argv
        self.addCleanup(setattr, sys, "argv", self._argv)
        sys.argv = ["builder_claim.py"]

    def test_hostile_arguments_are_refused_or_answered(self):
        calls = []
        for value in HOSTILE:
            calls.extend([
                lambda v=value: BC.claim(v, "b-1", 1, "br", self.runs),
                lambda v=value: BC.claim("h.a", v, 1, "br", self.runs),
                lambda v=value: BC.claim("h.a", "b-1", v, "br", self.runs),
                lambda v=value: BC.claim("h.a", "b-1", 1, v, self.runs),
                lambda v=value: BC.claim("h.a", "b-1", 1, "br", v),
                lambda v=value: BC.claim("h.a", "b-1", 1, "br", self.runs, v),
                lambda v=value: BC.release(v, self.runs),
                lambda v=value: BC.release("h.a-123456", v),
                lambda v=value: BC.claim_hold(v, "h.a"),
                lambda v=value: BC.claim_hold(self.runs, v),
                lambda v=value: BC.claim_hold(self.runs, "h.a", v),
                lambda v=value: BC.main(v),
            ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for call in calls:
                try:
                    answer = call()
                except (ValueError, SystemExit):
                    continue
                self.assertIsInstance(answer, (str, int))


class DryPassTests(_Fixture):
    """The real pool pass, its sibling modules stubbed, its subprocess replaced, no runner started."""

    def setUp(self):
        _Fixture.setUp(self)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        self.wbs = os.path.join(self.tmp, "wbs.json")
        plan = {"units": [{"id": "T1", "state": "OPEN", "sub_units": ["T1.a"], "evidence": "",
                           "spec": self.spec}]}
        with open(self.wbs, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        with open(os.path.join(self.home, ".claude", "evidence", "spec-scores.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"T1.a": {"score": 9}}, fh)
        self.folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs)

    def _run_dry(self):
        saved = {key: os.environ.get(key) for key in ("HOME", "BROTHER_WIP_GATE", "BROTHER_LOAD_CAP",
                                                      "BROTHER_EXPLORE")}
        saved_runs = RP.RUNS
        saved_argv = sys.argv
        saved_path = _FakePlanPath.path
        os.environ["HOME"] = self.home
        os.environ["BROTHER_WIP_GATE"] = "off"
        os.environ.pop("BROTHER_LOAD_CAP", None)
        os.environ.pop("BROTHER_EXPLORE", None)
        RP.RUNS = self.runs
        _FakePlanPath.path = self.wbs
        sys.argv = ["runner_pool.py", "--dry"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                RP.main()
        finally:
            RP.RUNS = saved_runs
            _FakePlanPath.path = saved_path
            sys.argv = saved_argv
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return buf.getvalue()

    def test_dry_pass_prints_claimed_by_builder_and_starts_nothing(self):
        out = self._run_dry()
        self.assertIn("claimed by builder b-17 (pid %d, branch lx1-a2)" % os.getpid(), out)
        self.assertNotIn("START", out)
        self.assertIn("started 0", out)

    def test_the_dry_pass_starts_the_sub_unit_once_the_claim_is_released(self):
        self.assertTrue(BC.release(self.folder, runs_dir=self.runs))
        out = self._run_dry()
        self.assertNotIn("claimed by builder", out)
        self.assertIn("START", out)
        self.assertIn("started 1", out)


if __name__ == "__main__":
    unittest.main()
