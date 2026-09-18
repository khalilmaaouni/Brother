"""Calibration for scripts/wire_register.py (WIRE-02).

Every fixture here is synthetic: a scratch scripts/ directory, a scratch
battery file, and a scratch coverage JSON built by this file, never the
live repository (which other agents are editing concurrently while this
suite runs). The one rule this module exists to enforce is tested directly:
a suite that fails, times out, prints nothing, or dirties the tree it ran in
is never registered, no matter what its exit code says.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

import wire_register as wr


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _passing_test_src():
    return textwrap.dedent("""
        import unittest

        class T(unittest.TestCase):
            def test_ok(self):
                self.assertEqual(1, 1)

        if __name__ == "__main__":
            unittest.main()
    """)


def _failing_test_src():
    return textwrap.dedent("""
        import unittest

        class T(unittest.TestCase):
            def test_fails(self):
                self.assertEqual(1, 2)

        if __name__ == "__main__":
            unittest.main()
    """)


def _hanging_test_src():
    return textwrap.dedent("""
        import time
        time.sleep(30)
    """)


def _dirtying_test_src():
    # Writes a file into the repo it runs in and still reports a clean pass.
    return textwrap.dedent("""
        import os
        import unittest

        class T(unittest.TestCase):
            def test_ok(self):
                with open(os.path.join(os.path.dirname(__file__), "side_effect.txt"), "w") as fh:
                    fh.write("dirtied")
                self.assertTrue(True)

        if __name__ == "__main__":
            unittest.main()
    """)


def _coverage_payload(parts):
    """parts: {name: {"status": ..., "test": "scripts/test_x.py" or None,
    "tier": "A"}}. Only the fields wire_register.candidates() reads are
    required; the rest mirror assurance_coverage.py's real shape loosely."""
    records = []
    for name, spec in parts.items():
        records.append({
            "part": name,
            "status": spec["status"],
            "test": spec.get("test"),
            "tier": spec.get("tier", "B"),
        })
    return {"parts": records}


def _write_coverage(root, parts):
    path = os.path.join(root, "coverage.json")
    _write(path, json.dumps(_coverage_payload(parts)))
    return path


BATTERY_TEMPLATE = """#!/bin/sh
cd "$(dirname "$0")/.." || exit 1

run_check() {
  name="$1"; shift
  "$@" >/dev/null 2>&1
}

run_check "existing-self" python3 scripts/test_existing.py -v

# LAST, on purpose: compares against a snapshot taken at the top.
run_check "real-logs-unchanged" python3 scripts/real_logs.py compare "$X"
"""


def _write_battery(root):
    path = os.path.join(root, "check_all.sh")
    _write(path, BATTERY_TEMPLATE)
    return path


class CandidatesTests(unittest.TestCase):

    def test_filters_no_data_with_test_file(self):
        root = tempfile.mkdtemp()
        cov = _write_coverage(root, {
            "has_test": {"status": "NO-DATA", "test": "scripts/test_has_test.py"},
            "no_test": {"status": "NO-DATA", "test": None},
            "already_wired": {"status": "WIRED", "test": "scripts/test_already_wired.py"},
        })
        self.assertEqual(wr.candidates(cov), ["has_test"])

    def test_tier_c_included_not_excluded(self):
        # This module's docstring decision: tier answers a release-gate
        # question, never whether a real passing suite is worth wiring.
        root = tempfile.mkdtemp()
        cov = _write_coverage(root, {
            "tier_c_part": {"status": "NO-DATA", "test": "scripts/test_tier_c_part.py",
                             "tier": "C"},
        })
        self.assertEqual(wr.candidates(cov), ["tier_c_part"])

    def test_zero_candidates(self):
        root = tempfile.mkdtemp()
        cov = _write_coverage(root, {
            "wired": {"status": "WIRED", "test": "scripts/test_wired.py"},
        })
        self.assertEqual(wr.candidates(cov), [])


