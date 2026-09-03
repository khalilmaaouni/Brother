"""The orphan worktree clause: the one gap crash reconcile did not close.

claim_store.reconcile() already reports an abandoned CLAIM. It says nothing
about the LANE a SIGKILLed run left on disk, because nothing paired a lane's
directory back to the claim that made it. worktree_lane.orphan_report() closes
that pairing, and this test drives it the same way test_crash_resume.py drives
reconcile: a real git repository, a real lane created through the module's own
API, and a claim expired by hand to simulate a dead owner.

REPORTS, NEVER DELETES: every assertion below checks the lane directory is
still on disk after the report, because a report that quietly cleaned up would
be indistinguishable from the crash-recovery footgun this estate has a
standing rule against.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store as C  # noqa: E402
import worktree_lane as W  # noqa: E402


def a_repo():
    d = tempfile.mkdtemp(prefix="canon-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    with open(os.path.join(d, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    run("add", "-A")
    run("commit", "-q", "-m", "R0")
    return d


class TheOrphanReport(unittest.TestCase):
    def setUp(self):
        self.repo = a_repo()
        self.store = os.path.join(tempfile.mkdtemp(), "claims.json")

    def test_a_dead_owners_lane_reports_abandoned_and_is_kept(self):
        path, _branch, problem = W.acquire(self.repo, "CR1")
        self.assertFalse(problem, problem)

        claim, problem = C.acquire(self.store, "CR1", "crash-A")
        self.assertEqual(problem, "")
        self.assertIsNotNone(claim)

        # Simulate the dead owner exactly as test_crash_resume.py does: no
        # process is killed here, the lease is just expired by hand, which is
        # the durable-state view a restarting controller would actually see.
        import json
        with open(self.store, encoding="utf-8") as fh:
            data = json.load(fh)
        data["CR1"]["expires_at"] = time.time() - 1
        with open(self.store, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        findings, problem = W.orphan_report(self.repo, self.store)
        self.assertEqual(problem, "")
        self.assertEqual(len(findings), 1, findings)
        finding = findings[0]
        self.assertEqual(finding["classification"], W.ABANDONED)
        self.assertEqual(os.path.realpath(finding["path"]), os.path.realpath(path))
        self.assertEqual(finding["owner"], "crash-A")
        self.assertIn("removed only by a human or by a future unit that names "
                      "it", finding["detail"])

        # THE REPORT NEVER DELETES: the lane is exactly where it was.
        self.assertTrue(os.path.isdir(path))

    def test_a_live_owners_lane_reports_owned(self):
        path, _branch, problem = W.acquire(self.repo, "CR2")
        self.assertFalse(problem, problem)
        claim, problem = C.acquire(self.store, "CR2", "worker-B")
        self.assertEqual(problem, "")

        findings, problem = W.orphan_report(self.repo, self.store)
        self.assertEqual(problem, "")
        self.assertEqual(len(findings), 1, findings)
        finding = findings[0]
        self.assertEqual(finding["classification"], W.OWNED)
        self.assertEqual(os.path.realpath(finding["path"]), os.path.realpath(path))
        self.assertEqual(finding["owner"], "worker-B")
        self.assertTrue(os.path.isdir(path))

    def test_a_lane_with_no_matching_claim_reports_unknown_never_skipped(self):
        # A lane made through the real API, with no claim ever taken for it:
        # a stray directory shaped exactly like a lane, and nothing names it.
        path, _branch, problem = W.acquire(self.repo, "CR3")
        self.assertFalse(problem, problem)

        findings, problem = W.orphan_report(self.repo, self.store)
        self.assertEqual(problem, "")
        self.assertEqual(len(findings), 1, findings)
        finding = findings[0]
        self.assertEqual(finding["classification"], W.UNKNOWN)
        self.assertEqual(os.path.realpath(finding["path"]), os.path.realpath(path))
        self.assertIsNone(finding["owner"])
        self.assertIn(W.NODATA, finding["detail"])
        self.assertTrue(os.path.isdir(path))

    def test_an_unreadable_claim_store_is_no_data_not_a_pass(self):
        broken = os.path.join(tempfile.mkdtemp(), "claims.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        W.acquire(self.repo, "CR4")
        findings, problem = W.orphan_report(self.repo, broken)
        self.assertIsNone(findings)
        self.assertIn(W.NODATA, problem)

    def test_no_lanes_reports_nothing(self):
        findings, problem = W.orphan_report(self.repo, self.store)
        self.assertEqual(problem, "")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
