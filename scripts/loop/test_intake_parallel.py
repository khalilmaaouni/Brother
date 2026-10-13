#!/usr/bin/env python3
"""LA2.a: the intake's independent read only probes run together and print in table order.

WHY. A cold launch spent two minutes in the intake, a chain of independent probes run one after another. They now
run in one bounded pool (loop_intake.run_probes); prepare reads each result at its old position, so the lines, the
states and the verdict are exactly what the serial code produced, and the TIME rows stay in PROBE_ORDER.

The mutations that must turn this suite red, each against its own case:
  M-LA2-ORDER   the TIME rows are appended in completion order  -> test_parallel_lines_equal_serial_lines_for_mixed_outcomes
  M-LA2-CANARY  the canary is submitted at once with the others -> test_the_canary_starts_after_tree_and_digest_finish
  M-LA2-RAISE   a probe exception propagates                    -> test_a_raising_probe_is_no_data_and_not_ready

Everything outside the test is injected: no network, no real model, program or machine state.
Python 3.9 and 3.13, standard library only. Run: python3 -B scripts/loop/test_intake_parallel.py
"""
import datetime
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_intake as LI  # noqa: E402

_REG = {"cheap": {"transport": "bridge", "privacy": "public", "kinds": {"build", "grade"}, "quality": {"build": 5, "grade": 5}},
        "strong": {"transport": "claude", "privacy": "private", "kinds": {"build", "grade", "plan", "prose"}, "quality": {"build": 9, "grade": 9}}}
