#!/usr/bin/env python3
"""test_catalog_end_state.py: the five catalogs after the 1.1.0 cut (U8).

Two kinds of test live here, on purpose in one file:

  CutApplied: skipped, with the reason printed, until the cut runs
  scripts/retire_catalogs.py (a skip is never a pass: the red instrument
  before the cut is `retire_catalogs.py --check`, exit 1 today). It names
  the exact end state of every catalog, so nobody has to remember it.
  RetireScript: green today. Copies the current catalogs into a temp root,
  runs the script there, and proves it produces the end state, is idempotent,
  blocks on an unreadable catalog, and reports NOT DONE / DONE correctly.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import retire_catalogs as RC  # noqa: E402

CLAUDE = os.path.join(".claude-plugin", "marketplace.json")
CURSOR = os.path.join(".cursor-plugin", "marketplace.json")
CODEX = os.path.join(".agents", "plugins", "marketplace.json")


def _names(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return [p["name"] for p in json.load(fh)["plugins"]]


def _end_state_holds(root):
    """The whole end state, as one predicate both halves share."""
    problems = []
    if _names(root, CLAUDE) != ["brother"]:
        problems.append("%s lists %s" % (CLAUDE, _names(root, CLAUDE)))
    with open(os.path.join(root, CLAUDE), encoding="utf-8") as fh:
        doc = json.load(fh)
    entry = doc["plugins"][0]
    if entry.get("source", {}).get("path") != "bundle":
        problems.append("%s: brother must ship from bundle" % CLAUDE)
    if entry.get("source", {}).get("ref") != "v" + doc["metadata"]["version"]:
        problems.append("%s: brother's ref must be v<metadata.version>" % CLAUDE)
    if entry.get("version") != doc["metadata"]["version"]:
        problems.append("%s: brother's version must equal metadata.version" % CLAUDE)
    if _names(root, CURSOR) != ["brother"]:
        problems.append("%s lists %s" % (CURSOR, _names(root, CURSOR)))
    if _names(root, CODEX) != ["brother"]:
        problems.append("%s lists %s" % (CODEX, _names(root, CODEX)))
    for rel in RC.DELETE:
        if os.path.exists(os.path.join(root, rel)):
            problems.append("%s still exists" % rel)
    return problems


@unittest.skipIf(RC.pending(ROOT),
                 "the U8 catalog edit is not applied yet; the red instrument "
                 "until the cut is `python3 scripts/retire_catalogs.py --check` "
                 "(exit 1 today). This class runs green once the cut applies it.")
class CutApplied(unittest.TestCase):
    """SKIPPED (never a pass) before the cut, GREEN after
    `python3 scripts/retire_catalogs.py`.
    Also names the two companion edits the cut makes in the same commit:
    tests/test_surface.py's "at least two plugins" assertion flips to
    exactly one, and docs/CHARTER.md's sentence that the three products
    "can never be made one install unit" is rewritten."""

    def test_every_catalog_lists_only_brother(self):
        self.assertEqual(_end_state_holds(ROOT), [])

    def test_donecheck_u8_agrees(self):
        proc = subprocess.run([sys.executable, "-B",
                               os.path.join(HERE, "donecheck_u8.py")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class RetireScript(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="retire-catalogs-")
        for rel in list(RC.CATALOGS) + [CODEX] + list(RC.DELETE):
            src = os.path.join(ROOT, rel)
            if os.path.exists(src):
                dst = os.path.join(self.tmp, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run([sys.executable, "-B", RC.__file__,
                               "--root", self.tmp] + list(args),
                              capture_output=True, text=True)

    def test_apply_reaches_the_end_state_and_is_idempotent(self):
        first = RC.apply(self.tmp)
        self.assertEqual(_end_state_holds(self.tmp), [])
        self.assertEqual(RC.apply(self.tmp), [], "second run must change nothing")
        self.assertTrue(first or _end_state_holds(ROOT) == [])

    def test_check_reports_not_done_then_done(self):
        before = self._run("--check")
        if _end_state_holds(ROOT):
            self.assertEqual(before.returncode, 1, before.stdout)
            self.assertIn("NOT DONE", before.stdout)
        run = self._run()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        after = self._run("--check")
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertIn("DONE", after.stdout)

    def test_indentation_and_other_fields_survive(self):
        with open(os.path.join(self.tmp, CLAUDE), encoding="utf-8") as fh:
            before = json.load(fh)
        RC.apply(self.tmp)
        with open(os.path.join(self.tmp, CLAUDE), encoding="utf-8") as fh:
            text = fh.read()
        after = json.loads(text)
        self.assertTrue(text.startswith('{\n  "name"'), text[:20])
        for key in before:
            if key != "plugins":
                self.assertEqual(before[key], after[key], key)
        self.assertIn([p for p in before["plugins"] if p["name"] == "brother"][0],
                      after["plugins"])

    def test_unreadable_catalog_blocks(self):
        with open(os.path.join(self.tmp, CLAUDE), "w") as fh:
            fh.write("{not json")
        proc = self._run()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA", proc.stderr)
        self.assertEqual(self._run("--check").returncode, 2)

    def test_a_catalog_without_brother_is_refused(self):
        path = os.path.join(self.tmp, CURSOR)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["plugins"] = [p for p in doc["plugins"] if p["name"] != "brother"]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        proc = self._run()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("exactly one brother entry", proc.stderr)


    def _git(self, *args):
        return subprocess.run(["git", "-C", self.tmp, "-c", "user.name=t",
                               "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
                              + list(args), capture_output=True, text=True)

    def _git_tree(self):
        for rel in RC.DELETE:
            dst = os.path.join(self.tmp, rel)
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write('{"name": "x", "plugins": []}\n')
        self.assertEqual(self._git("init", "-q").returncode, 0)
        self._git("add", "-A")
        done = self._git("commit", "-q", "-m", "seed")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_deletion_is_staged_in_a_git_tree(self):
        """checksums.sh lists `git ls-files`; an unstaged delete makes it
        refuse the cut ("listed as a tracked file but is not a regular file")."""
        self._git_tree()
        changed = RC.apply(self.tmp, "1.1.0")
        for rel in RC.DELETE:
            self.assertIn(rel, changed)
        tracked = self._git("ls-files").stdout.splitlines()
        for rel in RC.DELETE:
            self.assertNotIn(rel.replace(os.sep, "/"), tracked)
        status = self._git("status", "--porcelain").stdout.splitlines()
        for rel in RC.DELETE:
            self.assertIn("D  " + rel.replace(os.sep, "/"), status)

    def test_a_failed_stage_is_reported_not_silent(self):
        self._git_tree()
        lock = os.path.join(self.tmp, ".git", "index.lock")
        with open(lock, "w") as fh:
            fh.write("")
        proc = self._run("--apply", "--version", "1.1.0")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("deleted but", proc.stderr)


if __name__ == "__main__":
    unittest.main()
