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

FR1.c (2026-10-14, C3): class TestFR1C pins the other end of the same script. Step 2b2 runs a PLAIN export dry run
(no --tag-time-checks) and the chain's export-real-home row (cut.export_rows) is the one run that carries it, with
--prove-required-fast, on the bytes that ship. The 2b2 block is extracted by its banners and run under set -e with a
fake python3 that logs its argv; the chain rows are read through a fake runner and a captured emit_row, so nothing
builds a tree or touches the rehearsal evidence folder.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


TAG_TIME_FLAG = "--tag-time-checks"


def tag_time_block(script=CUT_SCRIPT):
    """The exact 2b2 block of the cut script: from its banner up to the 2c banner. A script with no 2b2 banner, no
    2c banner after it, or no export_public.py line between them raises: an empty block must never pass."""
    with open(script, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh.read().splitlines()]
    starts = [i for i, l in enumerate(lines) if l.startswith('echo "== 2b2.')]
    if len(starts) != 1:
        raise AssertionError("the cut script has %d '== 2b2.' banners, expected 1" % len(starts))
    ends = [i for i, l in enumerate(lines) if i > starts[0] and l.startswith('echo "== 2c.')]
    if not ends:
        raise AssertionError("the cut script has no '== 2c.' banner after the 2b2 banner")
    block = lines[starts[0]:ends[0]]
    if not any("scripts/export_public.py" in l for l in block):
        raise AssertionError("the 2b2 block holds no export_public.py line: %r" % block)
    return "\n".join(block)


def executable_lines(block):
    """The lines of a shell block that run: blanks, comments and echo/printf lines are not executable."""
    out = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("echo ", "echo\t", "printf ")) or line == "echo":
            continue
        out.append(line)
    return out


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


class TestFR1C(unittest.TestCase):
    """FR1.c (C3): one tag time dry run per rehearsal. Step 2b2 is plain; the chain's export-real-home row carries
    --tag-time-checks once; the virgin-gate row never does; the hand-run STOP text still names the full dry run."""

    def run_2b2(self):
        tmp = tempfile.mkdtemp(prefix="fr1c-2b2-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log = os.path.join(tmp, "argv.log")
        fake = os.path.join(tmp, "bin")
        os.mkdir(fake)
        with open(os.path.join(fake, "python3"), "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\necho "$@" >> "$FR1C_LOG"\n')
        os.chmod(os.path.join(fake, "python3"), 0o755)
        script = "set -e\nVERSION=%s\nstep_clock() { :; }\n%s\necho %s\n" % (VERSION, tag_time_block(), "STEP_2B2_DONE")
        env = dict(os.environ, PATH=fake + os.pathsep + os.environ.get("PATH", ""), FR1C_LOG=log)
        proc = subprocess.run(["sh", "-c", script], cwd=tmp, capture_output=True, text=True, env=env)
        logged = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                logged = [l.rstrip("\n") for l in fh]
        return proc, logged

    def chain_rows(self):
        """(commands the fake runner saw, [(row name, recorded argv)]) from cut.export_rows with its default checks."""
        import cut
        import cut_preflight
        home = tempfile.mkdtemp(prefix="fr1c-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        os.mkdir(os.path.join(home, ".claude"))
        cmds, emitted = [], []

        def runner(cmd, **kwargs):
            cmds.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def emit(name, argv, key, home_kind, verdict, seconds, text):
            emitted.append((name, list(argv)))
            return {}

        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(cut, "emit_row", emit), \
                mock.patch.object(cut_preflight, "export_tree_builder", lambda root, cache=False: (lambda dest: None)):
            cut.export_rows(HERE, VERSION, runner=runner, key="k" * 64)
        return cmds, emitted

    def test_step_2b2_runs_a_plain_dry_run(self):
        proc, logged = self.run_2b2()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("STEP_2B2_DONE", proc.stdout)
        self.assertEqual(logged, ["scripts/export_public.py --dry-run"])

    def test_no_executable_line_of_the_cut_script_carries_the_tag_time_flag(self):
        with open(CUT_SCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(executable_lines(text), "the extractor read no executable line of the cut script")
        self.assertEqual([l for l in executable_lines(text) if TAG_TIME_FLAG in l], [])

    def test_a_comment_or_echo_naming_the_flag_does_not_count(self):
        block = "\n".join([
            "# the old step ran python3 scripts/export_public.py --dry-run %s" % TAG_TIME_FLAG,
            'echo "run: python3 scripts/export_public.py --dry-run %s"' % TAG_TIME_FLAG,
            "  echo  'again %s'" % TAG_TIME_FLAG,
            "",
            "python3 scripts/export_public.py --dry-run",
        ])
        self.assertEqual(executable_lines(block), ["python3 scripts/export_public.py --dry-run"])
        hidden = block + "\npython3 scripts/export_public.py --dry-run %s\n" % TAG_TIME_FLAG
        self.assertEqual([l for l in executable_lines(hidden) if TAG_TIME_FLAG in l],
                         ["python3 scripts/export_public.py --dry-run %s" % TAG_TIME_FLAG])

    def test_the_chains_export_real_home_row_carries_the_tag_time_flag_once(self):
        cmds, emitted = self.chain_rows()
        carrying = [c for c in cmds if TAG_TIME_FLAG in c]
        self.assertEqual(len(carrying), 1, cmds)
        self.assertEqual(carrying[0].count(TAG_TIME_FLAG), 1)
        self.assertIn("--prove-required-fast", carrying[0])
        self.assertTrue(carrying[0][1].endswith("export_public.py"), carrying[0])
        argv = dict(emitted)["export-real-home"]
        self.assertEqual(argv.count(TAG_TIME_FLAG), 1, argv)
        self.assertIn("--prove-required-fast", argv)

    def test_the_virgin_gate_row_does_not_carry_it(self):
        cmds, emitted = self.chain_rows()
        gate = [c for c in cmds if c[0] == "sh" and c[1].endswith("required_fast.sh")]
        self.assertEqual(len(gate), 1, cmds)
        self.assertNotIn(TAG_TIME_FLAG, " ".join(gate[0]))
        self.assertNotIn(TAG_TIME_FLAG, dict(emitted)["virgin-gate"])

    def test_the_hand_run_stop_text_still_names_the_full_dry_run(self):
        with open(CUT_SCRIPT, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        stop = next(i for i, l in enumerate(lines) if l.startswith('echo "== STOP.'))
        text = "\n".join(lines[stop:])
        self.assertIn("python3 scripts/export_public.py --dry-run --prove-required-fast %s" % TAG_TIME_FLAG, text)

    def test_a_script_without_the_2b2_banner_fails_loudly(self):
        with open(CUT_SCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        tmp = tempfile.mkdtemp(prefix="fr1c-nobanner-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        renamed = os.path.join(tmp, "cut.sh")
        with open(renamed, "w", encoding="utf-8") as fh:
            fh.write(text.replace('echo "== 2b2.', 'echo "== 2b-two.'))
        with self.assertRaises(AssertionError):
            tag_time_block(renamed)
        no_end = os.path.join(tmp, "cut_noend.sh")
        with open(no_end, "w", encoding="utf-8") as fh:
            fh.write(text.replace('echo "== 2c.', 'echo "== 2c-renamed.'))
        with self.assertRaises(AssertionError):
            tag_time_block(no_end)
        self.assertIn("export_public.py", tag_time_block())


if __name__ == "__main__":
    unittest.main(verbosity=2)