_BASE = {"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False, "default": "cheap"}
_ROLES = {"worker": dict(_BASE, setting="BROTHER_PIN_MODEL"),
          "finisher": dict(_BASE, when="after_run", content="private", must_be_chosen=True, default=None)}
_NOON = datetime.datetime(2026, 1, 1, 12, 0)
TUPLE_PROBES = ("alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")


def _ok_reach(plan):
    return [{"status": "OK", "role": r, "model": m, "program": "/p", "version": "1.0", "cause": "fixture",
             "cached": False, "binding": None} for r, m in plan]


def _good():
    p = {n: (lambda n=n: ("OK", "%s answers" % n)) for n in TUPLE_PROBES}
    p.update(canary=lambda: ("OK", "went end to end"), lint=lambda: ("OK", "clean"), headroom=lambda: 50.0,
             hold=lambda: False, reach=_ok_reach, now=lambda: _NOON)
    return p


def _slow(value, seconds):
    def probe(*_a):
        time.sleep(seconds)
        return value
    return probe


def _boom(name):
    def probe(*_a):
        raise RuntimeError("boom in %s " % name + "x" * 200)
    return probe


def _prepare(over=None, env=None, workers=None):
    saved = LI.PROBE_WORKERS
    if workers is not None:
        LI.PROBE_WORKERS = workers
    try:
        return LI.prepare({"finisher": "strong"}, "18:00", 10.0, None, _ROLES, _REG, dict(_good(), **(over or {})),
                          env={} if env is None else env)
    finally:
        LI.PROBE_WORKERS = saved


def _strip(rec, lines):
    """What the serial run must equal: every line but the TIME values, the TIME row names in order, the record."""
    rest = [l for l in lines if not l.startswith("TIME ")]
    names = [l.split()[1] for l in lines if l.startswith("TIME ")]
    r = {k: v for k, v in rec.items() if k not in ("timings", "epoch", "lines")}
    return rest, names, list(rec["timings"]), r


class ProbesRunTogether(unittest.TestCase):

    def test_five_slow_probes_finish_in_the_time_of_one(self):
        slow = {n: _slow(("OK", "%s answers" % n), 1.0) for n in ("alive", "lease", "parity", "switch", "done")}
        t0 = time.monotonic()
        rec, lines = _prepare(slow)
        took = time.monotonic() - t0
        self.assertLess(took, 2.5, "five 1 s probes took %.1f s: they ran one after another" % took)
        ref_rec, ref_lines = _prepare()
        self.assertEqual(rec["verdict"], "READY", lines)
        self.assertEqual(_strip(rec, lines), _strip(ref_rec, ref_lines))

    def test_parallel_lines_equal_serial_lines_for_mixed_outcomes(self):
        # headroom (first in the table) is slow in every fixture, so it finishes LAST in the pool
        fixtures = (
            {"headroom": _slow(50.0, 0.3)},
            {"headroom": _slow(50.0, 0.3), "alive": lambda: ("REFUSED", "a runner is alive"), "tree": lambda: ("REFUSED", "dirty")},
            {"headroom": _slow(50.0, 0.3), "canary": lambda: ("NO-DATA", "could not run"), "lint": lambda: ("FINDINGS", "2 units"),
             "done": _boom("done")},
            {"headroom": _slow(None, 0.3), "digest": lambda: ("NO-DATA", "crashed"),
             "reach": lambda plan: [dict(p, status="FAIL", cause="unrecognized_model", remedy="update it") for p in _ok_reach(plan)]},
            {"headroom": _slow(50.0, 0.3), "hold": lambda: True, "pool": _boom("pool"), "salvage": lambda: ("REFUSED", "x")},
        )
        verdicts = []
        for i, over in enumerate(fixtures):
            with self.subTest(fixture=i):
                par = _prepare(over, workers=8)
                ser = _prepare(over, workers=1)
                self.assertEqual(_strip(*par), _strip(*ser), "M-LA2-ORDER: the parallel run differs from the serial one")
                self.assertEqual(_strip(*par)[1], ["headroom", "alive", "lease", "parity", "switch", "tree", "digest", "done",
                                                   "salvage", "pool", "plan_lint", "canary", "reach", "hold"])
                verdicts.append(par[0]["verdict"])
        self.assertEqual(verdicts, ["READY", "NOT READY", "NOT READY", "NOT READY", "NOT READY"])

    def test_the_canary_starts_after_tree_and_digest_finish(self):
        seen = {}
        def rec(name, seconds):
            def probe():
                seen[name + "_start"] = time.monotonic()
                time.sleep(seconds)
                seen[name + "_end"] = time.monotonic()
                return "OK", name
            return probe
        rec_, lines = _prepare({"tree": rec("tree", 0.3), "digest": rec("digest", 0.4), "canary": rec("canary", 0.0),
                                "alive": rec("alive", 0.3)})
        self.assertEqual(rec_["verdict"], "READY", lines)
        self.assertGreaterEqual(seen["canary_start"], max(seen["tree_end"], seen["digest_end"]),
                                "M-LA2-CANARY: the canary's worktree overlapped the tree or digest git reads")
        self.assertLess(seen["alive_start"], seen["tree_end"], "the independent probes did not overlap")

    def test_at_most_eight_probes_run_at_once(self):
        lock, now, peak = threading.Lock(), [0], [0]
        def probe(value):
            def run(*_a):
                with lock:
                    now[0] += 1; peak[0] = max(peak[0], now[0])
                time.sleep(0.15)
                with lock:
                    now[0] -= 1
                return value
            return run
        over = {n: probe(("OK", n)) for n in TUPLE_PROBES + ("canary", "lint")}
        over.update(headroom=probe(50.0), hold=probe(False), reach=lambda plan: probe(_ok_reach(plan))())
        rec, lines = _prepare(over)
        self.assertEqual(rec["verdict"], "READY", lines)
        self.assertLessEqual(peak[0], LI.PROBE_WORKERS)
        self.assertEqual(LI.PROBE_WORKERS, 8)
        self.assertGreater(peak[0], 1)

    def test_a_raising_probe_is_no_data_and_not_ready(self):
        for name in TUPLE_PROBES + ("canary",):
            with self.subTest(probe=name):
                rec, lines = _prepare({name: _boom(name)})
                said = [l for l in lines if ("probe %s raised RuntimeError: boom in %s" % (name, name)) in l]
                self.assertEqual(len(said), 1, "M-LA2-RAISE: %s\n%s" % (name, "\n".join(lines)))
                text = said[0].split("raised RuntimeError: ", 1)[1]
                self.assertLessEqual(len(text.split("  HINT:")[0].split("  RISK:")[0]), 120, said[0])
                if name == "canary":   # a canary that cannot run stays the advisable class
                    self.assertTrue(said[0].startswith("ASK"), said[0])
                    self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", lines)
                else:
                    self.assertTrue(said[0].startswith("NO-DATA"), said[0])
                    self.assertEqual(rec["verdict"], "NOT READY", lines)
                self.assertIn(name, rec["timings"])   # the raising probe keeps its TIME row
        rec, lines = _prepare({"headroom": _boom("headroom")})
        self.assertEqual(rec["verdict"], "NOT READY", lines)
        self.assertTrue(any(l.startswith("NO-DATA") and "headroom could not be read" in l for l in lines), lines)
        rec, lines = _prepare({"reach": _boom("reach")})
        self.assertEqual(rec["verdict"], "NOT READY", lines)
        self.assertTrue(any(l.startswith("NO-DATA") and "no proof was run" in l for l in lines), lines)
        # lint: the same NO-DATA tuple, through the lint's existing branch (report: information; block: asked)
        for env in ({}, {"BROTHER_RUNFLOW_LINT": "block"}):
            with self.subTest(lint_env=env):
                raised = _prepare({"lint": _boom("lint")}, env=env)
                said = [l for l in raised[1] if "probe lint raised RuntimeError" in l]
                self.assertEqual(len(said), 1, raised[1])
                direct = _prepare({"lint": lambda: ("NO-DATA", "x")}, env=env)
                self.assertEqual(raised[0]["verdict"], direct[0]["verdict"])
                self.assertNotEqual(raised[0]["verdict"], "READY" if env else "NOT READY")

    def test_a_raising_hold_is_held(self):
        rec, lines = _prepare({"hold": _boom("hold")})
        self.assertEqual(rec["verdict"], "HELD", lines)
        self.assertIn("HELD     the owner's HOLD is IN FORCE: no driver may start until he orders it", lines)

    def test_only_the_probes_given_run(self):
        sink = []
        out = LI.run_probes({"alive": lambda: ("OK", "a"), "hold": lambda: False}, None, sink)
        self.assertEqual(out, {"alive": ("OK", "a"), "hold": False})
        self.assertEqual([r[0] for r in sink], ["alive", "hold"])
        sink = []
        self.assertEqual(LI.run_probes({"reach": _ok_reach}, None, sink), {})   # no plan: reach is not run
        self.assertEqual(LI.run_probes({"alive": lambda: ("OK", "a")}, None, [], workers=1), {"alive": ("OK", "a")})


if __name__ == "__main__":
    unittest.main(verbosity=1)
