#!/usr/bin/env python3
"""The grader's screen admits the execution contract (2026-09-27, Codex and Opus): the standard library outside the deny
set, and repository modules whose own imports reach nothing denied. Tests the copy the loop runs, scripts/loop/grade_build.py.
Run: python3 -B scripts/test_grade_build_contract.py"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import grade_build as G  # noqa: E402


def _build(**kw):
    b = {"unit": "Z", "sub": "Z.1", "edits": [], "tests": [], "mutations": [], "done_check": "python3 -m unittest"}
    b.update(kw)
    return b

class TheScreenAdmitsTheExecutionContract(unittest.TestCase):
    """2026-09-27 (Codex and Opus): the standard library outside the deny set, and repository modules whose own imports
    reach nothing denied, pass the screen; one condition per case, each through unsafe(), the grader's entry point."""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="screen-contract-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts", "loop"))
        os.makedirs(os.path.join(self.root, "pkg"))
        for rel, text in (("scripts/loop/pure_lib.py", "import json, os\n"),
                          ("scripts/loop/spawns.py", "import subprocess\n"),
                          ("scripts/loop/via_spawns.py", "import pure_lib\nimport spawns\n"),
                          ("scripts/loop/dynamic.py", "import importlib\nm = importlib.import_module('x')\n"),
                          ("pkg/__init__.py", ""), ("pkg/netty.py", "import socket\n"),
                          ("scripts/loop/shells.py", "import os\ndef go(c):\n    return os.system(c)\n"),
                          ("scripts/loop/shells_from.py", "from os import system\ndef go(c):\n    return system(c)\n")):
            with open(os.path.join(self.root, rel), "w") as fh: fh.write(text)
        self.cwd = os.getcwd(); os.chdir(self.root); self.addCleanup(os.chdir, self.cwd)
        G._REPO_SAFE.clear(); self.addCleanup(G._REPO_SAFE.clear)

    def screen(self, code):
        return G.unsafe(_build(edits=[{"path": "scripts/sample.py", "new_file_content": code}]), runners=set())

    def test_an_ordinary_standard_library_module_passes(self):
        self.assertIsNone(self.screen("import threading\n"))

    def test_a_future_import_passes(self):
        self.assertIsNone(self.screen("from __future__ import annotations\n"))

    def test_a_denied_standard_library_module_is_still_refused(self):
        self.assertIn("socket", self.screen("import socket\n") or "")

    def test_a_pure_repository_module_passes(self):
        self.assertIsNone(self.screen("import pure_lib\n"))

    def test_a_repository_module_that_spawns_processes_is_refused(self):
        self.assertIn("spawns", self.screen("import spawns\n") or "")

    def test_the_refusal_follows_imports_through_the_repository(self):
        self.assertIn("via_spawns", self.screen("import via_spawns\n") or "")

    def test_a_module_that_imports_by_computed_name_is_refused(self):
        self.assertIn("dynamic", self.screen("import dynamic\n") or "")

    def test_a_submodule_named_in_a_from_import_is_screened_too(self):
        self.assertIn("pkg.netty", self.screen("from pkg import netty\n") or "")

    def test_a_repository_module_that_calls_os_system_is_refused(self):
        self.assertIn("shells", self.screen("import shells\n") or "")

    def test_a_repository_module_that_imports_system_by_name_is_refused(self):
        self.assertIn("shells_from", self.screen("import shells_from\n") or "")

    def test_no_credential_shaped_variable_reaches_the_sandbox(self):
        self.assertTrue(all(G.SECRET_ENV.search(k) for k in ("OPENROUTER_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "ANTHROPIC_AUTH_TOKEN")))
        self.assertFalse(any(G.SECRET_ENV.search(k) for k in ("HOME", "PATH", "TMPDIR", "PYTHONDONTWRITEBYTECODE")))

    def test_a_test_frameworks_run_method_passes(self):
        self.assertIsNone(self.screen("def t(case, result):\n    return case.run(result)\n"))
        self.assertIsNone(self.screen("import unittest\nunittest.TestSuite().run(unittest.TestResult())\n"))

    def test_a_bare_run_call_is_still_refused(self):
        self.assertIn("run", self.screen("def run(x):\n    return x\nrun(1)\n") or "")

    def test_self_check_uses_the_graders_contract(self):
        import self_check
        b = _build(edits=[{"path": "scripts/hog_subject.py", "new_file_content": "def f():\n    return 1\n"}],
                   tests=[{"path": "scripts/test_hog_subject.py", "new_file_content": "from scripts.hog_subject import f\n"}])
        self.assertIsNone(self_check.unsafe_reason(b))

    def test_an_unknown_third_party_module_is_refused(self):
        self.assertIn("requests_toolbelt_x", self.screen("import requests_toolbelt_x\n") or "")



class TheGradersSuiteEnvironmentDropsTheRunsKnobs(unittest.TestCase):
    """2026-10-03: grade_build.run built its own environment and kept BROTHER_TRANSPORTS=claude, so L5a-7's bridge
    selftests refused themselves at grade time (7 of 241 red) while landing, through loop_switches.suite_env, ran clean.
    Through run(), the entry point: a run knob, a credential name and a git location never reach the build's suite."""
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="grade-env-")
        self.addCleanup(shutil.rmtree, self.root, True)
        with open(os.path.join(self.root, "probe.py"), "w") as fh:
            fh.write("import os\nfor k in ('BROTHER_TRANSPORTS', 'BROTHER_PIN_MODEL', 'GH_TOKEN', 'GIT_DIR', 'HOME', 'KEEP_ME'):\n"
                     "    print('%s=%s' % (k, os.environ.get(k, 'UNSET')))\n")
        saved = G.sandboxed; G.sandboxed = lambda cmd, root, tmp: cmd   # the fixture runs the probe unwrapped; no network, no provider
        self.addCleanup(setattr, G, "sandboxed", saved)

    def seen(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"BROTHER_TRANSPORTS": "claude", "BROTHER_PIN_MODEL": "opus55", "GH_TOKEN": "x",
                                          "GIT_DIR": "/nowhere", "KEEP_ME": "1"}):
            worst, _tail, each, _moved = G.run(self.root, ["python3 probe.py"])
        self.assertEqual(worst, 0, each)
        return dict(l.split("=", 1) for l in each[0][2].splitlines() if "=" in l)

    def test_the_runs_transport_allowlist_does_not_reach_the_suite(self):
        self.assertEqual(self.seen()["BROTHER_TRANSPORTS"], "UNSET")

    def test_the_runs_model_pin_does_not_reach_the_suite(self):
        self.assertEqual(self.seen()["BROTHER_PIN_MODEL"], "UNSET")

    def test_credentials_and_git_locations_stay_out_and_home_is_the_sandbox(self):
        got = self.seen()
        self.assertEqual((got["GH_TOKEN"], got["GIT_DIR"]), ("UNSET", "UNSET"))
        self.assertTrue(got["HOME"].startswith(os.path.realpath(self.root)) or got["HOME"].startswith(self.root), got["HOME"])

    def test_an_unrelated_variable_is_kept(self):
        self.assertEqual(self.seen()["KEEP_ME"], "1")


if __name__ == "__main__":
    unittest.main()
