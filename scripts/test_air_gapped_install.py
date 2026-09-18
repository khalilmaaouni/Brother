#!/usr/bin/env python3
"""Tests for scripts/air_gapped_install.py (DOM-50.10).

Run directly: python3 scripts/test_air_gapped_install.py -v

The one test that matters most here is
test_run_probe_step_network_blocked_detects_unblocked: it proves that a
network-expected probe step which unexpectedly SUCCEEDS (the proxy block did
not hold) is caught as NETWORK_NOT_BLOCKED rather than read as a pass. A
version of run_probe_step that always reports BLOCKED_AS_EXPECTED for a
network-expected step would pass every other test in this file and still be
worthless; this is the one that catches it.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import air_gapped_install as agi


class StaticAuditTests(unittest.TestCase):

    def test_empty_checkout_is_not_identified(self):
        tmp = tempfile.mkdtemp(prefix="agi-test-empty-")
        try:
            result = agi.static_audit(tmp)
            self.assertFalse(result["install_path_identified"])
            self.assertEqual(result["findings"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_finds_curl_and_git_and_codex_calls_by_file_and_line(self):
        tmp = tempfile.mkdtemp(prefix="agi-test-hits-")
        try:
            scripts_dir = os.path.join(tmp, "scripts")
            os.makedirs(scripts_dir)
            with open(os.path.join(scripts_dir, "brother_install.py"), "w") as fh:
                fh.write("line one, nothing here\n")
                fh.write('    args = ["plugin", "marketplace", "add", source]\n')
                fh.write("line three\n")
            with open(os.path.join(scripts_dir, "bundle-install-smoke.sh"), "w") as fh:
                fh.write("#!/bin/sh\n")
                fh.write('curl -sfL "$RAW" -o "$WORK/marketplace.json"\n')
            result = agi.static_audit(tmp)
            self.assertTrue(result["install_path_identified"])
            by_file = {(f["file"], f["line"]): f["what"] for f in result["findings"]}
            self.assertEqual(
                by_file[("scripts/brother_install.py", 2)],
                "codex plugin marketplace add (fetches the marketplace source)")
            self.assertEqual(
                by_file[("scripts/bundle-install-smoke.sh", 2)],
                "curl invocation")
            # the untouched lines never appear
            self.assertNotIn(("scripts/brother_install.py", 1), by_file)
            self.assertNotIn(("scripts/brother_install.py", 3), by_file)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_one_present_file_is_enough_to_identify_the_install_path(self):
        tmp = tempfile.mkdtemp(prefix="agi-test-partial-")
        try:
            bundle_runtime = os.path.join(tmp, "bundle", "runtime")
            os.makedirs(bundle_runtime)
            with open(os.path.join(bundle_runtime, "verify_runtime.py"), "w") as fh:
                fh.write("import hashlib\n")
            result = agi.static_audit(tmp)
            self.assertTrue(result["install_path_identified"])
            self.assertEqual(result["findings"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BlockedNetworkEnvTests(unittest.TestCase):

    def test_every_proxy_variable_points_at_the_unroutable_port(self):
        env = agi.blocked_network_env("/tmp/does-not-need-to-exist-for-this-test")
        for key in agi.PROXY_ENV_KEYS:
            self.assertEqual(env[key], agi.UNROUTABLE_PROXY)
        self.assertEqual(env["PIP_NO_INDEX"], "1")

    def test_home_and_config_dirs_repointed_at_tmp_home_never_the_real_one(self):
        tmp_home = "/tmp/agi-fake-home-for-test"
        env = agi.blocked_network_env(tmp_home)
        self.assertEqual(env["HOME"], tmp_home)
        real_home = os.path.expanduser("~")
        self.assertNotEqual(env["HOME"], real_home)
        self.assertTrue(env["CODEX_HOME"].startswith(tmp_home))
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].startswith(tmp_home))


class BuildProbeStepsTests(unittest.TestCase):

    def test_codex_marketplace_add_is_named_not_probed(self):
        steps = agi.build_probe_steps("/repo", "/tmp/home")
        codex_steps = [s for s in steps if s["name"] == "codex_plugin_marketplace_add"]
        self.assertEqual(len(codex_steps), 1)
        step = codex_steps[0]
        self.assertTrue(step["not_probed"])
        self.assertIn("reason", step)
        self.assertTrue(step["reason"])  # never an empty, silent reason

    def test_every_executable_step_declares_an_expect(self):
        steps = agi.build_probe_steps("/repo", "/tmp/home")
        for step in steps:
            if step.get("not_probed"):
                continue
            self.assertIn(step["expect"], ("offline", "network_blocked"))
            self.assertIn("timeout", step)
            self.assertGreater(step["timeout"], 0)


class RunProbeStepTests(unittest.TestCase):
    """Uses harmless local `python -c` commands standing in for the real
    ones, so these tests never touch curl or git and run the same on any
    machine that has a Python interpreter."""

    def _env(self):
        return dict(os.environ)

    def test_offline_step_completes_regardless_of_its_own_exit_code(self):
        step = {"name": "x", "source": "test", "expect": "offline", "timeout": 5,
                "cwd": None,
                "argv": [sys.executable, "-c", "import sys; sys.exit(1)"]}
        result = agi.run_probe_step(step, self._env())
        self.assertEqual(result["outcome"], "RAN_OFFLINE")
        self.assertEqual(result["returncode"], 1)

    def test_network_blocked_step_detects_the_block_holding(self):
        step = {"name": "x", "source": "test", "expect": "network_blocked",
                "timeout": 5, "cwd": None,
                "argv": [sys.executable, "-c", "import sys; sys.exit(1)"]}
        result = agi.run_probe_step(step, self._env())
        self.assertEqual(result["outcome"], "BLOCKED_AS_EXPECTED")

    def test_run_probe_step_network_blocked_detects_unblocked(self):
        """The safety-relevant case: a network-expected step that exits 0
        (as if the block had not held, or a cache answered) must be reported
        as NETWORK_NOT_BLOCKED, never folded into a pass."""
        step = {"name": "x", "source": "test", "expect": "network_blocked",
                "timeout": 5, "cwd": None,
                "argv": [sys.executable, "-c", "import sys; sys.exit(0)"]}
        result = agi.run_probe_step(step, self._env())
        self.assertEqual(result["outcome"], "NETWORK_NOT_BLOCKED")

    def test_timeout_is_named_not_hung_forever(self):
        step = {"name": "x", "source": "test", "expect": "offline", "timeout": 1,
                "cwd": None,
                "argv": [sys.executable, "-c", "import time; time.sleep(5)"]}
        result = agi.run_probe_step(step, self._env())
        self.assertEqual(result["outcome"], "TIMEOUT")

    def test_missing_binary_is_named_not_a_crash(self):
        step = {"name": "x", "source": "test", "expect": "offline", "timeout": 5,
                "cwd": None,
                "argv": ["/no/such/binary-brother-air-gapped-test", "--version"]}
        result = agi.run_probe_step(step, self._env())
        self.assertEqual(result["outcome"], "COULD_NOT_RUN")

    def test_not_probed_step_passes_through_without_running_anything(self):
        step = {"name": "x", "source": "test", "not_probed": True,
                "reason": "because reasons"}
        result = agi.run_probe_step(step, self._env())
        self.assertEqual(result["outcome"], "NOT-PROBED")
        self.assertEqual(result["detail"], "because reasons")


class GenerateReportTests(unittest.TestCase):

    def test_no_data_when_install_path_cannot_be_identified(self):
        tmp = tempfile.mkdtemp(prefix="agi-test-nodata-")
        try:
            report = agi.generate_report(tmp)
            self.assertEqual(report["verdict"], "NO-DATA")
            self.assertEqual(report["probe"], [])
            self.assertTrue(report["reason"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fail_when_a_network_expected_step_is_not_blocked(self):
        """Even with a real install path identified, one unblocked
        network-expected step must sink the whole verdict to FAIL: nothing
        else in the report can be trusted as an offline measurement once the
        guard itself is shown not to hold."""
        tmp = tempfile.mkdtemp(prefix="agi-test-fail-")
        try:
            bundle_runtime = os.path.join(tmp, "bundle", "runtime")
            os.makedirs(bundle_runtime)
            with open(os.path.join(bundle_runtime, "verify_runtime.py"), "w") as fh:
                fh.write("import hashlib\n")

            fake_step = {"name": "fake_unblocked", "source": "test",
                         "expect": "network_blocked", "timeout": 5, "cwd": None,
                         "argv": [sys.executable, "-c", "import sys; sys.exit(0)"]}
            with mock.patch.object(agi, "build_probe_steps",
                                    return_value=[fake_step]):
                report = agi.generate_report(tmp)
            self.assertEqual(report["verdict"], "FAIL")
            self.assertEqual(report["probe"][0]["outcome"], "NETWORK_NOT_BLOCKED")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pass_when_install_path_identified_and_all_blocks_hold(self):
        tmp = tempfile.mkdtemp(prefix="agi-test-pass-")
        try:
            bundle_runtime = os.path.join(tmp, "bundle", "runtime")
            os.makedirs(bundle_runtime)
            with open(os.path.join(bundle_runtime, "verify_runtime.py"), "w") as fh:
                fh.write("import hashlib\n")

            offline_step = {"name": "offline_ok", "source": "test",
                            "expect": "offline", "timeout": 5, "cwd": None,
                            "argv": [sys.executable, "-c", "import sys; sys.exit(0)"]}
            blocked_step = {"name": "blocked_ok", "source": "test",
                            "expect": "network_blocked", "timeout": 5, "cwd": None,
                            "argv": [sys.executable, "-c", "import sys; sys.exit(1)"]}
            with mock.patch.object(agi, "build_probe_steps",
                                    return_value=[offline_step, blocked_step]):
                report = agi.generate_report(tmp)
            self.assertEqual(report["verdict"], "PASS")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FormatSummaryTests(unittest.TestCase):

    def test_summary_names_every_finding_and_every_probe_step(self):
        report = {
            "verdict": "PASS", "reason": None,
            "static_audit": {"findings": [
                {"file": "scripts/x.py", "line": 3, "text": "curl foo",
                 "what": "curl invocation"}]},
            "probe": [{"name": "s1", "source": "src", "outcome": "RAN_OFFLINE",
                       "detail": ""}],
        }
        text = agi.format_summary(report)
        self.assertIn("scripts/x.py:3", text)
        self.assertIn("curl invocation", text)
        self.assertIn("s1", text)
        self.assertIn("RAN_OFFLINE", text)


class RealRepoSanityTest(unittest.TestCase):
    """One anchor against the actual tree this worktree holds: the install
    path this product documents must still be identifiable, and the one
    network call this file's own docstring names by file:line must still be
    where it says it is. A regression that silently renames or removes
    scripts/brother_install.py without updating INSTALL_PATH_FILES shows up
    here, not only in a human reading the diff."""

    def test_real_repo_install_path_identified_and_known_call_found(self):
        result = agi.static_audit(agi.REPO_ROOT)
        self.assertTrue(result["install_path_identified"])
        hits = [f for f in result["findings"]
                if f["file"] == "scripts/brother_install.py"
                and f["what"].startswith("codex plugin marketplace add")]
        self.assertTrue(hits, "expected brother_install.py's marketplace add "
                         "call to be found by the static audit")


if __name__ == "__main__":
    unittest.main()
