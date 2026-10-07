#!/usr/bin/env python3
"""test_cut_bump_commit: CV1 (2026-09-30), scripts/cut_v1.0.0.sh step "== 2s."
dies on a no-op bump.

WHAT WAS WRONG. The manifests already read 1.1.0 before the 1.1.0 cut (they
were bumped ahead of the tag), so step 1 changed nothing, step 2r regenerated
nothing, and step 2s ran `git commit -q -m ...` on a clean tree: git exits 1
with "nothing to commit", `set -e` (line 15) stops the script, and the cut dies
one step before the release note. The fix at the source: when the staged
tree is empty the step records "already at <version>, nothing to commit" and
continues; a real bump, whole or partial (some carriers already at the target,
the rest not), still commits.

HOW THIS IS TESTED, the same shape as test_cut_invariant_step.py: the live
2s block is extracted from the script by its "== 2s." and "== 2b." lines, so
a future edit is tested as it actually reads, then run under `set -e` in a
throwaway git repository with a marker echoed after it. Three fixtures, one
condition each: a bumped tree commits and continues; an already bumped (clean)
tree prints the record and continues without a commit; a partially bumped tree
(one carrier changed, one already at the target) commits exactly the changed
carrier. The partial bump itself, every carrier reaching the target whatever
it read before, is scripts/version_source.py's contract and is proven here on
its own fixture as well.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CUT_SCRIPT = os.path.join(HERE, "cut_v1.0.0.sh")
VERSION_SOURCE = os.path.join(HERE, "version_source.py")
MARKER = "STEP_2S_CONTINUED"
VERSION = "1.1.0"

sys.path.insert(0, HERE)
import test_version_source as VS  # noqa: E402  the fixture builder, one definition


def bump_commit_block():
    """The exact 2s block of the live cut script: from its banner up to the 2b banner."""
    with open(CUT_SCRIPT, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('echo "== 2s.'))
    end = next(i for i, l in enumerate(lines) if l.startswith('echo "== 2b.'))
    block = lines[start:end]
    assert any("git commit" in l for l in block), block
    return "\n".join(block)


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True).stdout


class TheBumpCommitStep(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="cut-2s-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        git(self.repo, "init", "-q")
        for name, body in (("a.json", '{"version": "1.0.9"}\n'), ("b.json", '{"version": "1.0.9"}\n')):
            with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")

    def run_block(self):
        script = "set -e\nVERSION=%s\nstep_clock() { :; }\n%s\necho %s\n" % (VERSION, bump_commit_block(), MARKER)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["sh", "-c", script], cwd=self.repo, capture_output=True, text=True, env=env)

    def commits(self):
        return git(self.repo, "rev-list", "--count", "HEAD").strip()

    def test_a_bumped_tree_commits_and_continues(self):
        for name in ("a.json", "b.json"):
            with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
                fh.write('{"version": "%s"}\n' % VERSION)
        proc = self.run_block()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(MARKER, proc.stdout)
        self.assertEqual(self.commits(), "2")
        self.assertIn(VERSION, git(self.repo, "log", "-1", "--format=%s"))
        self.assertNotIn("nothing to commit", proc.stdout)

    def test_an_already_bumped_tree_records_it_and_continues(self):
        proc = self.run_block()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(MARKER, proc.stdout)
        self.assertIn("already at %s, nothing to commit" % VERSION, proc.stdout)
        self.assertEqual(self.commits(), "1")

    def test_a_partially_bumped_tree_commits_the_rest(self):
        with open(os.path.join(self.repo, "b.json"), "w", encoding="utf-8") as fh:
            fh.write('{"version": "%s"}\n' % VERSION)
        proc = self.run_block()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(MARKER, proc.stdout)
        self.assertEqual(self.commits(), "2")
        self.assertEqual(git(self.repo, "show", "--name-only", "--format=", "HEAD").split(), ["b.json"])

    def test_version_source_finishes_a_partial_bump(self):
        """Step 1's contract behind the partial case: a tree where some carriers already carry the target
        and others do not ends with every carrier at the target and no drift."""
        tmp = Path(tempfile.mkdtemp(prefix="cut-2s-vs-"))
        self.addCleanup(shutil.rmtree, str(tmp), ignore_errors=True)
        VS.build_fixture(tmp)
        first = subprocess.run([sys.executable, VERSION_SOURCE, "--write", "--version", VERSION, "--root", str(tmp)],
                               capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        # Put one carrier back on the old version: the tree is now partially bumped.
        codex = tmp / "bundle" / ".codex-plugin" / "plugin.json"
        doc = json.loads(codex.read_text())
        doc["version"] = "1.0.9"
        codex.write_text(json.dumps(doc, indent=2) + "\n")
        drift = subprocess.run([sys.executable, VERSION_SOURCE, "--check", "--root", str(tmp)],
                               capture_output=True, text=True)
        self.assertEqual(drift.returncode, 1, drift.stdout)
        again = subprocess.run([sys.executable, VERSION_SOURCE, "--write", "--version", VERSION, "--root", str(tmp)],
                               capture_output=True, text=True)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(json.loads(codex.read_text())["version"], VERSION)
        clean = subprocess.run([sys.executable, VERSION_SOURCE, "--check", "--root", str(tmp)],
                               capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
