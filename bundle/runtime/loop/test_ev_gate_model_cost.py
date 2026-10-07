"""External test of ev_gate.py's F21 fix (model_round_cost) and repair_wave.py's per-model
pricing wiring (round_cost_for_arms, and the per-lane loop drawing its arms ONCE and reusing
them for both pricing and dispatch). Imports the real modules and calls their real functions;
does not read or pattern-match either file's source text.
"""
import contextlib
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ev_gate

SOURCE = Path(HERE) / "repair_wave.py"


def _repair_wave_ns():
    """repair_wave.py's own top level names (its functions, including round_cost_for_arms and
    _FALLBACK_JOB_COST), without ever reaching its real per lane dispatch body.

    repair_wave.py is NOT a safely importable module: everything after its own function
    definitions is bare module level code that runs unconditionally UNLESS "--selftest" is in
    sys.argv, in which case it calls sys.exit(selftest()) right there, before any real work. A
    plain `import repair_wave` with the unittest runner's own sys.argv (neither a wave directory
    nor --selftest) was measured, while building this file, to run that real body against
    sys.argv leftovers, printing a real "CALIB NO-DATA" line from a real judge_calibrate.py call:
    a real subprocess call and a real read of ~/.claude/evidence, from a bare import statement.

    Fixed by setting sys.argv to end in --selftest and exec()'ing the source into a namespace
    dict this function controls: exec (unlike import, and unlike runpy.run_path's return value)
    keeps every name already assigned in that dict even after the sys.exit(selftest()) call
    raises SystemExit past it. selftest() itself is repair_wave's own safe, self contained,
    temp-directory-only self test, exactly what --selftest already runs everywhere else."""
    src = SOURCE.read_text(encoding="utf-8")
    ns = {"__file__": str(SOURCE), "__name__": "repair_wave_under_test"}
    old_argv = sys.argv[:]
    sys.argv = [str(SOURCE), "--selftest"]
    try:
        exec(compile(src, str(SOURCE), "exec"), ns)
    except SystemExit as exc:
        # THE EXIT CODE IS THE VERDICT, NEVER DISCARDED: sys.exit(selftest()) hands back exactly what
        # repair_wave.py's own selftest returned, 0 for pass and nonzero for a real failure there. Every
        # function this file goes on to import (round_cost_for_arms, _FALLBACK_JOB_COST) comes out of
        # THIS SAME exec'd namespace, so a swallowed nonzero code would let this suite call functions
        # from a module its own author says is broken and never say so.
        if exc.code not in (0, None):
            raise RuntimeError("repair_wave.py's own --selftest exited %r; fix it there before trusting "
                              "anything this file imports from it" % (exc.code,)) from exc
    finally:
        sys.argv = old_argv
    return ns


_RW = _repair_wave_ns()
round_cost_for_arms = _RW["round_cost_for_arms"]
_FALLBACK_JOB_COST = _RW["_FALLBACK_JOB_COST"]


class TestModelRoundCostIsMedianNeverFlat(unittest.TestCase):
    def test_median_of_reconciled_costs_for_one_model(self):
        class FakeErr(Exception):
            pass

        def costs(root, model, limit=200):
            return [0.10, 0.30, 0.20] if model == "muse" else []

        self.assertEqual(ev_gate.model_round_cost("muse", _resolver=lambda: (costs, FakeErr)), 0.20)

    def test_plugin_absent_is_none_never_a_flat_default(self):
        self.assertIsNone(ev_gate.model_round_cost("muse", _resolver=lambda: (None, None)))

    def test_no_history_for_this_model_is_none_never_zero(self):
        class FakeErr(Exception):
            pass

        def costs(root, model, limit=200):
            return []

        self.assertIsNone(ev_gate.model_round_cost("muse", _resolver=lambda: (costs, FakeErr)))


class TestRoundCostForArms(unittest.TestCase):
    def test_sums_each_arms_own_measured_cost_with_a_fallback_per_unknown_arm(self):
        orig = ev_gate.model_round_cost
        ev_gate.model_round_cost = lambda m: {"m1": 0.5}.get(m)
        try:
            cost = round_cost_for_arms(["m1", "m2", "m1"])
        finally:
            ev_gate.model_round_cost = orig
        self.assertAlmostEqual(cost, 0.5 + _FALLBACK_JOB_COST + 0.5)

    def test_no_arms_prices_nothing(self):
        self.assertIsNone(round_cost_for_arms([]))


