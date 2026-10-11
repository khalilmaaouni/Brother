#!/usr/bin/env python3
"""QS1.d: the vault refresh run at every session start is quiet when healthy (docs/plan/specs/QS1.md).

WHAT IS PINNED. `bm_vault.py refresh` prints nothing without the switch: not the "no vault root configured" line, not
the refresh's progress lines, not the healthy status and "Vault loaded" lines. It still does the work (a later verbose
refresh reports every note indexed), and with BROTHER_VERBOSE_START=1 the lines come back. Failure lines are printed in
every mode by cmd_refresh's own exception branches, which this change does not touch.

HOW. The real file, as a subprocess, in a fake HOME with a consented config, with and without a configured fixture
vault of two notes. Nothing touches the real ~/.claude, ~/.brotherme or a vault.

Python 3.9 and 3.13, standard library only, no network.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT_TOOL = os.path.join(HERE, "..", "products", "brothermode", "tools", "bm_vault.py")


class QuietVaultRefresh(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-quiet-start-vault-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        self.vault = os.path.join(self.tmp, "vault")
        for d in (os.path.join(self.home, ".brotherme"), os.path.join(self.home, ".claude"), self.project, self.vault):
            os.makedirs(d)
        with io.open(os.path.join(self.home, ".brotherme", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"setup_complete": True, "vault_path": self.vault, "privacy_notice_version": "2026-08-01",
                       "installation_mode": "clone", "security_mode": "standard"}, fh)
        for name, body in (("one.md", "# One\n\nfirst note\n"), ("two.md", "# Two\n\nsecond note\n")):
            with io.open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
                fh.write(body)

    def bind_vault(self):
        with io.open(os.path.join(self.home, ".claude", "bm_vault.json"), "w", encoding="utf-8") as fh:
            json.dump({"vault": self.vault}, fh)

    def refresh_lines(self, **extra):
        env = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(extra)
        r = subprocess.run([sys.executable, VAULT_TOOL, "refresh"], cwd=self.project, env=env, input=b"{}",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-600:])
        return [ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]

    def test_no_vault_is_quiet_without_the_switch(self):
        self.assertEqual(self.refresh_lines(), [])

    def test_no_vault_line_returns_with_the_switch(self):
        out = "\n".join(self.refresh_lines(BROTHER_VERBOSE_START="1"))
        self.assertIn("no vault root configured", out)

    def test_first_refresh_is_quiet_and_still_indexes(self):
        self.bind_vault()
        self.assertEqual(self.refresh_lines(), [], "a quiet first refresh printed")
        verbose = "\n".join(self.refresh_lines(BROTHER_VERBOSE_START="1"))
        self.assertIn(", 2 notes, 0 unindexed", verbose, "the quiet refresh did not index the two notes")
        self.assertIn("Vault loaded", verbose)

    def test_healthy_refresh_is_quiet(self):
        self.bind_vault()
        self.refresh_lines(BROTHER_VERBOSE_START="1")
        self.assertEqual(self.refresh_lines(), [], "a healthy refresh printed without the switch")

    def test_maintainer_switch_is_the_same_switch(self):
        self.bind_vault()
        self.refresh_lines()
        out = "\n".join(self.refresh_lines(BROTHERMODE_MAINTAINER="1"))
        self.assertIn("vault-index:", out)

    def test_unreadable_index_status_prints_without_the_switch(self):
        """QS1.md: every NO-DATA line about an unreadable index prints in every mode. The fixture binds an empty
        vault (nothing behind, so the quiet healthy branch is the one taken) over an index whose meta table is a
        view with no v column, so the status line itself is 'NO-DATA: the index could not be read'."""
        empty = os.path.join(self.tmp, "empty-vault")
        os.makedirs(empty)
        with io.open(os.path.join(self.home, ".claude", "bm_vault.json"), "w", encoding="utf-8") as fh:
            json.dump({"vault": empty}, fh)
        con = sqlite3.connect(os.path.join(self.home, ".claude", "bm_vault_index.sqlite3"))
        con.execute("CREATE VIEW meta AS SELECT 1 AS k")
        con.commit()
        con.close()
        out = "\n".join(self.refresh_lines())
        self.assertIn("vault-index: NO-DATA: the index could not be read", out)

    def test_unmeasured_unindexed_count_prints_without_the_switch(self):
        """A status line whose unindexed count is NO-DATA (the notes table could not be read after a refresh) is not
        the healthy shape, so a quiet start prints it. Reaching that state needs the count to fail after a successful
        refresh, so cmd_refresh, the entry point, runs in process with _unindexed answering None, its own
        could-not-read value."""
        saved_env, saved_stdin = dict(os.environ), sys.stdin
        try:
            for name in ("BROTHER_VERBOSE_START", "BROTHERMODE_MAINTAINER", "BROTHERMODE_VAULT", "BROTHERME_CONFIG",
                         "CLAUDE_CONFIG_DIR"):
                os.environ.pop(name, None)
            os.environ.update(HOME=self.home, BM_VAULT_ROOT=self.vault)
            spec = importlib.util.spec_from_file_location("bm_vault_quiet_under_test", VAULT_TOOL)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(mod.INDEX_PATH.startswith(self.home), mod.INDEX_PATH)
            mod._unindexed = lambda con, roots: None
            sys.stdin = io.StringIO("{}")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(mod.cmd_refresh({}), 0)
        finally:
            os.environ.clear()
            os.environ.update(saved_env)
            sys.stdin = saved_stdin
        self.assertIn("NO-DATA unindexed", out.getvalue())

    def test_only_exactly_one_opens_the_switch(self):
        self.bind_vault()
        self.refresh_lines()
        for value in ("0", "true", " 1", "yes"):
            self.assertEqual(self.refresh_lines(BROTHER_VERBOSE_START=value), [], "value %r opened it" % value)


if __name__ == "__main__":
    unittest.main(verbosity=1)
