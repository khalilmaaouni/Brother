#!/usr/bin/env python3
"""Calibration for the automatic vault index refresh (readiness row E54).

WHY THIS SUITE EXISTS. The point-of-need recall hook serves lessons out of a sqlite index,
and until now nothing refreshed that index. Measured on 2026-09-03: last indexed 79.0 hours
ago, 61 notes unindexed. Every lesson written that week was invisible at exactly the moment
it was needed, and nothing in a session said so, because a stale index and a current one look
identical from the inside.

The properties under test are the ones that make an automatic refresh safe enough to run at
every session start: it is CHEAP when there is nothing to do, it indexes only what changed,
it stops at its budget instead of delaying the session, and every failure is a NO-DATA line
and exit 0 rather than a session that will not start.

Structure mirrors tools/test_bm_vault.py (its own temp vault, HOME moved so INDEX_PATH and the
projects root follow it, the tool driven as a subprocess) and, for the hook case, the module
loader shape of tools/test_vault_recall_hook.py. That loader is written out again here rather
than imported from the other suite, matching how every entry point in this tree owns its own
copy of a gate instead of trusting an import to still do the job tomorrow.

Run: python3 tools/test_vault_index_refresh.py      (unittest output, exit 0 or 1)
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")
HOOK = os.path.join(HERE, "vault_recall_hook.py")
PRODUCT_ROOT = os.path.dirname(HERE)

#: The exact shape the refresh and the hook both print. Pinned on its WORDS, not only its
#: shape: a number is easy to keep and a label is easy to change, and the label is the half a
#: reader acts on ("unindexed" is what tells someone the index is behind).
STATUS_RE = re.compile(
    r"^vault-index: last indexed (?:NEVER|\d+ minutes ago), \d+ notes, \d+ unindexed$")

NOTE = """---
name: %s
description: %s
type: project
---
%s
"""


def _write(path, text):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run(argv, env):
    p = subprocess.run([sys.executable, TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def status_line(out):
    for line in out.splitlines():
        if line.startswith("vault-index: last indexed"):
            return line
    return ""


def _write_consented_config(path):
    """Matches scripts/setup.py's write_config() schema exactly, the same
    fixture tools/test_bm_bash_audit.py's own _write_consented_config uses:
    row E54's refresh gate (tools/test_bm_consent.py) reads the identical
    config through the identical scripts/setup.py loader, so this suite
    drives refresh consented rather than calibrating consent a second
    time here."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump({"setup_complete": True,
                  "vault_path": os.path.dirname(path),
                  "privacy_notice_version": "2026-08-01",
                  "installation_mode": "clone",
                  "security_mode": "standard"}, f)


