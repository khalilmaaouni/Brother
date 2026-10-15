#!/usr/bin/env python3
"""LS2.a: the native session cap is sized to the unit, not one constant.

S, M and L answer 2700, 4500 and 5400 seconds (each inside SESSION_RANGE), BROTHER_NATIVE_SESSION_S still wins when
it is set and valid, no size keeps DEFAULT_SESSION_S, and an unknown size letter refuses with ValueError rather than
falling back silently. The one production call site in unit_runner.py passes the unit's own size, and the recording
stub in scripts/test_unit_runner_native.py accepts that keyword.

Run from the repository root: python3 -B scripts/loop/test_native_worker_session_cap.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import native_worker as NW  # noqa: E402  the module under test, edited by this build

RUNNER = os.path.join(HERE, "unit_runner.py")
HARNESS = os.path.join(os.path.dirname(HERE), "test_unit_runner_native.py")
CAPS = {"S": 2700, "M": 4500, "L": 5400}


class TheCapByUnitSize(unittest.TestCase):
    """REQ-LS2-A-01 to REQ-LS2-A-05: the per size table, the default, the knob, the refusals and hostile input."""

    def test_each_size_has_its_cap(self):
        self.assertEqual(NW.session_seconds({}, size="S"), 2700)
        self.assertEqual(NW.session_seconds({}, size="M"), 4500)
        self.assertEqual(NW.session_seconds({}, size="L"), 5400)
        for size, cap in CAPS.items():
            self.assertEqual(NW.session_seconds({}, size=size), cap)
        self.assertEqual(dict(NW.SESSION_BY_SIZE), CAPS)

    def test_no_size_keeps_the_default_cap(self):
        self.assertEqual(NW.session_seconds({}), NW.DEFAULT_SESSION_S)
        self.assertEqual(NW.session_seconds({}, size=None), NW.DEFAULT_SESSION_S)
        self.assertEqual(NW.session_seconds(None), NW.DEFAULT_SESSION_S)

    def test_the_env_knob_still_wins_over_the_size(self):
        for size in CAPS:
            self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "3600"}, size=size), 3600)
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "600"}, size="L"), 600)
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "7200"}, size="S"), 7200)

    def test_an_unknown_size_refuses(self):
        for bad in ("s", "m", "l", "XL", "SS", " S", "S ", "", "2700", "2700\n"):
            with self.assertRaises(ValueError):
                NW.session_seconds({}, size=bad)

    def test_a_bad_size_refuses_even_when_the_env_is_set(self):
        for bad in ("XL", "x", "", " "):
            with self.assertRaises(ValueError):
                NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "3600"}, size=bad)

    def test_every_cap_sits_inside_session_range(self):
        for size, cap in NW.SESSION_BY_SIZE.items():
            self.assertGreaterEqual(cap, NW.SESSION_RANGE[0], "cap for %s below SESSION_RANGE" % size)
            self.assertLessEqual(cap, NW.SESSION_RANGE[1], "cap for %s above SESSION_RANGE" % size)
        self.assertGreaterEqual(NW.DEFAULT_SESSION_S, NW.SESSION_RANGE[0])
        self.assertLessEqual(NW.DEFAULT_SESSION_S, NW.SESSION_RANGE[1])

    def test_a_bad_env_value_still_refuses_with_a_size(self):
        for bad in ("59", "7201", "abc", "-1", "1.5", "600x"):
            with self.assertRaises(ValueError):
                NW.session_seconds({"BROTHER_NATIVE_SESSION_S": bad}, size="M")

    def test_the_positional_env_call_is_unchanged(self):
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "3000"}), 3000)
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "600"}), 600)
        self.assertEqual(NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "7200"}), 7200)
        self.assertEqual(NW.session_seconds({}), NW.DEFAULT_SESSION_S)
        with self.assertRaises(ValueError):
            NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "599"})
        with self.assertRaises(ValueError):
            NW.session_seconds({"BROTHER_NATIVE_SESSION_S": "7201"})

    def test_hostile_sizes_refuse_never_crash(self):
        for bad in (0, 1, True, False, -1, 1.5, float("nan"), float("inf"), "", "x", "S ", b"S", b"",
                    [], ["S"], {}, {"S": 2700}, (), ("S",), {1, 2}, frozenset(("S",)), object()):
            with self.assertRaises(ValueError):
                NW.session_seconds({}, size=bad)

    def test_an_absent_size_field_keeps_the_launch_default(self):
        rec = {}
        self.assertEqual(NW.session_seconds({}, size=rec.get("size")), NW.DEFAULT_SESSION_S)


class TheRunnerCallSite(unittest.TestCase):
    """REQ-LS2-A-06: the runner passes the unit's own size, and the harness stub accepts it."""

    def test_unit_runner_passes_the_unit_size_at_its_one_call_site(self):
        src = open(RUNNER, encoding="utf-8").read()
        self.assertIn('_NW.session_seconds(size=_unit_rec.get("size"))', src)

    def test_the_native_stub_accepts_the_size_keyword(self):
        src = open(HARNESS, encoding="utf-8").read()
        self.assertIn("def session_seconds(env=None, size=None):", src)


if __name__ == "__main__":
    unittest.main()
