#!/usr/bin/env python3
"""Calibration for the benchmark bundle builder.

CONFIRMED DEFECT this exists to catch again: the shipped zip's own directory,
holding only the benchmark scripts, ran 27 tests with 1 failure because the
test suite reads a results file relative to the repo and the bundle carried
no benchmarks dir. test_bundle_runs_green_with_no_repo_above_it reproduces
that exact case (a scratch copy with no repo above it) and must pass.
"""
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_benchmark_bundle as mkb  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMP = "2026-08-30T00:00:00Z"


def _purge_pycache(root):
    for dirpath, dirnames, _ in os.walk(root):
        if "__pycache__" in dirnames:
            shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            dirnames.remove("__pycache__")


class BundleBuilds(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="bundle-")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_bundle_builds_with_expected_layout_and_manifest(self):
        manifest_path = mkb.build(self.out, STAMP)
        self.assertTrue(os.path.isfile(manifest_path))
        for rel in (
            "scripts/vault_benchmark_v2.py",
            "scripts/test_vault_benchmark_v2.py",
            "run_benchmark.sh",
            "verify_manifest.py",
        ):
            self.assertTrue(os.path.isfile(os.path.join(self.out, rel)), rel)
        self.assertTrue(glob.glob(os.path.join(self.out, "benchmarks", "memory-ab", "results-*.json")))
        self.assertTrue(glob.glob(os.path.join(self.out, "benchmarks", "graph-value", "results-*.json")))
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["generated_at"], STAMP)
        self.assertEqual(manifest["python_floor"], "3.9")
        # the runtime that actually built the bundle, not just the floor
        self.assertEqual(manifest["python_version"], platform.python_version())
        self.assertEqual(manifest["platform"], platform.platform())
        # every shipped file (never the manifest itself) is hashed
        self.assertIn("scripts/vault_benchmark_v2.py", manifest["files"])
        self.assertNotIn("benchmark_manifest.json", manifest["files"])

    def test_missing_outcome_artifact_raises_not_a_silent_empty_bundle(self):
        with self.assertRaises(SystemExit):
            mkb.build(self.out, STAMP, memory_ab_glob=os.path.join(self.out, "no-such-*.json"))


class BundleRunsStandalone(unittest.TestCase):
    """The exact failing case tonight: a scratch copy with no repo above it."""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="bundle-run-")
        mkb.build(self.out, STAMP)
        _purge_pycache(self.out)

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_bundle_runs_green_with_no_repo_above_it(self):
        pr = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"],
            cwd=self.out, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        out = pr.stdout.decode("utf-8", "replace")
        self.assertEqual(pr.returncode, 0, out)
        self.assertIn("OK", out)
        self.assertNotIn("FAILED", out)

    def test_verify_manifest_passes_on_untouched_bundle(self):
        pr = subprocess.run([sys.executable, "verify_manifest.py"], cwd=self.out,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(pr.returncode, 0, pr.stdout.decode("utf-8", "replace"))

    def test_verify_manifest_refuses_a_tampered_fixture(self):
        target = glob.glob(os.path.join(self.out, "benchmarks", "memory-ab", "results-*.json"))[0]
        with open(target, "r+b") as fh:
            data = bytearray(fh.read())
            data[0] ^= 0xFF  # flip one byte
            fh.seek(0)
            fh.write(data)
        pr = subprocess.run([sys.executable, "verify_manifest.py"], cwd=self.out,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertNotEqual(pr.returncode, 0)
        self.assertIn("TAMPER DETECTED", pr.stdout.decode("utf-8", "replace"))

    def test_missing_artifact_is_NODATA_never_a_pass(self):
        """d04 without the fixture must report NO-DATA, never fall back to a
        stale prior claim."""
        scripts_dir = os.path.join(self.out, "scripts")
        sys.path.insert(0, scripts_dir)
        try:
            import vault_benchmark_v2 as vb
            got, msg = vb.d04_memory_outcome_lift(
                {"results_glob": os.path.join(self.out, "no-such-dir", "results-*.json")})
            self.assertEqual(got, vb.NODATA, msg)
        finally:
            sys.path.remove(scripts_dir)
            sys.modules.pop("vault_benchmark_v2", None)


if __name__ == "__main__":
    unittest.main()