class TestRepairWaveDrawsArmsOnce(unittest.TestCase):
    """worker_mix.picks in the real bridge samples from an unseeded random.Random() on every
    call, so pricing a round from a SEPARATE arms() draw than the one dispatched could price a
    different model mix than what actually runs. Proven here with a picks() stand-in that
    returns a DIFFERENT model on each call (call 1: modelA, call 2+: modelB): if repair_wave.py
    drew arms twice (once to price, once to dispatch), the dispatched job's model would disagree
    with the round cost already computed from the first draw. Every other module repair_wave.py
    imports is faked too (the same shape as scripts/test_repair_wave_contract.py's own harness),
    so this never touches the network, a credential, or real spend."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="rw-arms-once-"))
        bw, pw, nw = (self.root / n for n in ("build", "probe", "repair"))
        for p in (bw / "grades", bw / "out", pw / "logs"):
            p.mkdir(parents=True)
        prompt = self.root / "prompt.txt"
        prompt.write_text("Fixture: repair one deliberate failure.")
        (bw / "jobs.json").write_text(json.dumps([{"id": "F1", "prompt_file": str(prompt)}]))
        (bw / "grades/F1-r0.txt").write_text("PASS\nexit=0\n")   # grade_one.sh's real shape: the verdict, then the exit code
        (bw / "out/F1-r0-build.json").write_text(json.dumps(
            {"edits": [{"path": "scripts/fixture.py", "new_file_content": "x = 1"}], "tests": []}))
        (pw / "logs/F1.done").write_text("DIRTY")
        (pw / "logs/F1-test.log").write_text("CRASH fixture AttributeError forced\n")
        self.bw, self.pw, self.nw = bw, pw, nw

    def test_the_arms_priced_are_the_arms_dispatched(self):
        calls = {"picks": 0}

        def picks(per, *a, **k):
            calls["picks"] += 1
            return ["modelA"] * per if calls["picks"] == 1 else ["modelB"] * per

        priced = {}

        def should_continue(hr, hp, round_cost=None, value=None, **k):
            priced["round_cost"] = round_cost
            return True, "fixture"

        fake_modules = {
            "probe_build": types.SimpleNamespace(classify=lambda *a: "CRASH", REFUSAL_VALUE=re.compile("^REFUSED$"), lessons=lambda: ""),
            "worker_mix": types.SimpleNamespace(picks=picks, arm_stats=lambda: {}),
            "ev_gate": types.SimpleNamespace(history=lambda *a: (0, 0), should_continue=should_continue,
                                              model_round_cost=lambda m: {"modelA": 1.0, "modelB": 5.0}.get(m)),
            "repair_advisor": types.SimpleNamespace(enabled=lambda *a: False),
            # U3: repair_wave asks the router for the code root its fan out runs from (the fan out itself is faked)
            "model_router": types.SimpleNamespace(registry=lambda: {"modelA": {}, "modelB": {}}, seatable_names=lambda: ["modelA", "modelB"],
                                                  code_root=lambda: str(self.root)),
            "brief_fit": types.SimpleNamespace(fit=lambda *a, **k: "\n".join(a)),
            "grade_build": types.SimpleNamespace(private_hits=lambda *a: 0),
        }

        def fake_run(argv, **kw):
            name = Path(argv[0]).name if len(argv) == 1 else Path(argv[1]).name
            if name == "judge_calibrate.py":
                return subprocess.CompletedProcess(argv, 0, stdout="CALIB fixture\n", stderr="")
            if Path(argv[0]).name == "grade_lanes_par.sh":
                return subprocess.CompletedProcess(argv, 0)
            if name == "probe_wave.py":
                kw["stdout"].write("probe fixture\n")
                kw["stdout"].flush()
                return subprocess.CompletedProcess(argv, 0)
            raise AssertionError("unexpected subprocess: %r" % (argv,))

        class FakeFanout:
            def __init__(self, argv, **kw):
                pass

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

            def kill(self):
                pass

            pid = 0

        home = tempfile.mkdtemp(prefix="rw-arms-once-home-")
        os.makedirs(os.path.join(home, ".claude", "evidence"))
        out = io.StringIO()
        argv_tail = [str(self.bw), str(self.pw), str(self.nw), "1"]
        with patch.dict(sys.modules, fake_modules), patch.object(sys, "argv", [str(SOURCE)] + argv_tail), \
                patch.object(subprocess, "run", fake_run), patch.object(subprocess, "Popen", FakeFanout), \
                patch.dict(os.environ, {"HOME": home}, clear=False), contextlib.redirect_stdout(out):
            try:
                runpy.run_path(str(SOURCE), run_name="__main__")
            except SystemExit:  # sbe: allow-silent this fixture never actually raises it (verified by instrumenting the catch); this test judges side effects (jobs.json, the picks count, the priced cost) below, never repair_wave.py's own exit code, and an unrelated crash still propagates since only SystemExit is caught here
                pass

        self.assertEqual(calls["picks"], 1,
                          "arms() (via worker_mix.picks) was called more than once for one lane: " + out.getvalue())
        jobs = json.loads((self.nw / "jobs.json").read_text())
        self.assertTrue(jobs, out.getvalue())
        self.assertEqual(jobs[0]["model"], "modelA",
                          "dispatched a model from a LATER arms() draw than the one priced: " + out.getvalue())
        self.assertAlmostEqual(priced.get("round_cost"), 1.0,
                                msg="the priced round cost did not match the single (modelA) arms draw: " + out.getvalue())



class ACorruptLedgerRefusesTheRound(unittest.TestCase):
    """2026-09-30: a corrupt ledger returned None, which round_cost_for_arms priced at the 0.02 cold start."""

    def test_a_corrupt_ledger_raises_through_round_cost_for_arms(self):
        class FakeErr(Exception):
            pass

        def costs(root, model, limit=200):
            raise FakeErr("corrupt row")
        real = ev_gate.model_round_cost
        ev_gate.model_round_cost = lambda m: real(m, _resolver=lambda: (costs, FakeErr))
        try:
            with self.assertRaises(ev_gate.CostUnreadable):
                round_cost_for_arms(["muse"])
        finally:
            ev_gate.model_round_cost = real

    def test_an_unreadable_ledger_file_raises_through_round_cost_for_arms(self):
        """Isolates the OSError branch: the resolver's LedgerError type is never raised, only OSError is."""
        class FakeErr(Exception):
            pass

        def costs(root, model, limit=200):
            raise OSError("ledger file unreadable (fixture)")
        real = ev_gate.model_round_cost
        ev_gate.model_round_cost = lambda m: real(m, _resolver=lambda: (costs, FakeErr))
        try:
            with self.assertRaises(ev_gate.CostUnreadable):
                round_cost_for_arms(["muse"])
        finally:
            ev_gate.model_round_cost = real

if __name__ == "__main__":
    unittest.main()
