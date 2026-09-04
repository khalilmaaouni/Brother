"""What per-writer isolation must keep true.

The acceptance test is the directive's own: two units run at once with distinct
working directories and branches, neither writes canonical, and the failure of
one does not alter the other's workspace. Driven against a real git repository,
because isolation asserted in a docstring is exactly the level-0 state this
replaced.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claim_store as C  # noqa: E402
import worktree_lane as W  # noqa: E402


def a_repo():
    d = tempfile.mkdtemp(prefix="canon-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    with open(os.path.join(d, "canonical.txt"), "w", encoding="utf-8") as fh:
        fh.write("untouched\n")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return d


def branch_of(path):
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()


class TheAcceptanceTest(unittest.TestCase):
    """Section 8 of the directive, driven rather than described."""

    def setUp(self):
        self.repo = a_repo()
        self.lanes = W.Lanes(self.repo, ["U1", "U2"])

    def test_isolation_is_established_for_both(self):
        self.assertTrue(self.lanes.isolated, self.lanes.why())

    def test_distinct_working_directories(self):
        p1, p2 = self.lanes.path_for("U1"), self.lanes.path_for("U2")
        self.assertNotEqual(p1, p2)
        self.assertTrue(os.path.isdir(p1) and os.path.isdir(p2))

    def test_distinct_branches(self):
        self.assertNotEqual(branch_of(self.lanes.path_for("U1")),
                            branch_of(self.lanes.path_for("U2")))

    def test_neither_writes_canonical(self):
        for uid, name in (("U1", "from_u1.txt"), ("U2", "from_u2.txt")):
            with open(os.path.join(self.lanes.path_for(uid), name), "w",
                      encoding="utf-8") as fh:
                fh.write("x\n")
        canon = [c for c in os.listdir(self.repo) if not c.startswith(".")]
        self.assertEqual(canon, ["canonical.txt"])

    def test_destroying_one_lane_does_not_alter_the_other(self):
        p1, p2 = self.lanes.path_for("U1"), self.lanes.path_for("U2")
        with open(os.path.join(p2, "from_u2.txt"), "w", encoding="utf-8") as fh:
            fh.write("y\n")
        shutil.rmtree(p1)
        self.assertTrue(os.path.exists(os.path.join(p2, "from_u2.txt")))
        self.assertEqual(branch_of(p2), "lane/U2")

    def test_canonical_content_is_unchanged_throughout(self):
        with open(os.path.join(self.lanes.path_for("U1"), "canonical.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("a worker rewrote this in its own lane\n")
        with open(os.path.join(self.repo, "canonical.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "untouched\n")


class ItFailsClosedRatherThanSharing(unittest.TestCase):
    """A system that silently falls back to the unsafe thing under load fails
    exactly when nobody is watching."""

    def test_concurrency_drops_to_one_when_a_lane_cannot_be_made(self):
        lanes = W.Lanes(a_repo(), ["U3"], root="/proc/definitely-not-writable")
        self.assertFalse(lanes.isolated)
        self.assertEqual(lanes.safe_concurrency(5), 1)

    def test_it_says_why_rather_than_dropping_silently(self):
        lanes = W.Lanes(a_repo(), ["U3"], root="/proc/definitely-not-writable")
        why = lanes.why()
        self.assertIn("concurrency drops to 1", why)
        self.assertIn("shared-tree", why)

    def test_isolation_never_hands_back_the_canonical_tree(self):
        """The one wrong answer: a caller that got no lane must reduce
        concurrency, never write where everyone else is."""
        repo = a_repo()
        path, _b, problem = W.acquire(repo, "U", root="/proc/nope")
        self.assertIsNone(path)
        self.assertTrue(problem)

    def test_a_non_repository_is_refused_with_a_reason(self):
        path, _b, problem = W.acquire(tempfile.mkdtemp(), "U")
        self.assertIsNone(path)
        self.assertIn("not a git repository", problem)

    def test_concurrency_never_exceeds_the_lanes_that_exist(self):
        lanes = W.Lanes(a_repo(), ["U1", "U2"])
        self.assertEqual(lanes.safe_concurrency(9), 2)


class ItNeverDeletesWorkNobodyLookedAt(unittest.TestCase):
    def test_release_refuses_a_dirty_lane(self):
        repo = a_repo()
        lanes = W.Lanes(repo, ["U1"])
        p = lanes.path_for("U1")
        with open(os.path.join(p, "unseen.txt"), "w", encoding="utf-8") as fh:
            fh.write("work nobody has looked at\n")
        ok, note = W.release(repo, p)
        self.assertFalse(ok)
        self.assertIn("uncommitted", note)
        self.assertTrue(os.path.isdir(p))

    def test_force_releases_it_and_that_is_the_only_way(self):
        repo = a_repo()
        lanes = W.Lanes(repo, ["U1"])
        p = lanes.path_for("U1")
        with open(os.path.join(p, "unseen.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.assertTrue(W.release(repo, p, force=True)[0])

    def test_a_clean_lane_releases_without_force(self):
        repo = a_repo()
        lanes = W.Lanes(repo, ["U1"])
        self.assertTrue(W.release(repo, lanes.path_for("U1"))[0])

    def test_release_all_names_what_it_kept(self):
        repo = a_repo()
        lanes = W.Lanes(repo, ["U1", "U2"])
        with open(os.path.join(lanes.path_for("U1"), "x.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("x\n")
        released, kept = lanes.release_all()
        self.assertEqual([k[0] for k in kept], ["U1"])
        self.assertEqual([r[0] for r in released], ["U2"])

    def test_an_unreadable_lane_is_left_in_place_rather_than_guessed_at(self):
        ok, note = W.release(a_repo(), "/no/such/lane")
        self.assertFalse(ok)
        self.assertIn(W.NODATA, note)


class ConcurrencyIsProvenByBarrierNotByTiming(unittest.TestCase):
    """Section 33 of the directive. A timing test can pass on a fast machine
    that ran everything serially. This one cannot: each worker waits for the
    other's file, so serial execution deadlocks and only true parallelism
    finishes."""

    def _barrier_pair(self, workers):
        d = tempfile.mkdtemp()
        done = []

        def make(name, other):
            def go():
                with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                    fh.write("ready\n")
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    if os.path.exists(os.path.join(d, other)):
                        done.append(name)
                        return True
                    time.sleep(0.01)
                return False
            return go

        threads = [threading.Thread(target=make(*pair)) for pair in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        return done

    def test_two_genuinely_parallel_workers_both_clear_the_barrier(self):
        done = self._barrier_pair([("A_READY", "B_READY"), ("B_READY", "A_READY")])
        self.assertEqual(sorted(done), ["A_READY", "B_READY"])

    def test_a_serial_run_cannot_clear_it(self):
        """Run them one after the other: the first waits for a file the second
        has not written yet, and times out. This is what makes the test above
        evidence rather than decoration."""
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "A_READY"), "w", encoding="utf-8") as fh:
            fh.write("ready\n")
        deadline = time.time() + 0.3
        cleared = False
        while time.time() < deadline:
            if os.path.exists(os.path.join(d, "B_READY")):
                cleared = True
                break
            time.sleep(0.01)
        self.assertFalse(cleared)


class StaleLaneRefusal(unittest.TestCase):
    """The other half of the 2026-09-02 lane-cleanup fix: a `lane/<unit>`
    branch left behind by an earlier, uncleaned run must never be silently
    reused by a fresh acquire(), or a fresh attempt inherits a dead run's
    commits (observed live: a second run of the same unit found the branch
    still there and its test unit inherited the first run's work)."""

    def test_a_bare_stale_branch_is_removed_and_logged(self):
        repo = a_repo()
        # A leftover lane branch from an earlier, uncleaned run: no worktree
        # is registered for it, only the ref survives.
        subprocess.run(["git", "branch", "lane/A"], cwd=repo, check=True,
                       capture_output=True)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            path, branch, problem = W.acquire(repo, "A")
        self.assertTrue(path, problem)
        self.assertEqual(branch, "lane/A")
        self.assertIn("stale lane lane/A", buf.getvalue())
        self.assertIn("not reused", buf.getvalue())

    def test_a_checked_out_stale_lane_is_cleared_via_its_worktree_first(self):
        """The harder case: the old branch is still checked out in a linked
        worktree, so `git branch -D` alone would refuse it. acquire() must
        clear the worktree before the branch, and the fresh lane must not
        carry the dead run's commit forward."""
        repo = a_repo()
        old_path, old_branch, problem = W.acquire(repo, "A")
        self.assertTrue(old_path, problem)
        with open(os.path.join(old_path, "old_work.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("a dead run's commit\n")
        subprocess.run(["git", "add", "-A"], cwd=old_path, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-qm", "dead run"], cwd=old_path,
                       check=True, capture_output=True)

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            new_path, new_branch, problem = W.acquire(repo, "A")
        self.assertTrue(new_path, problem)
        self.assertEqual(new_branch, "lane/A")
        self.assertNotEqual(new_path, old_path)
        self.assertIn("stale lane lane/A", buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(new_path, "old_work.txt")),
                         "the dead run's commit must not follow into the "
                         "fresh lane")

    def test_removal_failure_refuses_the_unit_with_NO_DATA(self):
        """When the stale lane cannot be cleared, acquire() must refuse the
        unit rather than risk handing back a lane that still carries the old
        branch's history under it."""
        repo = a_repo()
        subprocess.run(["git", "branch", "lane/A"], cwd=repo, check=True,
                       capture_output=True)

        real = subprocess.run

        def failing_runner(cmd, **kw):
            if "branch" in cmd and "-D" in cmd:
                class _F:
                    returncode, stdout, stderr = 1, "", "monkeypatched failure"
                return _F()
            return real(cmd, capture_output=True, text=True, cwd=repo, timeout=120)

        path, branch, problem = W.acquire(repo, "A", runner=failing_runner)
        self.assertIsNone(path)
        self.assertIn(W.NODATA, problem)
        self.assertIn("lane/A", problem)


class OneDefinitionOfLiveness(unittest.TestCase):
    """E86. orphan_report() decided liveness by time alone
    (expires_at > now), while claim_store.live() also treats a dead owning pid
    on this host as not live. The same claim therefore read abandoned in
    reconcile() and OWNED here, two lines apart in one run.log. Both must call
    the same rule."""

    def test_a_dead_pid_claim_reads_abandoned_in_both_places(self):
        repo = a_repo()
        store = os.path.join(tempfile.mkdtemp(), "claims.json")
        path, _branch, problem = W.acquire(repo, "U1")
        self.assertFalse(problem, problem)

        claim, problem = C.acquire(store, "U1", "brother-run-59377", ttl=3600)
        self.assertEqual(problem, "")
        self.assertIsNotNone(claim)

        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()  # reaped, so genuinely gone rather than a zombie
        with open(store, encoding="utf-8") as fh:
            data = json.load(fh)
        data["U1"]["pid"] = dead.pid
        data["U1"]["hostname"] = C._hostname()
        with open(store, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        now = time.time()
        # The lease itself still has time on it: only the dead pid may decide.
        self.assertGreater(float(data["U1"]["expires_at"]), now)

        reconciled, problem = C.reconcile(store)
        self.assertEqual(problem, "")
        self.assertEqual(reconciled[0]["status"], "abandoned")

        findings, problem = W.orphan_report(repo, store)
        self.assertEqual(problem, "")
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0]["classification"], W.ABANDONED,
                         "reconcile() called this abandoned; the lane report "
                         "must not call the same claim still leased")
        self.assertIn("pid %d" % dead.pid, findings[0]["detail"])
        self.assertTrue(os.path.isdir(path))  # reports, never deletes


if __name__ == "__main__":
    unittest.main()
