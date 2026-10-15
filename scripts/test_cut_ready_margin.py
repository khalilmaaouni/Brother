#!/usr/bin/env python3
"""FR0.d: cut_ready.py's measured horizon from driver state.

Every fixture is a temp folder holding driver records; no test runs the real
ceremony script, git worktree or the network.

Python 3.9, standard library only. No em or en dashes.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_ready as CR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


def _state_with(total_seconds, phases=None):
    if phases is None:
        phases = {
            "first": {"verdict": "PASS", "seconds": total_seconds / 2.0},
            "second": {"verdict": "REUSED", "seconds": total_seconds / 2.0},
        }
    return {"phases": phases}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-ready-margin-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ev = os.path.join(self.tmp, "ev")
        os.makedirs(self.ev)

    def write_driver(self, name, state, schema="cv1.driver.1", mtime=None, raw=None):
        path = os.path.join(self.ev, name)
        with open(path, "wb") as handle:
            if raw is not None:
                handle.write(raw)
            else:
                payload = json.dumps({"schema": schema, "state": state}).encode("utf-8")
                handle.write(payload)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path


class MeasuredHorizon(Base):
    def test_a_nine_hour_measured_cut_refuses_a_seventeen_hour_pack(self):
        path = self.write_driver("driver-1.1.3-tag.json", _state_with(9.0 * 3600.0))
        hours, source = CR.measured_horizon(self.ev)
        self.assertAlmostEqual(hours, 9.0, places=6)
        self.assertIn(os.path.basename(path), source)
        self.assertIn("9.0", source)

        def run_all(root, version, horizon_hours=None, virgin=False):
            allowed = 24.0 - horizon_hours
            if 17.0 > allowed:
                return [("REFUSED", "pack-clock",
                         "pack 17.0 h old exceeds the %.1f h window" % allowed)]
            return [("OK", "pack-clock", "pack 17.0 h within the %.1f h window" % allowed)]

        rows = CR.preflight_rows("/root", "1.1.3", hours,
                                 run_all=run_all, horizon_source=source)
        pack = [r for r in rows if r["name"] == "pack-clock"]
        self.assertEqual(len(pack), 1)
        self.assertEqual(pack[0]["verdict"], CR.FAIL)
        self.assertIn("horizon 9", pack[0]["detail"])

        floor_rows = CR.preflight_rows("/root", "1.1.3", CR.FLOOR_HOURS, run_all=run_all,
                                       horizon_source="floor")
        floor_pack = [r for r in floor_rows if r["name"] == "pack-clock"][0]
        self.assertEqual(floor_pack["verdict"], CR.PASS)

    def test_no_driver_state_uses_the_six_hour_floor_and_says_so(self):
        self.assertEqual(CR.measured_horizon(self.ev),
                         (6.0, "floor 6 h, no driver state with a verdict"))
        self.write_driver("driver-1.1.3-foreign.json", _state_with(3600.0),
                          schema="cv1.driver.2")
        self.assertEqual(CR.measured_horizon(self.ev),
                         (6.0, "floor 6 h, no driver state with a verdict"))
        self.assertEqual(CR.measured_horizon(os.path.join(self.tmp, "missing")),
                         (6.0, "floor 6 h, no driver state with a verdict"))
        self.assertEqual(CR.measured_horizon(""),
                         (6.0, "floor 6 h, no driver state with a verdict"))

    def test_a_five_hour_measured_cut_keeps_the_floor(self):
        path = self.write_driver("driver-1.1.3-five.json", _state_with(5.0 * 3600.0))
        hours, source = CR.measured_horizon(self.ev)
        self.assertEqual(hours, 6.0)
        self.assertIn("below the floor", source)
        self.assertIn(os.path.basename(path), source)
        self.assertIn("5.0", source)


class BadStates(Base):
    def test_bad_states_are_skipped_a_runaway_is_capped_and_the_line_names_the_file(self):
        path = self.write_driver("driver-1.1.3-runaway.json",
                                 _state_with(30.0 * 3600.0), mtime=1700000000.0)
        self.write_driver("driver-1.1.3-corrupt.json", None, raw=b"{not json",
                          mtime=1700000100.0)
        hours, source = CR.measured_horizon(self.ev)
        self.assertEqual(hours, CR.CAP_HOURS)
        self.assertIn("capped", source)
        self.assertIn(os.path.basename(path), source)
        self.assertIn("skipped driver-1.1.3-corrupt.json", source)

    def test_state_seconds_ignores_bad_seconds_and_verdictless_phases(self):
        state = {"phases": {
            "a": {"verdict": "PASS", "seconds": 10.0},
            "b": {"verdict": "FAIL", "seconds": -5.0},
            "c": {"verdict": "NO-DATA", "seconds": True},
            "d": {"verdict": "REUSED", "seconds": float("nan")},
            "e": {"verdict": "REUSED", "seconds": float("inf")},
            "f": {"verdict": "REUSED", "seconds": "60"},
            "g": {"seconds": 100.0},
            "h": {"verdict": "GREEN", "seconds": 100.0},
        }}
        self.assertEqual(CR.state_seconds(state), 10.0)
        self.assertIsNone(CR.state_seconds({"phases": {}}))
        self.assertIsNone(CR.state_seconds({}))


class Hostile(Base):
    HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1},
               (), {1, 2}, object())

    def test_every_public_function_refuses_hostile_input(self):
        for value in self.HOSTILE:
            for func in (CR.state_seconds, CR.measured_horizon):
                try:
                    func(value)
                except (ValueError, LookupError, SystemExit):
                    continue
                except Exception as exc:
                    self.fail("%s with %r crashed with %s: %s"
                              % (func.__name__, value, type(exc).__name__, exc))


if __name__ == "__main__":
    unittest.main()