class ProbeTests(unittest.TestCase):

    def _scripts_dir(self):
        root = tempfile.mkdtemp()
        d = os.path.join(root, "scripts")
        os.makedirs(d)
        return root, d

    def test_passing_suite(self):
        root, d = self._scripts_dir()
        _write(os.path.join(d, "test_ok.py"), _passing_test_src())
        pr = wr.probe("ok", timeout_s=10, scripts_dir=d, repo_root=root)
        self.assertTrue(pr.passed)
        self.assertFalse(pr.timed_out)
        self.assertEqual(pr.exit_code, 0)

    def test_failing_suite_reports_exit_code_and_last_line(self):
        root, d = self._scripts_dir()
        _write(os.path.join(d, "test_bad.py"), _failing_test_src())
        pr = wr.probe("bad", timeout_s=10, scripts_dir=d, repo_root=root)
        self.assertFalse(pr.passed)
        self.assertFalse(pr.timed_out)
        self.assertNotEqual(pr.exit_code, 0)
        self.assertTrue(pr.last_line)

    def test_timeout_is_reported_separately_from_failure(self):
        root, d = self._scripts_dir()
        _write(os.path.join(d, "test_hangs.py"), _hanging_test_src())
        pr = wr.probe("hangs", timeout_s=0.2, scripts_dir=d, repo_root=root)
        self.assertFalse(pr.passed)
        self.assertTrue(pr.timed_out)
        self.assertIsNone(pr.exit_code)

    def test_probe_is_bounded_never_exceeds_timeout_by_much(self):
        root, d = self._scripts_dir()
        _write(os.path.join(d, "test_hangs2.py"), _hanging_test_src())
        pr = wr.probe("hangs2", timeout_s=0.3, scripts_dir=d, repo_root=root)
        self.assertTrue(pr.timed_out)
        self.assertLess(pr.duration_s, 5.0)

    def test_empty_test_file_never_registered(self):
        # A bad state a naive exit-code check would call a PASS: no source
        # ran, nothing was asserted, exit code is 0.
        root, d = self._scripts_dir()
        _write(os.path.join(d, "test_empty.py"), "")
        pr = wr.probe("empty", timeout_s=10, scripts_dir=d, repo_root=root)
        self.assertEqual(pr.exit_code, 0)
        self.assertFalse(pr.passed, "an empty file that runs no tests must never pass")

    def test_invalid_python_never_registered(self):
        root, d = self._scripts_dir()
        _write(os.path.join(d, "test_broken.py"), "def(():::\n")
        pr = wr.probe("broken", timeout_s=10, scripts_dir=d, repo_root=root)
        self.assertFalse(pr.passed)
        self.assertNotEqual(pr.exit_code, 0)

    def test_invalid_part_name_refused(self):
        root, d = self._scripts_dir()
        with self.assertRaises(wr.InvalidPartName):
            wr.probe("../etc/passwd", timeout_s=10, scripts_dir=d, repo_root=root)
        with self.assertRaises(wr.InvalidPartName):
            wr.probe("has a space", timeout_s=10, scripts_dir=d, repo_root=root)

    def test_missing_test_file_refused(self):
        root, d = self._scripts_dir()
        with self.assertRaises(wr.MissingTestFile):
            wr.probe("nope", timeout_s=10, scripts_dir=d, repo_root=root)

    def test_dirtying_probe_detected_and_not_passed(self):
        root = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        d = os.path.join(root, "scripts")
        os.makedirs(d)
        _write(os.path.join(d, "keep.txt"), "x")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
        _write(os.path.join(d, "test_dirty.py"), _dirtying_test_src())
        pr = wr.probe("dirty", timeout_s=10, scripts_dir=d, repo_root=root)
        self.assertTrue(pr.dirtied_tree)
        self.assertFalse(pr.passed, "a suite that dirties the tree must never register")


