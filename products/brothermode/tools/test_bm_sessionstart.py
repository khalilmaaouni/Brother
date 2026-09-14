#!/usr/bin/env python3
"""Tests for the row-V2 slice of bm_sessionstart.py: the first-run nudge that
names the missing vault binding.

Every test here runs bm_sessionstart.py as a real subprocess against a FAKE
HOME and a FAKE project root, the same technique test_bm_consent.py already
uses for this same file, so the assertions cover what a founder's terminal
actually sees rather than an in-process shortcut. Nothing here writes to the
real ~/.brotherme, ~/.claude or ~/BrotherModeVault.

Run: python3 tools/test_bm_sessionstart.py      (unittest output, exit 0 or 1)
"""
import io
import json
import os
import shutil
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
    # A packager (scripts/export_public.py, make_benchmark_bundle.py) can
    # copy this test without scripts/tmp_sandbox.py beside it. Say so rather
    # than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONSTART = os.path.join(HERE, "bm_sessionstart.py")
NUDGE = "No memory vault is bound yet"


def _clean_env(home):
    """Same shape as test_bm_consent.py's own _clean_env: real os.environ
    with HOME redirected and every override that could leak the real
    machine's vault or fence state stripped, so this file can never
    accidentally pass by inheriting the checkout's own live config."""
    env = dict(os.environ)
    env["HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k in ("BROTHERMODE_VAULT", "BM_VAULT_ROOT", "BROTHERMODE_ROOT",
              "BROTHERME_CONFIG", "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID",
              "CLAUDE_SESSION_ID"):
        env.pop(k, None)
    return env


def _write_consented_config(path):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "setup_complete": True,
            "vault_path": os.path.join(d, "BrotherModeVault"),
            "privacy_notice_version": "2026-08-01",
            "installation_mode": "clone",
            "security_mode": "standard",
        }, fh)


def _write_vault_config(home, vault):
    cfg_dir = os.path.join(home, ".claude")
    os.makedirs(cfg_dir, exist_ok=True)
    with io.open(os.path.join(cfg_dir, "bm_vault.json"), "w", encoding="utf-8") as fh:
        json.dump({"vault": vault}, fh)


class FirstRunVaultNudge(unittest.TestCase):
    """(row V2) a fresh project, once consented, is told plainly that no
    vault is bound yet, and told nothing once one is."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-sessionstart-vault-")
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.home)
        os.makedirs(self.project)
        self.env = _clean_env(self.home)
        _write_consented_config(
            os.path.join(self.home, ".brotherme", "config.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stdout(self):
        r = subprocess.run(
            [sys.executable, SESSIONSTART], cwd=self.project, env=self.env,
            input="{}", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_fresh_project_with_no_vault_bound_gets_the_nudge(self):
        out = self._stdout()
        self.assertIn(NUDGE, out)

    def test_fresh_project_with_a_vault_already_bound_gets_no_nudge(self):
        _write_vault_config(self.home, os.path.join(self.home, "SomeVault"))
        out = self._stdout()
        self.assertNotIn(NUDGE, out)

    def test_established_project_never_sees_the_nudge_even_unbound(self):
        """The nudge rides the same first-run gate as the "new project"
        line: an established project (one with a plan or a queue) is never
        told this again, matching the sibling line's own "a nag that fires
        every session stops being read" reasoning."""
        subprocess.run(["git", "init", "-q", self.project],  # sbe: allow-silent test fixture git init; the assertion below is what actually detects a wrong fixture
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.makedirs(os.path.join(self.project, "docs", "plan"), exist_ok=True)
        with io.open(os.path.join(self.project, "docs", "plan", "PLAN.md"),
                     "w", encoding="utf-8") as fh:
            fh.write("# plan\n")
        out = self._stdout()
        self.assertNotIn("BrotherMode: new project", out)
        self.assertNotIn(NUDGE, out)


class Night0912BmSessionstart(unittest.TestCase):
    """Whitebox, in-process (unlike the subprocess-driven classes above):
    the defect is inside main()'s own control flow after bm_handover.py
    detect succeeds but every line it printed was a first-run-suppressed
    NO-DATA, which a real subprocess fixture cannot reach without also
    faking bm_handover.py's own state; monkeypatching main()'s module
    globals is the direct way to drive that one branch."""

    def test_first_run_suppressed_no_data_does_not_report_failure(self):
        import contextlib
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "bm_sessionstart_ut", SESSIONSTART)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)

        m._load_bm_repo_scope = lambda: None
        m._is_first_run = lambda: True
        m._no_vault_bound = lambda: False

        def fake_run(args, stdin_text=None, capture=False, keep_stderr=False):
            base = os.path.basename(str(args[0]))
            if base == "setup.py":
                return 0, ""
            if base == "bm_handover.py" and len(args) > 1 and args[1] == "detect":
                return 0, ("NO-DATA: no handover pack exists yet\n"
                           "NO-DATA: no handover zip exists yet\n")
            return 0, ""

        m._run = fake_run

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("{}")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                m.main()
        finally:
            sys.stdin = old_stdin

        out = buf.getvalue()
        self.assertNotIn("could not run", out)
        self.assertNotIn("baton ceremony", out)


if __name__ == "__main__":
    unittest.main()