class TheRefreshOnATempVault(unittest.TestCase):
    """One throwaway vault and one throwaway index per test: these cases mutate the index on
    purpose (a corrupt file, a half-finished pass), so they must not share one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vault-index-refresh-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        os.makedirs(os.path.join(self.tmp, ".claude"))
        _write(os.path.join(self.vault, "a-leaky-file-handle.md"),
               NOTE % ("a-leaky-file-handle", "handles never bound to a name leak",
                       "Use with-open. See reader.py."))
        _write(os.path.join(self.vault, "a-default-argument-binds-early.md"),
               NOTE % ("a-default-argument-binds-early", "the constant is captured at def time",
                       "Pass None and resolve inside. See config.py."))
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp             # moves INDEX_PATH and the projects root
        self.env["BM_VAULT_ROOT"] = self.vault
        # Pins the correction-rule federation at an empty fixture store, so the real approved
        # rules of whatever repository this suite runs inside never leak into the corpus.
        store = os.path.join(self.tmp, "store-root")
        os.makedirs(store)
        self.env["BROTHERMODE_ROOT"] = store
        # E54's own gate (tools/test_bm_consent.py): refresh writes the index on a
        # stranger's machine before consent, so it is checked. This fixture is not
        # about consent, so it is consented up front rather than left to fall
        # through to the NO-DATA refusal.
        self.cfg_path = os.path.join(self.tmp, ".brotherme", "config.json")
        _write_consented_config(self.cfg_path)
        self.env["BROTHERME_CONFIG"] = self.cfg_path
        self.index = os.path.join(self.tmp, ".claude", "bm_vault_index.sqlite3")
        code, out = run(["refresh"], self.env)
        self.assertEqual(code, 0, "the first refresh exited %d: %s" % (code, out[:400]))
        self.first_out = out

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_the_first_refresh_builds_the_index_and_says_so(self):
        self.assertTrue(os.path.exists(self.index), "no index was written: %s" % self.first_out)
        self.assertIn("2 new", self.first_out, self.first_out)
        self.assertIn("0 unindexed", status_line(self.first_out), self.first_out)

    def test_02_a_fresh_index_is_not_stale_and_nothing_is_reindexed(self):
        # THE PROPERTY THAT MAKES THIS SAFE AT EVERY SESSION START: when the index already
        # holds every note at its current mtime, the run prints the age line and does no work.
        # "note(s)" is cmd_index's own summary word, so its absence is the proof that the
        # indexer never ran.
        before = os.path.getmtime(self.index)
        code, out = run(["refresh"], self.env)
        self.assertEqual(code, 0)
        self.assertNotIn("note(s)", out,
                         "a fresh index was reindexed anyway, which is the cost this check "
                         "exists to avoid:\n%s" % out)
        self.assertEqual(os.path.getmtime(self.index), before,
                         "a not-stale refresh wrote to the index")
        self.assertIn("0 unindexed", status_line(out), out)

    def test_03_the_status_line_words_are_pinned(self):
        code, out = run(["status-line"], self.env)
        self.assertEqual(code, 0)
        line = status_line(out)
        self.assertTrue(STATUS_RE.match(line),
                        "the status line changed shape or wording, and the hook and the "
                        "session start both print it: %r" % line)
        self.assertIn("2 notes", line, line)

    def test_04_status_line_is_read_only(self):
        """The hook runs this on an edit; it must never index and never write."""
        new = os.path.join(self.vault, "a-third-lesson.md")
        _write(new, NOTE % ("a-third-lesson", "written after the index was built", "late.py."))
        before = os.path.getmtime(self.index)
        code, out = run(["status-line"], self.env)
        self.assertEqual(code, 0)
        self.assertIn("1 unindexed", status_line(out), out)
        self.assertEqual(os.path.getmtime(self.index), before,
                         "status-line wrote to the index; it is the read-only half")

    def test_05_a_new_note_makes_it_stale_and_one_run_indexes_exactly_that_note(self):
        new = os.path.join(self.vault, "a-third-lesson.md")
        _write(new, NOTE % ("a-third-lesson", "written after the index was built", "late.py."))
        code, out = run(["refresh"], self.env)
        self.assertEqual(code, 0)
        self.assertIn("1 new, 0 refreshed", out,
                      "the refresh did not index exactly the one changed note:\n%s" % out)
        line = status_line(out)
        self.assertIn("3 notes", line, line)
        self.assertIn("0 unindexed", line, line)

    def test_06_a_zero_second_budget_stops_with_the_remaining_count_and_exits_zero(self):
        _write(os.path.join(self.vault, "a-third-lesson.md"),
               NOTE % ("a-third-lesson", "written after the index was built", "late.py."))
        code, out = run(["refresh", "--budget", "0"], self.env)
        self.assertEqual(code, 0, "a budget overrun must never fail a session start:\n%s" % out)
        self.assertIn("stopped at the 0.0s budget", out, out)
        line = status_line(out)
        self.assertIn("1 unindexed", line,
                      "the remaining count must be visible after an unfinished pass: %r" % line)
        self.assertIn("2 notes", line,
                      "an unfinished pass deleted or added notes; it must do neither: %r" % line)

    def test_07_an_unfinished_pass_keeps_the_old_last_indexed_stamp(self):
        """The trap this guards: stamping indexed_at on an unfinished pass would make the next
        start believe the index is current, and the notes it never reached would stay missing
        for good."""
        _write(os.path.join(self.vault, "a-third-lesson.md"),
               NOTE % ("a-third-lesson", "written after the index was built", "late.py."))
        code, before = run(["status-line"], self.env)
        self.assertEqual(code, 0)
        run(["refresh", "--budget", "0"], self.env)
        code, after = run(["status-line"], self.env)
        self.assertEqual(code, 0)
        self.assertEqual(status_line(before), status_line(after),
                         "an unfinished pass moved the last-indexed stamp")

    def test_08_a_corrupt_index_is_no_data_and_exit_zero_on_both_commands(self):
        _write(self.index, "this is not a sqlite database\n")
        for argv in (["refresh"], ["status-line"]):
            code, out = run(argv, self.env)
            self.assertEqual(code, 0, "%s exited %d on a corrupt index; a session start is "
                                      "never blocked by the index:\n%s" % (argv, code, out))
            self.assertIn("vault-index: NO-DATA:", out,
                          "%s stayed silent on a corrupt index:\n%s" % (argv, out))

    def test_09_an_unconfigured_vault_is_no_data_never_a_guessed_path(self):
        env = dict(self.env)
        env.pop("BM_VAULT_ROOT", None)
        env.pop("BROTHERMODE_VAULT", None)
        code, out = run(["refresh"], env)
        self.assertEqual(code, 0)
        self.assertIn("vault-index: NO-DATA:", out, out)


class ThePointOfNeedHookShowsTheIndexAge(unittest.TestCase):
    """The second half of E54: the age has to be visible where the work happens. The hook is
    driven for real here (real hook, real bm_vault.py, temp index), because the defect being
    closed was a mechanism that looked healthy while telling nobody anything."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vault-index-hook-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        os.makedirs(os.path.join(self.tmp, ".claude"))
        _write(os.path.join(self.vault, "a-leaky-file-handle.md"),
               NOTE % ("a-leaky-file-handle", "handles never bound to a name leak",
                       "Use with-open. See reader.py."))
        self.cfg_path = os.path.join(self.tmp, ".brotherme", "config.json")
        _write_consented_config(self.cfg_path)
        self.env = {"HOME": self.tmp, "BM_VAULT_ROOT": self.vault,
                    "BROTHERMODE_ROOT": self.tmp, "BM_TOOLS": PRODUCT_ROOT,
                    "BROTHERME_CONFIG": self.cfg_path}
        env = dict(os.environ)
        env.update(self.env)
        code, out = run(["refresh"], env)
        self.assertEqual(code, 0, out[:400])
        self.index = os.path.join(self.tmp, ".claude", "bm_vault_index.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load_hook(self):
        """Import the hook fresh under this test's environment, because TOOL is resolved at
        import time. The environment stays applied for the caller (unlike the loader in
        test_vault_recall_hook.py, which restores it) since the hook shells out to bm_vault.py
        and that subprocess must see the temp HOME too."""
        os.environ.update(self.env)
        spec = importlib.util.spec_from_file_location("vault_recall_hook_index_age", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._consented = lambda: True        # consent is calibrated in its own suite
        mod.SEEN = os.path.join(self.tmp, "seen")
        return mod

    def _drive(self, mod, path="/tmp/reader.py", session="s1"):
        saved_in, saved_out, saved_err = sys.stdin, sys.stdout, sys.stderr
        sys.stdin = io.StringIO(json.dumps(
            {"session_id": session, "tool_input": {"file_path": path}}))
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            rc = mod.main()
            return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdin, sys.stdout, sys.stderr = saved_in, saved_out, saved_err

    def test_10_the_hook_names_the_index_age_without_reindexing(self):
        saved = dict(os.environ)
        try:
            mod = self._load_hook()
            before = os.path.getmtime(self.index)
            rc, _out, err = self._drive(mod)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(rc, 0, "the hook must never block an edit")
        line = status_line(err)
        self.assertTrue(STATUS_RE.match(line),
                        "the hook printed no index age line; a stale index stays invisible, "
                        "which is the whole defect. stderr was:\n%s" % err)
        self.assertEqual(os.path.getmtime(self.index), before,
                         "the hook indexed; it is the read-only side of this row")

    def test_11_the_age_line_is_said_once_per_session_not_on_every_edit(self):
        saved = dict(os.environ)
        try:
            mod = self._load_hook()
            _rc, _out, first = self._drive(mod, path="/tmp/reader.py")
            _rc, _out, second = self._drive(mod, path="/tmp/other.py")
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertIn("vault-index:", first, first)
        self.assertNotIn("vault-index:", second,
                         "the age line repeated on the next edit; a line repeated on every "
                         "edit becomes wallpaper, and wallpaper is not read")


if __name__ == "__main__":
    unittest.main(verbosity=2)
