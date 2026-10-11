"""RW1.b: new runs record which Work document is theirs.

_write_run_target gains a keyword only `work_doc_path`. Given, it must name a
Work document directly inside the run folder under a plain .json name, and
the marker carries its basename as `work_document`; an invalid value REFUSES
(decision RW1-WRITER, 2026-10-11), it is never silently left out. Omitted,
the call is the explicit legacy shape and writes exactly the old keys. The
marker is written through work_record.write_record (atomic), and a resume
never rewrites it. Spec: docs/plan/specs/RW1.md, section RW1.b, as tightened
by decisions-v5/SYNTHESIS.md.

Done check: python3 -B scripts/test_brother_run_resume_pointer.py
"""
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
import brother_run as _br  # noqa: E402
import work_record as WR  # noqa: E402
from test_brother_run import (  # noqa: E402,F401
    WRITER_MODEL, make_repo, setUpModule, sh, tearDownModule, write_stub)

BROTHER_RUN = os.path.join(HERE, "brother_run.py")


def _marker(run_dir):
    with open(os.path.join(run_dir, _br.TARGET_FILENAME),
              encoding="utf-8") as fh:
        return json.load(fh)


class _Folder(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="brother-resume-pointer-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run_dir = os.path.join(self.root, "run")
        rec, problems = WR.create("a1 exists", [
            {"id": "A1", "title": "create a1", "done_check": "test -f A1.txt",
             "owns": ["A1.txt"]}], store=self.run_dir)
        self.assertEqual(problems, [])
        self.doc = rec["path"]
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)


class TheMarkerNamesTheDocument(_Folder):
    """REQ-RW1-B-KEY (M-RW1B-ABS-PATH, M-RW1B-DROP-CONTRACT)."""

    def test_the_key_is_the_basename_beside_every_old_key(self):
        _br._write_run_target(self.run_dir, self.repo, work_doc_path=self.doc)
        self.assertEqual(_marker(self.run_dir),
                         {"cwd": self.repo,
                          "work_document": os.path.basename(self.doc)})
        self.assertEqual(_br._find_work_doc_why(self.run_dir), (self.doc, ""))

    def test_a_contract_block_stays_beside_the_key(self):
        contract_path = os.path.join(self.root, "contract.json")
        _br._write_run_target(self.run_dir, self.repo,
                              contract_path=contract_path,
                              contract={"language": "en", "question": "q?"},
                              work_doc_path=self.doc)
        self.assertEqual(_marker(self.run_dir), {
            "cwd": self.repo,
            "contract": {"path": contract_path, "language": "en",
                         "question": "q?"},
            "work_document": os.path.basename(self.doc)})


class TheLegacyCallIsUnchanged(_Folder):
    """REQ-RW1-B-LEGACYSHAPE, a pin that passes on the base: no pointer
    argument writes exactly the old keys."""

    def test_no_pointer_argument_writes_the_old_keys(self):
        _br._write_run_target(self.run_dir, self.repo)
        self.assertEqual(_marker(self.run_dir), {"cwd": self.repo})
        contract_path = os.path.join(self.root, "c.json")
        _br._write_run_target(self.run_dir, self.repo, contract_path,
                              {"language": "fr", "question": "q"})
        self.assertEqual(_marker(self.run_dir), {
            "cwd": self.repo, "contract": {
                "path": contract_path, "language": "fr", "question": "q"}})

    def test_the_pointer_is_keyword_only(self):
        with self.assertRaises(TypeError):
            _br._write_run_target(self.run_dir, self.repo, None, None,
                                  self.doc)


class TheMarkerIsWrittenAtomically(_Folder):
    """REQ-RW1-B-ATOMIC (M-RW1B-PLAIN-OPEN)."""

    def test_the_one_atomic_writer_carries_the_marker(self):
        with mock.patch.object(_br.work_record, "write_record",
                               wraps=WR.write_record) as spy:
            _br._write_run_target(self.run_dir, self.repo,
                                  work_doc_path=self.doc)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args[0][0],
                         os.path.join(self.run_dir, _br.TARGET_FILENAME))

    def test_a_failed_write_leaves_the_old_marker_whole(self):
        _br._write_run_target(self.run_dir, self.repo)
        path = os.path.join(self.run_dir, _br.TARGET_FILENAME)
        with open(path, "rb") as fh:
            before = fh.read()
        with mock.patch.object(_br.work_record, "write_record",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                _br._write_run_target(self.run_dir, self.repo,
                                      work_doc_path=self.doc)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)


