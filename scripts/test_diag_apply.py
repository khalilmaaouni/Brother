#!/usr/bin/env python3
"""Admission tests use only isolated plans and mocked child processes."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

LOOP = Path(__file__).resolve().parent / "loop"
sys.path.insert(0, str(LOOP))


# Admission reads the unit's spec and refuses a done check the landing gate refuses (2026-09-29), so the fixture
# unit names a spec whose sections each carry one accepted command. Removed at exit with its directory.
_SPEC_DIR = tempfile.TemporaryDirectory(prefix="diag-apply-spec-")
SPEC = os.path.join(_SPEC_DIR.name, "X.md")
with open(SPEC, "w", encoding="utf-8") as _fh:
    _fh.write("# X\n### X.a\nDone check: `python3 -B scripts/test_x.py`\n### X.ab\nDone check: `python3 -B scripts/test_x.py`\n")


def plan(state="PARTIAL", evidence="", subs=None):
    return {"units": [{"id": "X", "state": state, "spec": SPEC, "sub_units": subs or ["X.a"], "evidence": evidence}]}


class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)

    def pool(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("import started a process")), mock.patch.object(subprocess, "Popen", side_effect=AssertionError("import started a worker")):
            return importlib.import_module("runner_pool")

    def test_import_has_no_plan_or_process_side_effect(self):
        self.pool()
        self.assertFalse((self.root / ".claude").exists())

    def test_admission_states(self):
        pool = self.pool()
        self.assertEqual(pool.admissible(plan(), "X.a"), "")
        self.assertIn("DONE", pool.admissible(plan("DONE"), "X.a"))
        self.assertIn("landed", pool.admissible(plan(evidence="X.a landed yesterday"), "X.a"))
        for p, sub in ((None,"X.a"), ({"units":[]},"X.a"), (plan(), "X.b"),
                       (plan(state=None),"X.a"), ({"units":[None]},"X.a")):
            self.assertTrue(pool.admissible(p, sub))
        self.assertEqual(pool.admissible(plan(evidence="X.ab landed yesterday"), "X.a"), "")
        self.assertEqual(pool.admissible(plan(evidence="X.a not landed"), "X.a"), "")
        self.assertTrue(pool.admissible({"units": plan()["units"] * 2}, "X.a"))

    def test_admissible_refuses_corrupt_state(self):
        pool = self.pool()
        refusal = pool.admissible(plan("DONE "), "X.a")
        self.assertTrue(refusal)
        self.assertIn("corrupt", refusal)

    def test_diag_corrupt_state_never_starts(self):
        self.assertEqual(self.diagnostic(plan("DONE ")), 0)

    def diagnostic(self, p, refusal=None):
        self.pool()
        d = importlib.import_module("diag_apply")
        folder = self.root / "diag/out"; folder.mkdir(parents=True)
        (folder / "X.a.json").write_text(json.dumps({"fact":"present", "prove":"grep token sample", "expect":"token", "hint":"local"}))
        board = self.root / "docs/plan"; board.mkdir(parents=True)
        (board / "BROTHER-1.1.0-LAUNCH-WBS.json").write_text(json.dumps(p))
        (self.root / ".claude/evidence/unit-runs").mkdir(parents=True)
        old = Path.cwd(); os.chdir(self.root); self.addCleanup(os.chdir, old)
        def run(argv, **kw):
            # a READABLE table with no runner in it: an empty ps output is NO-DATA and restarts nothing (RR lane C)
            return types.SimpleNamespace(stdout="COMMAND\n/sbin/launchd\n" if argv[0] == "ps" else "token", returncode=0)
        with mock.patch.object(d.subprocess, "run", side_effect=run), mock.patch.object(d.subprocess,"Popen") as start, contextlib.redirect_stdout(io.StringIO()):
            if refusal is None:
                d.main([str(folder.parent)])
            else:
                with mock.patch.object(d,"admissible", return_value=refusal) as guard:
                    d.main([str(folder.parent)]); guard.assert_called()
            return start.call_count

    def test_diag_done_never_starts(self):
        self.assertEqual(self.diagnostic(plan("DONE")), 0)

    def test_diag_unknown_never_starts(self):
        self.assertEqual(self.diagnostic({"units":[]}), 0)

    def test_diag_landed_never_starts(self):
        self.assertEqual(self.diagnostic(plan(evidence="X.a landed yesterday")), 0)

    def test_diag_open_starts_nothing_and_writes_the_note(self):
        """Plan E step 2b: a proven fact for an open unit starts no runner; it becomes the unit's note for the next brief."""
        notes = self.root / "notes"
        import diag_notes
        with mock.patch.object(diag_notes, "NOTES", str(notes)):
            self.assertEqual(self.diagnostic(plan()), 0)
        self.assertIn("X.a: VERIFIED FACT: present local", (notes / "X.txt").read_text())

    def test_diag_obeys_shared_refusal(self):
        self.assertEqual(self.diagnostic(plan(), "NO-DATA plan"), 0)

    def test_salvage_done_does_not_promote(self):
        self.salvage_case(plan("DONE"), 0)

    def test_salvage_corrupt_state_does_not_promote(self):
        self.salvage_case(plan("DONE "), 0)

    def test_salvage_obeys_shared_refusal(self):
        self.salvage_case(plan(), 0, "NO-DATA plan")

    def test_salvage_open_promotes(self):
        self.salvage_case(plan(), 1)

    def salvage_case(self, p, expected, refusal=None):
        self.pool(); s=importlib.import_module("salvage")
        board=self.root / "board.json"; board.write_text(json.dumps(p))
        c={"sub":"X.a", "action":"PROMOTE"}
        with mock.patch.dict(os.environ,{"SALVAGE_PLAN":str(board),"SALVAGE_RUNS":str(self.root)}), mock.patch.object(sys,"argv",["salvage.py","promote"]), mock.patch.object(s,"candidates",return_value=[c]), mock.patch.object(s,"plan_view",return_value=[c]), mock.patch.object(s,"promote") as promote, contextlib.redirect_stdout(io.StringIO()):
            if refusal is None: s.main()
            else:
                with mock.patch.object(s,"admissible",return_value=refusal) as guard:
                    s.main(); guard.assert_called()
            self.assertEqual(promote.call_count,expected)


if __name__ == "__main__":
    unittest.main()
