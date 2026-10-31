#!/usr/bin/env python3
"""Selftest for scripts/donecheck_cycles119.py: it reaches all THREE verdicts, and the exit code is what
is asserted, never the printed word (a gate on this estate once printed FAIL and exited 0 under eleven
passing tests). NO-DATA is never a pass, including when the generator carries no cycle check at all."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import donecheck_cycles119 as D  # noqa: E402

GEN = os.path.join(ROOT, "docs/plan/specs/1.1.9/plan_v119.py")
ACYCLIC = {"units": [{"id": "A", "wave": 0, "after": [], "depends_on": []},
                     {"id": "B", "wave": 0, "after": ["A"], "depends_on": ["A"]}]}
CYCLIC = {"units": [{"id": "A", "wave": 0, "after": ["B"], "depends_on": ["B"]},
                    {"id": "B", "wave": 0, "after": ["A"], "depends_on": ["A"]}]}


def verdict(wbs, gen=GEN):
    """The donecheck's own exit code, with its output captured."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = D.main(wbs=wbs, gen=gen)
    return rc, buf.getvalue()


class Verdicts(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(GEN):
            self.skipTest("NO-DATA: the 1.1.9 generator is not in this tree, so this selftest cannot run")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def plan(self, obj, name="wbs.json"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)
        return path

    def test_an_acyclic_plan_exits_zero(self):
        self.assertEqual(verdict(self.plan(ACYCLIC))[0], 0)

    def test_a_cyclic_plan_exits_one(self):
        self.assertEqual(verdict(self.plan(CYCLIC))[0], 1)

    def test_a_missing_plan_is_no_data(self):
        self.assertEqual(verdict(os.path.join(self.tmp.name, "absent.json"))[0], 2)

    def test_a_plan_that_is_not_json_is_no_data(self):
        self.assertEqual(verdict(self.plan("{not json"))[0], 2)

    def test_a_plan_without_units_is_no_data(self):
        self.assertEqual(verdict(self.plan({"no": "units"}))[0], 2)

    def test_a_generator_with_no_cycle_check_is_no_data_never_a_pass(self):
        stub = os.path.join(self.tmp.name, "stub_gen.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("# a generator that cannot judge a graph\n")
        rc, _ = verdict(self.plan(ACYCLIC), gen=stub)
        self.assertEqual(rc, 2, "a generator with no plan_cycles must read NO-DATA, never PASS")


if __name__ == "__main__":
    unittest.main(verbosity=1)
