#!/usr/bin/env python3
"""LOOP1's FROZEN harness: the pool names what limits it, admission reads lane and loop_gradable, and one named unit of
this plan lands through the loop with every stage row and no rescue line. Written by the session before the code; never
edited by whoever builds it (HARN1).

    python3 -B scripts/test_loop_delivery.py Room         LOOP1.a
    python3 -B scripts/test_loop_delivery.py Admitted     LOOP1.b
    python3 -B scripts/test_loop_delivery.py Delivered    LOOP1.c

RED BY ASSERTION, one condition per case, hermetic. Python 3.9, standard library."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
sys.path.insert(0, HERE)
WATCHED_UNIT, WATCHED_SUB = "AUTH1", "AUTH1.b"


def pool():
    import importlib
    return importlib.import_module("runner_pool")


def need(case, mod, name, signature=None):
    """The attribute, asserted first so a missing or differently shaped one is a FAIL by assertion, never an error."""
    case.assertTrue(hasattr(mod, name), "runner_pool defines no %s" % name)
    fn = getattr(mod, name)
    if signature:
        import inspect
        try:
            params = list(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            params = []
        case.assertEqual(signature, params, "%s does not take %s, it takes %s" % (name, signature, params))
    return fn


class Room(unittest.TestCase):
    """LOOP1.a"""

    def test_room_names_its_limit(self):
        """REQ-LOOP1A-01. room_of returns (room, limit name) and the name is the input that bound it."""
        P = pool()
        room_of = need(self, P, "room_of", ["alive", "in_progress", "burn_cap", "wip", "seat_cap", "busy"])
        got = room_of(alive=1, in_progress=1, burn_cap=5, wip=2, seat_cap=4, busy="")
        self.assertTrue(isinstance(got, tuple) and len(got) == 2, "room_of must return (room, limit), got %r" % (got,))
        self.assertEqual((1, "wip"), got)
        self.assertEqual((2, "burn"), room_of(alive=3, in_progress=0, burn_cap=5, wip=9, seat_cap=9, busy=""))
        self.assertEqual((0, "load"), room_of(alive=0, in_progress=0, burn_cap=5, wip=5, seat_cap=5, busy="load 9 over 8"))
        self.assertEqual((1, "seats"), room_of(alive=0, in_progress=0, burn_cap=5, wip=5, seat_cap=1, busy=""))

    def test_a_dry_pass_writes_only_the_room_record(self):
        """REQ-LOOP1A-02. --dry leaves one record (pool-room.jsonl) and no other file under the evidence folder."""
        home = tempfile.mkdtemp(prefix="pool-dry-")
        ev = os.path.join(home, ".claude", "evidence")
        os.makedirs(ev)
        plan = os.path.join(home, "BROTHER-TEST-WBS.json")
        with open(plan, "w") as fh:
            json.dump({"units": []}, fh)
        env = dict(os.environ, HOME=home, BROTHER_WBS=plan)
        p = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "runner_pool.py"), "--dry"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=600)
        self.assertIn("room for", p.stdout + p.stderr, "no room line: %s" % (p.stdout + p.stderr)[-400:])
        self.assertIn("limit:", p.stdout + p.stderr, "the room line names no limit")
        written = sorted(os.path.relpath(os.path.join(b, f), ev) for b, _d, fs in os.walk(ev) for f in fs)
        self.assertEqual(["pool-room.jsonl"], written, "a dry pass wrote: %s" % written)


class Admitted(unittest.TestCase):
    """LOOP1.b"""

    def unit(self, lane="loop", gradable=True, check="python3 -B scripts/test_x.py"):
        return ({"id": "U1", "state": "OPEN", "lane": lane, "loop_gradable": gradable, "done_check": check,
                 "sub_units": ["U1.a"], "evidence": "", "owns": ["scripts/x.py"]},
                {"id": "U1.a", "done_check": check, "files": [["scripts/x.py", "NEW"]]})

    def test_a_loop_unit_with_an_accepted_check_is_admitted(self):
        """REQ-LOOP1B-01."""
        P = pool()
        admission_reason = need(self, P, "admission_reason", ["unit", "sub", "tree"])
        u, s = self.unit()
        self.assertEqual("", admission_reason(u, s, ROOT), "a loop lane unit with an accepted check was refused")
        u, s = self.unit(lane="session")
        why = admission_reason(u, s, ROOT)
        self.assertIn("lane session", why)
        u, s = self.unit(gradable=False)
        self.assertTrue(admission_reason(u, s, ROOT), "a unit with loop_gradable false was admitted")

    def test_a_by_hand_refusal_names_the_change(self):
        """REQ-LOOP1B-02."""
        P = pool()
        admission_reason = need(self, P, "admission_reason", ["unit", "sub", "tree"])
        u, s = self.unit(check="python3 -B scripts/x.py | tee out")
        why = admission_reason(u, s, ROOT)
        self.assertTrue(why, "a check the grader refuses was admitted")
        self.assertIn("clear it by:", why)

    def test_an_unknown_lane_is_refused_by_name(self):
        P = pool()
        admission_reason = need(self, P, "admission_reason", ["unit", "sub", "tree"])
        u, s = self.unit(lane="teleport")
        why = admission_reason(u, s, ROOT)
        self.assertIn("teleport", why)


class Delivered(unittest.TestCase):
    """LOOP1.c: the watched unit's landing, read from the records the loop itself writes."""

    def rows(self):
        sys.path.insert(0, LOOP)
        import stage_log as SL
        self.assertTrue(hasattr(SL, "delivery_trace"), "stage_log defines no delivery_trace(unit, sub)")
        return SL.delivery_trace(WATCHED_UNIT, WATCHED_SUB)

    def test_the_named_unit_landed_through_the_loop(self):
        """REQ-LOOP1C-01. The stage rows read admitted, built, graded, landed, closed, each with a tree id."""
        rows = self.rows()
        self.assertTrue(rows, "no stage row exists for %s %s: it has not gone through the loop" % (WATCHED_UNIT, WATCHED_SUB))
        stages = [r.get("stage") for r in rows]
        for want in ("admitted", "built", "graded", "landed", "closed"):
            self.assertIn(want, stages, "stage %s is missing: %s" % (want, stages))
        self.assertEqual(stages, sorted(stages, key=["admitted", "built", "graded", "landed", "closed"].index),
                         "the stages are out of order: %s" % stages)
        for r in rows:
            self.assertRegex(str(r.get("tree", "")), r"^[0-9a-f]{40}$", "a stage row is not bound to a tree: %r" % (r,))

    def test_no_rescue_line(self):
        """REQ-LOOP1C-02. The plan evidence for the watched sub unit carries land_batch's template line and no rescue."""
        with open(os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.9-WBS.json"), encoding="utf-8") as fh:
            plan = json.load(fh)
        unit = next((u for u in plan["units"] if u["id"] == WATCHED_UNIT), None)
        self.assertIsNotNone(unit, "%s is not in the plan" % WATCHED_UNIT)
        ev = unit.get("evidence") or ""
        self.assertIn("%s LANDED" % WATCHED_SUB, ev, "%s has not landed" % WATCHED_SUB)
        self.assertIn("Landed by land_batch.py", ev, "the landing was not land_batch's")
        for word in ("by hand", "rescue", "--no-verify", "cherry-pick"):
            self.assertNotIn(word, ev.split("%s LANDED" % WATCHED_SUB, 1)[1][:600], "a rescue word follows the landing: %s" % word)


if __name__ == "__main__":
    unittest.main()
