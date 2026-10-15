#!/usr/bin/env python3
"""CT1.b: the canary names every missing fixture build file before it replays any fixture.

Run: python3 -B scripts/loop/test_loop_canary_fixtures.py

Red on the base tree: missing_fixture_files does not exist there, and main replays the first fixture before it
notices that a later fixture's build file is absent.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import loop_canary


def _touch(path, text="{}"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class MissingFixtureFilesTests(unittest.TestCase):
    """REQ-CT1-B-01, REQ-CT1-B-02, REQ-CT1-B-05: the list is complete, ordered, and corrupt input refuses."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="canary-fixtures-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.present = _touch(os.path.join(self.tmp, "present.json"))
        self.missing_a = os.path.join(self.tmp, "missing-a.json")
        self.missing_b = os.path.join(self.tmp, "missing-b.json")

    def _spec(self, entries, build=None):
        return {"sub": "s0", "base_commit": "c0",
                "build": self.present if build is None else build,
                "fixtures": entries}

    def _missing(self, spec):
        with contextlib.redirect_stderr(io.StringIO()):
            return loop_canary.missing_fixture_files(spec)

    def test_every_missing_build_file_is_listed_in_fixture_order(self):
        spec = self._spec([
            {"sub": "s1", "base_commit": "c1", "build": self.missing_a},
            {"sub": "s2", "base_commit": "c2", "build": self.present},
            {"sub": "s3", "base_commit": "c3", "build": self.missing_b},
            {"sub": "s4"},
        ])
        self.assertEqual(self._missing(spec),
                         ["s1: %s" % self.missing_a, "s3: %s" % self.missing_b])

    def test_all_files_present_lists_nothing(self):
        spec = self._spec([
            {"sub": "s1", "base_commit": "c1", "build": self.present},
            {"sub": "s2", "base_commit": "c2", "build": self.present},
        ])
        self.assertEqual(self._missing(spec), [])

    def test_a_directory_is_not_a_build_file_and_a_non_dict_is_refused(self):
        directory = os.path.join(self.tmp, "a-directory")
        os.mkdir(directory)
        spec = self._spec([{"sub": "s1", "base_commit": "c1", "build": directory}], build="")
        self.assertEqual(self._missing(spec), ["s0: ", "s1: %s" % directory])

    def test_hostile_input_is_refused_never_a_crash(self):
        for bad in (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"],
                    (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                self._missing(bad)
        with self.assertRaises(ValueError):
            self._missing({"sub": "s0", "base_commit": "c0", "build": self.present, "fixtures": 5})
        self.assertEqual(self._missing({}), ["None: None"])
        self.assertIsInstance(self._missing({"fixtures": []}), list)


class MainFixtureGateTests(unittest.TestCase):
    """REQ-CT1-B-03, REQ-CT1-B-04: main refuses NO-DATA before any replay, and is unchanged when files exist."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="canary-main-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.present = _touch(os.path.join(self.tmp, "present.json"))
        self.missing = os.path.join(self.tmp, "missing.json")

    def _run_main(self, spec, stages_impl):
        saved = (loop_canary.load_spec, loop_canary.stages, list(sys.argv))
        loop_canary.load_spec = lambda path=None: spec
        loop_canary.stages = stages_impl
        sys.argv = ["loop_canary.py"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = loop_canary.main()
        finally:
            loop_canary.load_spec, loop_canary.stages, sys.argv = saved
        return rc, buf.getvalue()

    def test_main_refuses_before_replaying_any_fixture(self):
        spec = {"sub": "s0", "base_commit": "c0", "build": self.present,
                "fixtures": [{"sub": "s1", "base_commit": "c1", "build": self.present},
                             {"sub": "s2", "base_commit": "c2", "build": self.missing}]}
        calls = []

        def exploding_stages(fx, runner, keep=False, spec_gate=None):
            calls.append(fx)
            self.fail("stages ran for %s: a missing fixture build file must stop the canary first"
                      % fx.get("sub"))

        rc, out = self._run_main(spec, exploding_stages)
        self.assertEqual(calls, [])
        self.assertEqual(rc, 3)
        self.assertIn("CANARY NO-DATA: 1 fixture build file(s) missing, no fixture was replayed", out)
        self.assertIn("s2: %s" % self.missing, out)

    def test_main_with_every_file_present_still_replays(self):
        spec = {"sub": "s0", "base_commit": "c0", "build": self.present,
                "fixtures": [{"sub": "s1", "base_commit": "c1", "build": self.present}]}
        seen = []

        def recording_stages(fx, runner, keep=False, spec_gate=None):
            seen.append(fx["sub"])
            return [("sandbox", "OK", "worktree at c0")]

        rc, out = self._run_main(spec, recording_stages)
        self.assertEqual(seen, ["s0", "s1"])
        self.assertEqual(rc, 0)
        self.assertNotIn("no fixture was replayed", out)
        self.assertIn("CANARY WOULD LAND", out)


if __name__ == "__main__":
    unittest.main()
