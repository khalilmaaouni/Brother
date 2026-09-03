#!/usr/bin/env python3
"""Tests for bm_vault_lock, on a throwaway fixture vault (just needs a .git/ dir).

Run: python3 tools/test_bm_vault_lock.py      (unittest output, exit 0 or 1)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(TOOL_DIR, "bm_vault_lock.py")


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class VaultLockTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="bm_vault_lock_test_")
        os.makedirs(os.path.join(self.vault, ".git"))
        self.lock_path = os.path.join(self.vault, ".git", "vault-writer.lock")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_acquire_then_check_reports_active_with_right_session(self):
        code, out = run(["acquire", "--vault", self.vault,
                          "--session", "session-a", "--note", "writing notes"])
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(self.lock_path))

        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("holder: session-a", out)
        self.assertIn("ACTIVE", out)
        self.assertNotIn("STALE", out)

    def test_release_by_wrong_session_refuses_and_lock_survives(self):
        run(["acquire", "--vault", self.vault, "--session", "session-a", "--note", ""])

        code, out = run(["release", "--vault", self.vault, "--session", "session-b"])
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.isfile(self.lock_path))

        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("holder: session-a", out)

    def test_release_by_right_session_removes_lock(self):
        run(["acquire", "--vault", self.vault, "--session", "session-a", "--note", ""])

        code, out = run(["release", "--vault", self.vault, "--session", "session-a"])
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.isfile(self.lock_path))

        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "NONE")

    def test_non_object_json_never_crashes(self):
        # valid JSON that is not an object (a list) must be treated as no lock, not
        # crash. Regression test: json.load happily parses "[]", and lock.get(...)
        # on a list raised AttributeError before this fix.
        with open(self.lock_path, "w") as f:
            json.dump([], f)

        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("NONE", out, out)
        self.assertNotIn("Traceback", out)

        code, out = run(["release", "--vault", self.vault, "--session", "anyone"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("Traceback", out)

    def test_backdated_lock_reports_stale(self):
        run(["acquire", "--vault", self.vault, "--session", "session-a", "--note", ""])
        old = (datetime.now(timezone.utc) - timedelta(seconds=14400 + 60)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(self.lock_path) as f:
            lock = json.load(f)
        lock["acquired"] = old
        with open(self.lock_path, "w") as f:
            json.dump(lock, f)

        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertIn("holder: session-a", out)
        self.assertIn("STALE", out)
        self.assertNotIn("ACTIVE", out)


# Reached through the variable, never spelled out: the docs suite refuses a vault
# path on the live surface, and a hardcoded one is wrong anyway for any reader
# whose vault is somewhere else.
_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
HOOK_SOURCE = os.path.join(_VAULT, "99-System", "Scripts", "pre-commit-hook.sh")


@unittest.skipUnless(os.path.isfile(HOOK_SOURCE),
                      "the real vault pre-commit-hook.sh is not present on this machine")
class VaultLockHookTests(unittest.TestCase):
    """Exercises the REAL installed hook script end to end, against a throwaway git
    repo: not a reimplementation of its logic. This is the coverage the adversarial
    review found missing, since every other test here drives the Python tool
    directly and none of them prove the warn-but-never-block behavior actually
    lives where a real commit would hit it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="bm_vault_lock_hook_test_")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.name", "t"], check=True)
        hook_dst = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        shutil.copy(HOOK_SOURCE, hook_dst)
        os.chmod(hook_dst, 0o755)
        self.env = dict(os.environ, BM_TOOLS=os.path.dirname(TOOL_DIR))

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _commit(self, message, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        p = subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", message],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")

    def test_active_foreign_lock_warns_but_commit_succeeds(self):
        code, out = run(["acquire", "--vault", self.repo,
                          "--session", "holder-session", "--note", "mid-write"])
        self.assertEqual(code, 0, out)

        with open(os.path.join(self.repo, "note.txt"), "w") as f:
            f.write("ordinary content, not a vault note, this repo has no graph gate deps\n")
        subprocess.run(["git", "-C", self.repo, "add", "note.txt"], check=True)

        code, out = self._commit("add note", {"BM_SESSION_ID": "committing-session"})
        self.assertEqual(code, 0, out)
        self.assertIn("WARNING", out, out)
        self.assertIn("holder-session", out, out)
        self.assertIn("committing-session", out, out)

    def test_no_lock_no_warning(self):
        with open(os.path.join(self.repo, "note.txt"), "w") as f:
            f.write("ordinary content\n")
        subprocess.run(["git", "-C", self.repo, "add", "note.txt"], check=True)

        code, out = self._commit("add note", {"BM_SESSION_ID": "any-session"})
        self.assertEqual(code, 0, out)
        self.assertNotIn("WARNING", out, out)


if __name__ == "__main__":
    unittest.main()
