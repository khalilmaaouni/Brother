#!/usr/bin/env python3
"""D32.a: a pass whose pool printed a live COOLDOWN line is a wait, not UNPRODUCTIVE (register row L28).

The pool holds a sub unit for RESEAT_MIN_S after a parked run; the pass counted that timer as doing nothing and exited
45, which the driver raises as UNPRODUCTIVE (2026-10-14 23h11: a run stopped on its first pass because its only unit
was cooling down). The pool now prints `COOLDOWN <unit> <sub> until <epoch>` for each cooled sub unit, and
loop_pass.sh's exit 45 block exits 0 with a WAITING line while that expiry is in the future.

Two entry points, both real: the lines of scripts/loop/loop_pass.sh between `# D32 BEGIN` and `# D32 END`, run by bash
with the pass's own variables, and runner_pool.py's main() as a --dry pass with sys.modules stand ins for its sibling
modules in a temp HOME. No process but bash is started and no network is touched.

Run: python3 -B scripts/loop/test_unproductive_cooldown.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(HERE, "runner_pool.py")
PASS_PATH = os.path.join(HERE, "loop_pass.sh")

_ROOT = None
_HOME = None
_WBS = None
_POOL = None
_SAVED = {}
_ENV_SAVED = {}


def _block():
    """The exit 45 block of loop_pass.sh, the lines between its two D32 markers. Missing markers fail the test."""
    with open(PASS_PATH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    try:
        a = lines.index("# D32 BEGIN")
        b = lines.index("# D32 END")
    except ValueError:
        raise AssertionError("loop_pass.sh carries no '# D32 BEGIN' / '# D32 END' markers around its exit 45 block")
    if b <= a + 1:
        raise AssertionError("the D32 markers hold no lines")
    return "\n".join(lines[a + 1:b])


def _run_block(pool_all, started="0", live="0", landed="0", closed="0"):
    """Run the block with the pass's variables; a pass the block does not end prints FELL THROUGH and exits 0."""
    tmp = tempfile.mkdtemp(prefix="d32-block-")
    try:
        script = os.path.join(tmp, "block.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n" + _block() + "\necho 'FELL THROUGH'\nexit 0\n")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "POOL_ALL": pool_all,
               "STARTED": started, "LIVE": live, "LANDED_N": landed, "CLOSED_N": closed}
        return subprocess.run(["bash", script], env=env, cwd=tmp, capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _CP(object):
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeSubprocess(object):
    """The pool's subprocess use, replaced: the pool starts no process at all."""
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


def _setup():
    global _ROOT, _HOME, _WBS
    if _ROOT is not None:
        return
    _ROOT = tempfile.mkdtemp(prefix="d32-cooldown-")
    _HOME = os.path.join(_ROOT, "home")
    os.makedirs(os.path.join(_HOME, ".claude", "evidence"))
    units, scores = [], {}
    for uid in ("ZA", "ZB"):
        spec = os.path.join(_ROOT, uid + ".md")
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write("# %s\n\nDone check: `python3 -B -m unittest nothing`\n" % uid)
        units.append({"id": uid, "state": "OPEN", "spec": spec, "sub_units": [uid + ".a"], "evidence": ""})
        scores[uid + ".a"] = {"score": 9}
    _WBS = os.path.join(_ROOT, "wbs.json")
    with open(_WBS, "w", encoding="utf-8") as fh:
        json.dump({"units": units}, fh)
    with open(os.path.join(_HOME, ".claude", "evidence", "spec-scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh)
    for name, value in (("HOME", _HOME), ("BROTHER_SCOPE", "."), ("BROTHER_WIP", "5")):
        _ENV_SAVED[name] = os.environ.get(name)
        os.environ[name] = value
    for name in ("BROTHER_PIN_MODEL", "BROTHER_NATIVE_SEATS", "WORKERS_PER_ROUND", "BROTHER_LOAD_CAP", "BROTHER_WIP_GATE"):
        _ENV_SAVED[name] = os.environ.pop(name, None)


def _load():
    """runner_pool.py with stand ins for every step of a pass except the cooldown under check: still_parked,
    reseated_too_soon and cooldown_line are the real ones; every parked fact is newer than any run (a re-seat)."""
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
    spec = importlib.util.spec_from_file_location("runner_pool_under_d32", POOL_PATH)
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
    module.parked_fact = lambda *a, **k: time.time() + 86400.0
    module.sunk = lambda rec: False
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


class PassBlockTests(unittest.TestCase):
    def test_a_cooldown_only_pass_waits(self):
        until = int(time.time()) + 300
        pool = "SCOPE   .\nZA    ZA.a     skip: skip: ZA.a started under ten minutes ago\nCOOLDOWN ZA ZA.a until %d\nstarted 0" % until
        r = _run_block(pool)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WAITING cooldown: ZA.a may restart after", r.stdout)
        self.assertIn("this pass is a wait, not UNPRODUCTIVE", r.stdout)
        self.assertNotIn("PASS DID NOTHING", r.stdout)

    def test_many_cooldowns_name_the_first_and_wait(self):
        now = int(time.time())
        r = _run_block("COOLDOWN ZA ZA.a until %d\nCOOLDOWN ZB ZB.a until %d\n" % (now + 400, now + 200))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WAITING cooldown: ZA.a may restart", r.stdout)

    def test_no_cooldown_still_alarms(self):
        for pool in ("", "SCOPE   .\nZA    ZA.a     skip: SPEC 5/10\nstarted 0", "cooldown ZA ZA.a until %d" % (int(time.time()) + 300)):
            with self.subTest(pool=pool):
                r = _run_block(pool)
                self.assertEqual(r.returncode, 45, r.stdout + r.stderr)
                self.assertIn("PASS DID NOTHING", r.stdout)
                self.assertNotIn("WAITING", r.stdout)

    def test_a_past_or_garbled_expiry_still_alarms(self):
        now = int(time.time())
        for line in ("COOLDOWN ZA ZA.a until %d" % (now - 5), "COOLDOWN ZA ZA.a until %d" % now,
                     "COOLDOWN ZA ZA.a until", "COOLDOWN ZA ZA.a until 12ab", "COOLDOWN ZA ZA.a until -%d" % (now + 300),
                     "COOLDOWN ZA ZA.a until %d.5" % (now + 300), "COOLDOWN %d" % (now + 300),
                     "COOLDOWN ZA ZA.a until %d trailing" % (now + 300), "COOLDOWN ZA ZA.a until 99999999999999999999999"):
            with self.subTest(line=line):
                r = _run_block("started 0\n" + line + "\n")
                self.assertEqual(r.returncode, 45, r.stdout + r.stderr)
                self.assertIn("PASS DID NOTHING", r.stdout)
                self.assertNotIn("WAITING", r.stdout)

    def test_a_pass_that_did_something_never_reaches_the_block(self):
        live = "COOLDOWN ZA ZA.a until %d" % (int(time.time()) + 300)
        for kw in ({"started": "1"}, {"live": "2"}, {"landed": "1"}, {"closed": "1"}):
            with self.subTest(**kw):
                r = _run_block(live, **kw)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(r.stdout.strip(), "FELL THROUGH")


class CooldownLineTests(unittest.TestCase):
    def setUp(self):
        self.rp = _load()

    def test_the_line_shape(self):
        self.assertEqual(self.rp.cooldown_line("ZA", "ZA.a", 1000.9), "COOLDOWN ZA ZA.a until %d" % int(1000.9 + self.rp.RESEAT_MIN_S))
        self.assertEqual(self.rp.cooldown_line("ZA", "ZA.a", 5), "COOLDOWN ZA ZA.a until %d" % (5 + self.rp.RESEAT_MIN_S))

    def test_hostile_cooldown_arguments_are_refused(self):
        for bad in (None, "", " ", "Z A", "ZA\n", 7, True, b"ZA", [], {}):
            for args in ((bad, "ZA.a", 1000.0), ("ZA", bad, 1000.0)):
                with self.subTest(args=args):
                    with self.assertRaises(ValueError):
                        self.rp.cooldown_line(*args)
        for bad in (None, 0, 0.0, -1.0, float("nan"), float("inf"), float("-inf"), True, False, "1000", [1000]):
            with self.subTest(run_at=bad):
                with self.assertRaises(ValueError):
                    self.rp.cooldown_line("ZA", "ZA.a", bad)


class PoolPassTests(unittest.TestCase):
    def setUp(self):
        self.rp = _load()
        self.runs = tempfile.mkdtemp(prefix="d32-runs-", dir=_ROOT)

    def tearDown(self):
        shutil.rmtree(self.runs, ignore_errors=True)

    def _folder(self, sub, word, age_s):
        d = os.path.join(self.runs, sub + "-231100")
        os.makedirs(d)
        with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
            fh.write(word + " test\n")
        t = time.time() - age_s
        os.utime(d, (t, t))
        return os.path.getmtime(d)

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

    def test_pool_prints_the_cooldown_line(self):
        at_a = self._folder("ZA.a", "EXHAUSTED", 0)
        at_b = self._folder("ZB.a", "WITHHELD", 60)
        lines = [l for l in self._pass().splitlines() if l.startswith("COOLDOWN ")]
        self.assertEqual(sorted(lines), ["COOLDOWN ZA ZA.a until %d" % int(at_a + self.rp.RESEAT_MIN_S),
                                         "COOLDOWN ZB ZB.a until %d" % int(at_b + self.rp.RESEAT_MIN_S)])

    def test_a_run_past_the_cooldown_prints_none(self):
        self._folder("ZA.a", "EXHAUSTED", self.rp.RESEAT_MIN_S + 60)
        out = self._pass()
        self.assertNotIn("COOLDOWN ", out)
        self.assertIn("RETRY", out)

    def test_the_pool_line_drives_the_pass_block_to_wait(self):
        self._folder("ZA.a", "EXHAUSTED", 0)
        out = self._pass()
        r = _run_block(out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WAITING cooldown: ZA.a may restart after", r.stdout)


if __name__ == "__main__":
    unittest.main()