class AnInvalidPointerRefuses(_Folder):
    """REQ-RW1-B-SKIP as decided (RW1-WRITER, M-RW1B-INVALID-OMITTED): a
    supplied pointer that is not a Work document directly in the run folder
    under a plain name refuses, naming why, and writes nothing."""

    def test_each_invalid_pointer_refuses_and_writes_nothing(self):
        other = os.path.join(self.root, "other")
        rec, _p = WR.create("elsewhere", [
            {"id": "B1", "title": "b", "done_check": "true",
             "owns": ["b"]}], store=other)
        claims = os.path.join(self.run_dir, "claims.json")
        with open(claims, "w", encoding="utf-8") as fh:
            fh.write("{}")
        junk = os.path.join(self.run_dir, "junk.json")
        with open(junk, "w", encoding="utf-8") as fh:
            fh.write('{"rows": []}')
        os.makedirs(os.path.join(self.run_dir, "dir.json"))
        marker = os.path.join(self.run_dir, _br.TARGET_FILENAME)
        for bad in (None, "", 7, os.path.join(self.run_dir, "dir.json"),
                    rec["path"], claims, junk,
                    os.path.join(self.run_dir, "W-missing.json")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as caught:
                    _br._write_run_target(self.run_dir, self.repo,
                                          work_doc_path=bad)
                self.assertIn("target.json was not written", str(caught.exception))
                self.assertFalse(os.path.lexists(marker))


class ANewRunResumesPastStrayFiles(unittest.TestCase):
    """REQ-RW1-B-WIRED and REQ-RW1-B-ONCE (M-RW1B-NO-CALLSITE,
    M-RW1B-REWRITE-ON-RESUME): one real run records its document, two stray
    .json files land in its folder, and --resume still finds the document
    and leaves the marker exactly as it was (same bytes, same file)."""

    def test_r01_r02_end_to_end(self):
        tmp = tempfile.mkdtemp(prefix="brother-resume-pointer-e2e-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = make_repo(tmp)
        dec = write_stub(tmp, "decomposer.py", textwrap.dedent("""
            import json, sys
            sys.stdin.read()
            print(json.dumps([{"id": "A1", "objective": "create A1",
                               "done_check": "test -f A1.txt",
                               "writes": ["A1.txt"], "deps": []}]))
        """))
        model = write_stub(tmp, "writer_model.py", WRITER_MODEL)
        env = dict(os.environ)
        for var in (tuple(brother_paths.CLAUDE_MARKER_VARS)
                    + tuple(brother_paths.CODEX_MARKER_VARS)):
            env.pop(var, None)
        env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable),
                                           shlex.quote(dec))
        env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable),
                                             shlex.quote(model))
        proc = sh([sys.executable, BROTHER_RUN, "make A1", "--cwd", repo,
                   "--runs-root", tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        runs = os.path.join(tmp, "docs", "plan", "runs")
        (name,) = os.listdir(runs)
        run_dir = os.path.join(runs, name)
        doc_name = _marker(run_dir).get("work_document")
        self.assertTrue(doc_name and os.path.isfile(
            os.path.join(run_dir, doc_name)), _marker(run_dir))
        for stray in ("diagnostics.json", "delivery_summary.json"):
            with open(os.path.join(run_dir, stray), "w",
                      encoding="utf-8") as fh:
                json.dump({"stray": stray}, fh)
        marker = os.path.join(run_dir, _br.TARGET_FILENAME)

        def fingerprint():
            st = os.stat(marker)
            with open(marker, "rb") as fh:
                return (hashlib.sha256(fh.read()).hexdigest(), st.st_ino,
                        st.st_mtime_ns)
        before = fingerprint()
        proc = sh([sys.executable, BROTHER_RUN, "--resume", run_dir,
                   "--cwd", repo, "--runs-root", tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("resuming run", out)
        self.assertNotIn("could not find the Work document", out)
        self.assertEqual(fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
