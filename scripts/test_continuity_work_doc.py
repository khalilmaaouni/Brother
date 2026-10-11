"""RW1.c: one rule. continuity._work_doc_path asks the engine's lookup
(brother_run._find_work_doc_why) instead of keeping its own copy, so the
capsule and the receipt read the same Work document the engine resumes, and
an interrupted run resumes once past a stray file. Spec:
docs/plan/specs/RW1.md, section RW1.c.

Done check: python3 -B scripts/test_continuity_work_doc.py
"""
import contextlib
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import claim_store  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402
import receipt_door  # noqa: E402
import work_record as WR  # noqa: E402
from test_brother_run import (  # noqa: E402,F401
    BROTHER_RUN, INVOCATION_LOGGING_MODEL, make_repo, setUpModule, sh,
    tearDownModule, write_stub)

OUTCOME = "a1 exists"


def _run_folder(parent, name, cwd, stray=True):
    """A run folder holding a Work document, a marker that points at it, a
    journal, and (by default) one stray .json beside them."""
    run_dir = os.path.join(parent, name)
    rec, problems = WR.create(OUTCOME, [
        {"id": "A1", "title": "create a1", "done_check": "test -f A1.txt",
         "owns": ["A1.txt"]}], store=run_dir)
    assert problems == [], problems
    _br._write_run_target(run_dir, cwd, work_doc_path=rec["path"])
    journal.append(run_dir, "work.created",
                   payload={"work_id": rec["work_id"]})
    if stray:
        with open(os.path.join(run_dir, "diagnostics.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"stray": True}, fh)
    return run_dir, rec["path"]


class _Root(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="brother-continuity-doc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)


class ContinuityAndTheEngineGiveOneAnswer(_Root):
    """REQ-RW1-C-ONE (M-RW1C-COPY): seven folders, one answer each."""

    def _folders(self):
        made = {}
        made["pointer-with-stray"], _ = _run_folder(self.root, "p", self.root)
        missing, doc = _run_folder(self.root, "m", self.root, stray=False)
        os.remove(doc)
        made["pointer-to-missing"] = missing
        legacy1 = os.path.join(self.root, "l1")
        WR.create(OUTCOME, [{"id": "A1", "title": "t", "done_check": "true",
                             "owns": ["a"]}], store=legacy1)
        made["legacy-one"] = legacy1
        legacy2 = os.path.join(self.root, "l2")
        WR.create(OUTCOME, [{"id": "A1", "title": "t", "done_check": "true",
                             "owns": ["a"]}], store=legacy2)
        with open(os.path.join(legacy2, "other.json"), "w") as fh:
            fh.write("{}")
        made["legacy-two"] = legacy2
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        made["empty"] = empty
        made["not-a-directory"] = os.path.join(self.root, "nowhere")
        return made

    def test_the_agreement_loop(self):
        for label, folder in self._folders().items():
            with self.subTest(folder=label):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    got = continuity._work_doc_path(folder)
                self.assertEqual(got, _br._find_work_doc_why(folder)[0])
        pointer = os.path.join(self.root, "p")
        self.assertIsNotNone(continuity._work_doc_path(pointer))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root lists any folder")
    def test_an_unlistable_folder_still_says_so(self):
        """REQ-RW1-C-STDERR (M-RW1C-NO-STDERR)."""
        folder = os.path.join(self.root, "locked")
        WR.create(OUTCOME, [{"id": "A1", "title": "t", "done_check": "true",
                             "owns": ["a"]}], store=folder)
        os.chmod(folder, 0)
        self.addCleanup(os.chmod, folder, 0o755)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(continuity._work_doc_path(folder))
        self.assertIn("continuity: ", err.getvalue())
        self.assertIn(_br.WORK_DOC_UNLISTABLE, err.getvalue())

    def test_an_ambiguous_folder_is_quiet(self):
        """M-RW1C-UNCONDITIONAL-STDERR: only the unlistable case speaks."""
        folders = self._folders()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(continuity._work_doc_path(folders["legacy-two"]))
        self.assertEqual(err.getvalue(), "")


class TheCapsuleAndTheReceiptReadTheSameDocument(_Root):
    """REQ-RW1-C-CAPSULE and REQ-RW1-C-RECEIPT (M-RW1C-COPY)."""

    def test_the_capsule_reads_the_outcome(self):
        run_dir, _doc = _run_folder(self.root, "run", self.root)
        cap, problem = continuity.capsule(run_dir)
        self.assertEqual(problem, "")
        self.assertEqual(cap["objective"], OUTCOME)

    def test_the_receipt_record_reads_the_outcome(self):
        run_dir, _doc = _run_folder(self.root, "run", self.root)
        body = receipt_door.receipt_record(run_dir, [])
        self.assertEqual(body["intent"]["outcome"], OUTCOME)


class R08AnInterruptedRunResumesOncePastAStray(unittest.TestCase):
    """REQ-RW1-R08: the shape of AResumedRunRerunsAnAbandonedClaimUpToTheBound
    (an abandoned claim stands for the interruption) plus one stray file in
    the run folder: one worker call, one landing, one claim, an accurate
    receipt."""

    def test_r08(self):
        tmp = tempfile.mkdtemp(prefix="brother-continuity-r08-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = make_repo(tmp)
        run_dir = os.path.join(tmp, "run")
        rec, problems = WR.create(OUTCOME, [
            {"id": "A1", "title": "create a1", "done_check": "test -f A1.txt",
             "owns": ["A1.txt"]}], store=run_dir)
        self.assertEqual(problems, [])
        _br._write_run_target(run_dir, repo, work_doc_path=rec["path"])
        claims_path = os.path.join(run_dir, "claims.json")
        _claim, problem = claim_store.acquire(
            claims_path, "A1", "dead-owner", attempt=1,
            clock=lambda: time.time() - 5000)
        self.assertEqual(problem, "")
        found, _why = claim_store.reconcile(claims_path)
        self.assertEqual([f["status"] for f in found], ["abandoned"], found)
        with open(os.path.join(run_dir, "diagnostics.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"stray": True}, fh)
        model = write_stub(tmp, "invocation_logging_model.py",
                           INVOCATION_LOGGING_MODEL)
        log_path = os.path.join(tmp, "worker_invocations.log")
        env = dict(os.environ)
        env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable),
                                             shlex.quote(model))
        env["WORKER_INVOCATION_LOG"] = log_path
        proc = sh([sys.executable, BROTHER_RUN, "--resume", run_dir,
                   "--cwd", repo], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (1):", out)
        self.assertTrue(os.path.exists(os.path.join(repo, "A1.txt")), out)
        with open(log_path, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), 1)
        landings = sh(["git", "rev-list", "--no-merges", "--count", "HEAD",
                       "--", "A1.txt"], cwd=repo).stdout.strip()
        self.assertEqual(landings, "1", out)
        with open(claims_path, encoding="utf-8") as fh:
            claims = json.load(fh)
        self.assertEqual((claims["A1"]["state"], claims["A1"]["attempt"]),
                         ("done", 2), claims)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines[-1].startswith("brother_run: receipt: "),
                        lines[-1])
        with open(lines[-1][len("brother_run: receipt: "):],
                  encoding="utf-8") as fh:
            receipt = json.load(fh)
        files = [c.get("file") for c in receipt["scope"]["changed"]]
        self.assertEqual(files.count("A1.txt"), 1, receipt["scope"])
        self.assertEqual(receipt["continuity"]["capsule"]["objective"],
                         OUTCOME)


class R09ARunIsOfferedOnlyToItsOwnRepository(_Root):
    """REQ-RW1-R09 (M-RW1C-DISCOVERY-IGNORES-CWD)."""

    def test_r09(self):
        repo_a = os.path.join(self.root, "A")
        repo_b = os.path.join(self.root, "B")
        os.makedirs(repo_a)
        os.makedirs(repo_b)
        runs = os.path.join(self.root, "docs", "plan", "runs")
        run_dir, _doc = _run_folder(runs, "20261011-run-a", repo_a)
        self.assertEqual(_br.find_unfinished_runs(self.root, repo_b), [])
        found = _br.find_unfinished_runs(self.root, repo_a)
        self.assertEqual([d for d, _o, _r in found], [run_dir])


if __name__ == "__main__":
    unittest.main()
