#!/usr/bin/env python3
"""FC1.a (docs/plan/specs/FC1.md): a preflight that did not clear ends the
rehearsal before any worktree or chain command, and a preflight row that reads
NO-DATA spares the expensive rows exactly as a REFUSED row does.

Measured 2026-10-11: a 1.1.1 rehearsal printed its preflight refusal at
03:16:02, then made a worktree and ran the whole chain for 50 more minutes
before writing the NOT-READY it had already reached.

HOW. cut.main --check runs on a scripted runner keyed the way test_cut.py
keys commands; the composed case runs cut_rehearsal.main in a throwaway git
repository whose child cut.py is cut.main itself, called in process. The
preflight cases drive cut_preflight.run_all through test_cut_preflight's own
fixture.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_preflight as P  # noqa: E402
import cut_rehearsal as CR  # noqa: E402
import test_cut_preflight as TP  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

V = "9.9.9"
HEAD = "e" * 40
CHAIN_LABELS = ("git worktree", "retire_catalogs.py", "cut_v1.0.0.sh", "plugin_bump_gate.py",
                "required_fast.sh", "refresh_cut.py", "release_invariant.py")
GREEN = {"git rev-parse HEAD": (0, HEAD + "\n"), "git status --porcelain": (0, "")}


def label(cmd):
    """git plus two words; the basename of cmd[1] for sh and interpreters (test_cut._key's shape)."""
    if cmd[0] == "git":
        return "git " + " ".join(cmd[1:3])
    return os.path.basename(cmd[1])


def chain_calls(labels):
    return [l for l in labels for c in CHAIN_LABELS if l == c or l.startswith(c + " ")]


class Runner(object):
    """Answers from `answers` (label -> (code, stdout)), default exit 0 and no output; records every label."""

    def __init__(self, answers):
        self.answers = dict(answers)
        self.labels = []

    def __call__(self, cmd, **kw):
        key = label(list(cmd))
        self.labels.append(key)
        code, out = self.answers.get(key, (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")


def _inner(argv, runner, root):
    with mock.patch.object(C, "_load_bm_store", return_value=(None, None, "absent")), \
            mock.patch.object(C, "release_paths", return_value=["scripts/"]):
        return C.main(argv, root=root, runner=runner)


def run_check(answers, tmp_root):
    """(code, stdout, labels) of cut.main --check on a scripted runner; any mkdtemp lands in tmp_root."""
    r = Runner(dict(GREEN, **answers))
    out = io.StringIO()
    with mock.patch.object(tempfile, "tempdir", tmp_root), contextlib.redirect_stdout(out):
        code = _inner(["--check", "--version", V], r, "/fake/root")
    return code, out.getvalue(), r.labels


class TheRehearsalStopsAtThePreflight(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-cut-rehearsal-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_refused_preflight_runs_no_chain(self):
        code, out, labels = run_check({"cut_preflight.py": (1, "cut-preflight: REFUSED\n")}, self.tmp)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("rehearsal: not run", out)
        self.assertIn("NOT READY for %s" % V, out)
        self.assertIn(C.PREFLIGHT_REFUSED, out)
        self.assertEqual(chain_calls(labels), [], labels)

    def test_no_data_and_a_missing_preflight_stop_the_same_way(self):
        for code_in in (2, 127):
            with self.subTest(exit=code_in):
                code, out, labels = run_check({"cut_preflight.py": (code_in, "")}, self.tmp)
                self.assertEqual(code, C.EXIT_REFUSED)
                self.assertIn("rehearsal: not run", out)
                self.assertEqual(chain_calls(labels), [], labels)

    def test_any_other_reason_still_runs_the_chain(self):
        code, out, labels = run_check({"git status --porcelain": (0, " M README.md\n")}, self.tmp)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("rehearsal: not run", out)
        self.assertIn("cut_v1.0.0.sh", labels)
        self.assertIn("required_fast.sh", labels)

    def test_all_green_is_ready_after_the_chain(self):
        code, out, labels = run_check({}, self.tmp)
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertIn("READY for %s" % V, out)
        self.assertIn("cut_v1.0.0.sh", labels)

    def test_the_stop_line_does_not_call_a_no_data_row_a_refusal(self):
        code, out, labels = run_check({"cut_preflight.py": (2, "cut-preflight: NO-DATA\n")}, self.tmp)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("did not clear (a row REFUSED or read NO-DATA)", out)
        self.assertNotIn("cut_preflight.py refused", out)
        self.assertEqual(chain_calls(labels), [], labels)


class TheComposedRehearsal(unittest.TestCase):
    """cut_rehearsal.main around a child cut.py --check whose preflight refuses: NOT-READY, no chain, minutes."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix="test-cut-rehearsal-rig-")
        self.addCleanup(shutil.rmtree, base, True)
        self.repo, self.ev, self.bs = (os.path.join(base, n) for n in ("repo", "evidence", "scratch"))
        home = os.path.join(base, "home")
        for d in (self.repo, home, self.bs):
            os.makedirs(d)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "BROTHER_")) and k != "XDG_CONFIG_HOME")
        env.update(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test", BROTHER_SCRATCH=self.bs,
                   BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        files = {"scripts/cut.py": "cut\n"}
        for rel in CR.SCRIPT_DIRS:
            files[rel + "/m.py"] = "m\n"
        for rel, text in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            proc = C._run(["git"] + args, self.repo)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.fake_root = os.path.join(base, "fake-root")
        self.inner = Runner(dict(GREEN, **{"cut_preflight.py": (1, "cut-preflight: REFUSED\n")}))
        self.worktrees = []

    def outer(self, cmd, **kw):
        cmd = list(cmd)
        if cmd[0] != "git":
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(tempfile, "tempdir", self.bs):
                code = _inner(cmd[2:], self.inner, self.fake_root)
            return subprocess.CompletedProcess(cmd, code, out.getvalue(), "")
        if cmd[1:3] == ["worktree", "add"]:
            self.worktrees.append(cmd)
            os.makedirs(cmd[-2])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["worktree", "remove"]:
            self.worktrees.append(cmd)
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return C._run(cmd, kw.get("cwd"), timeout=kw.get("timeout"))

    def test_a_refused_preflight_is_recorded_not_ready_in_minutes(self):
        out = io.StringIO()
        started = time.monotonic()
        with contextlib.redirect_stdout(out):
            code = CR.main(["--version", V], root=self.repo, runner=self.outer)
        seconds = time.monotonic() - started
        last = out.getvalue().strip().splitlines()[-1]
        self.assertTrue(last.startswith("REHEARSAL NOT-READY"), out.getvalue())
        self.assertEqual(code, 1)
        self.assertNotIn("cut_v1.0.0.sh", self.inner.labels)
        self.assertEqual(chain_calls(self.inner.labels), [], self.inner.labels)
        self.assertLess(seconds, 600)


class ANoDataRowSparesTheExpensiveRows(unittest.TestCase):
    """The preflight's two expensive rows run only when every earlier row is OK."""

    def run_all(self, answers):
        tp = TP.CheapestFirst("test_all_green_is_exit_zero_and_the_virgin_gate_ran")
        self.addCleanup(tp.doCleanups)
        return tp._all(answers)

    def assert_spared(self, answers, row):
        res, built = self.run_all(answers)
        rows = dict((n, (v, d)) for v, n, d in res)
        self.assertEqual(rows[row][0], P.NODATA, res)
        for name in ("plugin-bumps", "virgin-gate"):
            with self.subTest(row=row, spared=name):
                self.assertEqual(rows[name][0], P.NODATA, res)
                self.assertIn("not run", rows[name][1])
        self.assertEqual(built, [], "an expensive row built a tree after a NO-DATA row")
        self.assertEqual(P.exit_code(res), P.EXIT_NODATA, res)

    def test_an_unreadable_exception_audit_spares_both(self):
        self.assert_spared({"battery_exception_audit.py": (2, "NO-DATA\n")}, "exceptions")

    def test_every_other_cheap_no_data_row_spares_both(self):
        for answers, row in (({"close_ceremony_check.py": (2, "NO-DATA\n")}, "pack-clock"),
                             ({"readiness_gate.py": (0, "not json\n")}, "readiness-rows"),
                             ({"git log": (128, "fatal: bad revision\n")}, "changelog-text"),
                             ({"git ls-remote": (128, "fatal: unreachable\n")}, "public-remote")):
            with self.subTest(row=row):
                self.assert_spared(answers, row)

    def test_only_ok_rows_clear(self):
        self.assertTrue(P._cleared([(P.OK, "a", ""), (P.OK, "b", "")]))
        self.assertTrue(P._cleared([]))
        self.assertFalse(P._cleared([(P.OK, "a", ""), (P.NODATA, "b", "")]))
        self.assertFalse(P._cleared([(P.REFUSED, "a", "")]))
        self.assertFalse(P._cleared([(P.OK, "a", ""), ("MAYBE", "b", "")]))


if __name__ == "__main__":
    unittest.main()