class RegisterTests(unittest.TestCase):

    def test_inserts_before_last_check(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        outcome = wr.register(["newpart"], battery)
        self.assertEqual(outcome.registered, ["newpart"])
        with open(battery, encoding="utf-8") as fh:
            lines = fh.readlines()
        new_idx = next(i for i, l in enumerate(lines) if "test_newpart.py" in l)
        last_idx = next(i for i, l in enumerate(lines) if wr.LAST_CHECK_MARKER in l)
        self.assertLess(new_idx, last_idx)

    def test_run_check_line_matches_convention(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        wr.register(["some_part"], battery)
        with open(battery, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('run_check "some-part-self" python3 scripts/test_some_part.py -v', text)

    def test_idempotent_no_duplicate_line(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        wr.register(["dup_part"], battery)
        first = wr.register(["dup_part"], battery)
        with open(battery, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("test_dup_part.py"), 1)
        self.assertEqual(first.registered, [])
        self.assertEqual(first.already_registered, ["dup_part"])

    def test_already_registered_part_skipped(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        outcome = wr.register(["existing"], battery)
        self.assertEqual(outcome.registered, [])
        self.assertEqual(outcome.already_registered, ["existing"])

    def test_no_last_check_marker_refuses(self):
        root = tempfile.mkdtemp()
        battery = os.path.join(root, "check_all.sh")
        _write(battery, 'run_check "only-one" true\n')
        with self.assertRaises(wr.LastCheckNotFoundError):
            wr.register(["x"], battery)

    def test_read_only_battery_reports_error_not_crash(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        os.chmod(battery, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            with self.assertRaises(wr.BatteryWriteError):
                wr.register(["newpart"], battery)
        finally:
            os.chmod(battery, stat.S_IRUSR | stat.S_IWUSR)

    def test_invalid_part_name_refused(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        with self.assertRaises(wr.InvalidPartName):
            wr.register(["bad name"], battery)


class MainDryRunTests(unittest.TestCase):

    def _fixture(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        scripts_dir = os.path.dirname(battery)
        _write(os.path.join(scripts_dir, "test_goodpart.py"), _passing_test_src())
        cov = _write_coverage(root, {
            "goodpart": {"status": "NO-DATA", "test": "scripts/test_goodpart.py"},
        })
        return root, battery, scripts_dir, cov

    def test_dry_run_changes_nothing(self):
        root, battery, scripts_dir, cov = self._fixture()
        with open(battery, encoding="utf-8") as fh:
            before = fh.read()
        code = wr.main(["--coverage", cov, "--battery", battery,
                         "--dry-run", "--timeout", "10"])
        self.assertEqual(code, 0)
        with open(battery, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after, "--dry-run must not change the battery file")

    def test_real_run_registers_the_green_part(self):
        root, battery, scripts_dir, cov = self._fixture()
        code = wr.main(["--coverage", cov, "--battery", battery, "--timeout", "10"])
        self.assertEqual(code, 0)
        with open(battery, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("test_goodpart.py", text)

    def test_failing_candidate_never_registered_by_main(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        scripts_dir = os.path.dirname(battery)
        _write(os.path.join(scripts_dir, "test_badpart.py"), _failing_test_src())
        cov = _write_coverage(root, {
            "badpart": {"status": "NO-DATA", "test": "scripts/test_badpart.py"},
        })
        code = wr.main(["--coverage", cov, "--battery", battery, "--timeout", "10"])
        self.assertEqual(code, 0, "a failed probe is a finding, not a crash")
        with open(battery, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("test_badpart.py", text)

    def test_zero_candidates_is_a_clean_noop(self):
        root = tempfile.mkdtemp()
        battery = _write_battery(root)
        cov = _write_coverage(root, {
            "wired": {"status": "WIRED", "test": "scripts/test_wired.py"},
        })
        with open(battery, encoding="utf-8") as fh:
            before = fh.read()
        code = wr.main(["--coverage", cov, "--battery", battery])
        self.assertEqual(code, 0)
        with open(battery, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
