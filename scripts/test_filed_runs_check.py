"""Driven-backwards tests for scripts/filed_runs_check.py, audit row J2.

Mirrors scripts/test_jbeq_e2e_check.py's own shape: import the module
directly and call main() in-process so KNOWN_CHECKERS can be monkeypatched
per test, and assert the EXIT CODE, never only the printed line.

Every fixture here is its own throwaway git repository under a fresh temp
directory (tempfile.TemporaryDirectory, torn down after); nothing writes
into the real benchmark tree or the real vault.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import filed_runs_check as F  # noqa: E402

FAKE_CHECKER = (
    "import os, sys\n"
    "run_dir = sys.argv[1]\n"
    "data = os.path.join(run_dir, 'data.txt')\n"
    "if os.path.isfile(data):\n"
    "    print('fixture check: PASS')\n"
    "else:\n"
    "    print('fixture check: FAIL (missing data.txt)')\n"
)

WIDGET_PATTERN = re.compile(r'^benchmarks/widget/x/runs/[^/]+$')


def _git(root, *args):
    subprocess.run(["git"] + list(args), cwd=root, check=True,
                    capture_output=True)


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test Runner")
    _git(root, "config", "user.email", "test@example.invalid")


def _write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _commit(root, message="fixture"):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def run_main(*args):
    """Return (exit_code, stdout) for filed_runs_check.main(args)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = F.main(list(args))
    return code, buf.getvalue()


class FixtureCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        _init_repo(self.root)


class Verdicts(FixtureCase):

    def test_a_filed_run_whose_artefact_is_committed_reproduces(self):
        run_dir = os.path.join(self.root, "benchmarks", "widget", "x",
                                "runs", "2026-09-05")
        _write(os.path.join(self.root, "scripts", "fake_checker.py"),
               FAKE_CHECKER)
        _write(os.path.join(run_dir, "data.txt"), "hello\n")
        _write(os.path.join(run_dir, "checker-output.txt"),
               "fixture check: PASS\n")
        _commit(self.root)

        with mock.patch.object(F, "KNOWN_CHECKERS",
                                [(WIDGET_PATTERN, "scripts/fake_checker.py")]):
            code, out = run_main("--root", self.root)

        self.assertEqual(code, 0, out)
        self.assertIn(
            "filed run benchmarks/widget/x/runs/2026-09-05: REPRODUCES", out)

    def test_the_same_run_with_the_artefact_ignored_diverges(self):
        """J2 itself: checker-output.txt commits a PASS the tree cannot
        reproduce because the artefact it reads never reached git."""
        run_dir = os.path.join(self.root, "benchmarks", "widget", "x",
                                "runs", "2026-09-05")
        _write(os.path.join(self.root, "scripts", "fake_checker.py"),
               FAKE_CHECKER)
        _write(os.path.join(self.root, ".gitignore"), "data.txt\n")
        _write(os.path.join(run_dir, "checker-output.txt"),
               "fixture check: PASS\n")
        _commit(self.root)
        # Written AFTER the commit: present on disk, absent from git, so
        # `git archive HEAD` (what the checker actually reads) never sees it.
        _write(os.path.join(run_dir, "data.txt"), "hello\n")

        with mock.patch.object(F, "KNOWN_CHECKERS",
                                [(WIDGET_PATTERN, "scripts/fake_checker.py")]):
            code, out = run_main("--root", self.root)

        self.assertEqual(code, 1, out)
        self.assertIn(
            "filed run benchmarks/widget/x/runs/2026-09-05: DIVERGES "
            "(committed says fixture check: PASS, clean checkout says "
            "fixture check: FAIL (missing data.txt))", out)

    def test_a_partial_manifest_checkpoint_with_matching_hashes_reproduces(self):
        """The reproduction test for a MANIFEST.json checkpoint is the hash
        set, never the verdict word: an honest PARTIAL checkpoint whose
        listed artefacts all hash correctly REPRODUCES, carrying the
        PARTIAL sentence through unchanged rather than comparing it
        against a hash-check result."""
        run_dir = os.path.join(self.root, "benchmarks", "results",
                                "widget-gauntlet", "2026-09-05-checkpoint")
        _write(os.path.join(run_dir, "note.txt"), "partial run notes\n")
        note_hash = hashlib.sha256(b"partial run notes\n").hexdigest()
        manifest = {
            "verdict": "PARTIAL, PENDING TEMPORAL ARM, resume not before later",
            "artefacts": [{"path": "note.txt", "sha256": note_hash}],
        }
        _write(os.path.join(run_dir, "MANIFEST.json"), json.dumps(manifest))
        _commit(self.root)

        code, out = run_main("--root", self.root)

        self.assertEqual(code, 0, out)
        self.assertIn(
            "filed run benchmarks/results/widget-gauntlet/2026-09-05-checkpoint: "
            "REPRODUCES (verdict: PARTIAL, PENDING TEMPORAL ARM, resume not "
            "before later)", out)

    def test_a_manifest_checkpoint_with_one_wrong_hash_diverges(self):
        run_dir = os.path.join(self.root, "benchmarks", "results",
                                "widget-gauntlet", "2026-09-05-checkpoint")
        _write(os.path.join(run_dir, "note.txt"), "partial run notes\n")
        manifest = {
            "verdict": "PARTIAL, PENDING TEMPORAL ARM, resume not before later",
            "artefacts": [{"path": "note.txt", "sha256": "0" * 64}],
        }
        _write(os.path.join(run_dir, "MANIFEST.json"), json.dumps(manifest))
        _commit(self.root)

        code, out = run_main("--root", self.root)

        self.assertEqual(code, 1, out)
        self.assertIn(
            "filed run benchmarks/results/widget-gauntlet/2026-09-05-checkpoint: "
            "DIVERGES (hash mismatch for note.txt)", out)

    def test_a_filed_run_with_no_checker_declared_reads_no_data(self):
        run_dir = os.path.join(self.root, "benchmarks", "widget", "y",
                                "runs", "2026-09-05")
        _write(os.path.join(run_dir, "notes.txt"), "just notes\n")
        _commit(self.root)

        code, out = run_main("--root", self.root)

        self.assertEqual(code, 0, out)
        self.assertIn(
            "filed run benchmarks/widget/y/runs/2026-09-05: "
            "NO-DATA: no checker declared", out)

    def test_an_empty_tree_exits_3(self):
        code, out = run_main("--root", self.root)
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
