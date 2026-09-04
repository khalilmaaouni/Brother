#!/usr/bin/env python3
"""Calibration for tools/bm_vault_jbench.py, WBS VB2-03: the Japanese-first
retrieval benchmark runner.

Driven backwards: the shipped fixture actually passes every declared floor
end to end (subprocess CLI run, the real done-check evidence), a doctored
floor above the measured rate fails the run, a missing case file is
NO-DATA (exit 2), and a class with zero cases reports NO-DATA rather than a
silent pass.

Run: python3 -m unittest test_bm_vault_jbench -v

No em or en dashes anywhere in this file.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault_jbench.py")
FIXTURE = os.path.join(HERE, "fixtures", "japanese-benchmark.json")


def _load_jbench():
    spec = importlib.util.spec_from_file_location(
        "bm_vault_jbench", os.path.join(HERE, "bm_vault_jbench.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestShippedFixtureRun(unittest.TestCase):
    """The real, end-to-end done-check: the CLI, run as a subprocess exactly
    the way the row's own done-check quotes it, against the shipped fixture."""

    def test_shipped_fixture_meets_every_declared_floor(self):
        proc = subprocess.run([sys.executable, TOOL, "run", "--cases", FIXTURE],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                          "shipped fixture must clear every declared floor:\n%s\n%s"
                          % (proc.stdout, proc.stderr))
        self.assertIn("overall:", proc.stdout)
        self.assertIn("per-class score table:", proc.stdout)


class TestNoData(unittest.TestCase):
    def test_missing_case_file_is_no_data_exit_2(self):
        proc = subprocess.run(
            [sys.executable, TOOL, "run", "--cases", "/no/such/path.json"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("NO-DATA", proc.stdout)

    def test_empty_case_file_is_no_data_exit_2(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "empty.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"cases": []}, fh)
            proc = subprocess.run([sys.executable, TOOL, "run", "--cases", p],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("NO-DATA", proc.stdout)
        finally:
            import shutil
            shutil.rmtree(d)


class TestFloorDoctoring(unittest.TestCase):
    """A floor set above what the fixture actually measures must fail the
    run: proves the exit code is load-bearing, not decorative."""

    def test_doctored_floor_above_measured_rate_fails(self):
        jb = _load_jbench()
        fixture, err = jb.load_fixture(FIXTURE)
        self.assertIsNone(err)
        bm = jb._load_bm_vault()
        per_class, _overall, _detail = jb.run_benchmark(bm, fixture)
        # Every class the fixture actually measures scores at or below 100%;
        # a floor pinned above that must fail regardless of which class.
        cls_with_cases = next(c for c, v in per_class.items() if v)
        hits, total = per_class[cls_with_cases]
        measured = hits / total
        doctored = dict(jb.CLASS_FLOORS)
        doctored[cls_with_cases] = min(1.5, measured + 0.5)

        class Args:
            pass
        args = Args()
        args.cases = FIXTURE
        args.limit = jb.DEFAULT_LIMIT
        args.verbose = False
        orig_floors = jb.CLASS_FLOORS
        jb.CLASS_FLOORS = doctored
        try:
            rc = jb.cmd_run(args)
        finally:
            jb.CLASS_FLOORS = orig_floors
        self.assertNotEqual(rc, 0, "a floor doctored above the measured rate must fail")

    def test_class_with_zero_cases_is_no_data_not_a_pass(self):
        jb = _load_jbench()
        fixture, err = jb.load_fixture(FIXTURE)
        self.assertIsNone(err)
        fixture = dict(fixture)
        fixture["cases"] = [c for c in fixture["cases"] if c["class"] != "negative"]

        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "no-negative.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(fixture, fh, ensure_ascii=False)
            proc = subprocess.run([sys.executable, TOOL, "run", "--cases", p],
                                  capture_output=True, text=True)
            self.assertIn("NO-DATA", proc.stdout)
            self.assertNotEqual(proc.returncode, 0,
                                "a class with zero cases must never read as a pass")
        finally:
            import shutil
            shutil.rmtree(d)


class TestFixtureFileItself(unittest.TestCase):
    def test_at_least_200_cases(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            fixture = json.load(fh)
        self.assertGreaterEqual(len(fixture["cases"]), 200)

    def test_every_expected_note_and_forbidden_note_resolves_to_a_real_stem(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            fixture = json.load(fh)
        stems = {n["stem"] for n in fixture["notes"]}
        for case in fixture["cases"]:
            target = case.get("expected_note") or case.get("forbidden_note")
            self.assertIn(target, stems,
                          "case %s names a stem no note declares: %r"
                          % (case.get("id"), target))


if __name__ == "__main__":
    unittest.main()
