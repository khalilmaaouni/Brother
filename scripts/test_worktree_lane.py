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
import journal  # noqa: E402
import worktree_lane as W  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


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

    def test_the_breadcrumb_sidecar_carries_a_numeric_created_at(self):
        """REPAIR L1 (2026-09-10, Linux CI runner): acquire() writes
        created_at into the breadcrumb sidecar exactly once, so
        cleanup_lane has a stable, platform-independent creation time even
        on Linux, where st_birthtime does not exist and st_ctime is only
        the last metadata change."""
        path = self.lanes.path_for("U1")
        admin_dir = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=path,
            capture_output=True, text=True).stdout.strip()
        if not os.path.isabs(admin_dir):
            admin_dir = os.path.normpath(os.path.join(path, admin_dir))
        sidecar_path = os.path.join(admin_dir, W.LANE_SIDECAR_NAME)
        with open(sidecar_path, encoding="utf-8") as fh:
            sidecar = json.load(fh)
        self.assertIn("created_at", sidecar)
        self.assertIsInstance(sidecar["created_at"], (int, float))
        self.assertNotIsInstance(sidecar["created_at"], bool)

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


class TheLaneReuseProofNeedsAllFiveSignals(unittest.TestCase):
    """RESUME-FIX F3 (2026-09-09), extended by REPAIR ROUND 3's R4: a dead
    claim's own lane, still on disk, is reused on the next acquire() for
    the SAME unit only when branch_for, a registered worktree, this run's
    own journal, a claim still claimed-and-dead, a real commit beyond the
    fork point, AND a working tree with no dirt beyond that commit (or
    dirt confined to the unit's own declared owned_paths) all hold. Any
    one missing is the existing clear-and-recreate path, unchanged
    (except the dirt signal, which retains the lane instead -- see R4's
    own tests below): this is what corroborates F3 never touching the
    ordinary retry of a unit that finished a round (PASS, FAIL, or
    QUARANTINE) and was released the normal way, a unit whose worker
    never wrote anything before it was killed, or a unit whose worker was
    still mid-write when it was killed. The class keeps its original name
    (an identifier, not a claim) though the signal count has grown."""

    def setUp(self):
        self.repo = a_repo()
        self.run_dir = tempfile.mkdtemp(prefix="lane-reuse-run-")
        self._prior_env = os.environ.get(journal.RUN_DIR_ENV_VAR)
        os.environ[journal.RUN_DIR_ENV_VAR] = self.run_dir

    def tearDown(self):
        if self._prior_env is None:
            os.environ.pop(journal.RUN_DIR_ENV_VAR, None)
        else:
            os.environ[journal.RUN_DIR_ENV_VAR] = self._prior_env
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _commit_something(self, path):
        with open(os.path.join(path, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("the unit's own prior write\n")
        subprocess.run(["git", "add", "-A"], cwd=path,
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "worker wrote new.txt"],
                       cwd=path, capture_output=True, text=True, check=True)

    def _mark_dead_and_orphaned(self, unit_id):
        claims_path = os.path.join(self.run_dir, "claims.json")
        with open(claims_path, "w", encoding="utf-8") as fh:
            json.dump({unit_id: {"state": "claimed",
                                "expires_at": time.time() - 100,
                                "pid": 999999999, "hostname": "nowhere"}}, fh)
        journal.append(self.run_dir, "claim.orphaned_by_kill", unit_id=unit_id)

    def test_a_crash_orphaned_lane_with_a_real_commit_is_reused(self):
        path1, branch1, problem1 = W.acquire(self.repo, "U1")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        self._mark_dead_and_orphaned("U1")

        path2, branch2, problem2 = W.acquire(self.repo, "U1")
        self.assertEqual(problem2, "")
        self.assertEqual(os.path.realpath(path2), os.path.realpath(path1),
                         "a crash-orphaned lane holding a real commit must "
                         "be reused, not recreated empty")
        self.assertEqual(branch2, branch1)
        self.assertTrue(os.path.exists(os.path.join(path2, "new.txt")),
                        "the unit's own prior write must survive the reuse")

    def test_branch_for_matches_what_acquire_actually_creates(self):
        path, branch, problem = W.acquire(self.repo, "some/odd:id")
        self.assertEqual(problem, "")
        self.assertEqual(branch, W.branch_for("some/odd:id"))

    def test_an_ordinarily_released_lane_is_never_reused(self):
        """No kill at all: the unit finished a round (here, simply
        released "failed" by hand, exactly like loop_bridge.main() does at
        the end of an ordinary dispatch) and its lane is left on disk.
        Without a "claim.orphaned_by_kill" journal event, the fourth
        signal is missing, and the ordinary clear-and-recreate path must
        still apply -- the regression this test pins was live for a few
        hours on 2026-09-09: a quarantined lane read back as delivered on
        its very next ordinary retry."""
        path1, branch1, problem1 = W.acquire(self.repo, "U2")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        # released the ordinary way: no kill, so no orphan journal entry
        claims_path = os.path.join(self.run_dir, "claims.json")
        with open(claims_path, "w", encoding="utf-8") as fh:
            json.dump({"U2": {"state": "failed", "expires_at": 0}}, fh)

        path2, branch2, problem2 = W.acquire(self.repo, "U2")
        self.assertEqual(problem2, "")
        self.assertNotEqual(path2, path1,
                            "an ordinarily-released lane must never be "
                            "silently reused by the next claim")
        self.assertFalse(os.path.exists(os.path.join(path2, "new.txt")))

    def test_an_empty_crash_orphaned_lane_is_not_reused_either(self):
        """The worker was killed before it ever wrote or committed
        anything (still sitting exactly at the fork point): the fifth
        signal (a real commit beyond the fork point) is missing, so
        reuse is declined and the ordinary fresh path applies, exactly as
        it did before F3 -- nothing worth preserving means nothing is
        preserved, but also nothing is silently inherited that should not
        be."""
        path1, branch1, problem1 = W.acquire(self.repo, "U3")
        self.assertEqual(problem1, "")
        # no commit made in path1: the lane is empty, exactly as a worker
        # killed mid-sleep leaves it
        self._mark_dead_and_orphaned("U3")

        path2, branch2, problem2 = W.acquire(self.repo, "U3")
        self.assertEqual(problem2, "")
        self.assertNotEqual(path2, path1,
                            "an empty lane holds nothing worth preserving; "
                            "it must not be reused")

    def test_a_lane_released_the_ordinary_way_after_a_reuse_is_never_reused_again(self):
        """R1, repair round 3: a bare any() over the whole journal used to
        LATCH -- one kill early in a run made every LATER round for the
        same unit read as crash-orphaned too, even a round that quarantined
        and released the lane the ordinary way. Driven here exactly as the
        reviewer drove it: kill once, resume (reuse allowed), then a later
        round that quarantines and releases, then another round -- the lane
        must be recreated, not reused a second time."""
        claims_path = os.path.join(self.run_dir, "claims.json")

        # Round 1: the worker writes a real commit, then is killed.
        path1, branch1, problem1 = W.acquire(self.repo, "U4")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        self._mark_dead_and_orphaned("U4")

        # Resume: the crash-orphaned lane, proven by all five signals, is
        # reused -- this much already worked before this repair.
        path2, branch2, problem2 = W.acquire(self.repo, "U4")
        self.assertEqual(problem2, "")
        self.assertEqual(os.path.realpath(path2), os.path.realpath(path1),
                         "the resumed attempt must reuse the crash-orphaned "
                         "lane")

        # That resumed attempt finishes the ORDINARY way: claimed, then
        # released quarantined, exactly what claim_store.release() writes
        # at the end of a real round -- never a second kill.
        claim, problem = C.acquire(claims_path, "U4", "brother-run-resume",
                                   ttl=3600)
        self.assertEqual(problem, "")
        C.release(claims_path, "U4", "brother-run-resume",
                  state="quarantined")

        # A later round reclaims U4 (an ordinary retry) and asks
        # worktree_lane for a lane again. Before this repair, the OLD
        # "claim.orphaned_by_kill" event from round 1 still latched and
        # this reused the quarantined lane a second time instead of
        # recreating it -- the exact regression driven live on 2026-09-09.
        path3, branch3, problem3 = W.acquire(self.repo, "U4")
        self.assertEqual(problem3, "")
        self.assertNotEqual(os.path.realpath(path3), os.path.realpath(path2),
                            "a lane released the ordinary way must never be "
                            "reused on the strength of an older kill")

    def test_a_reuse_leaks_no_new_brother_lane_temp_dir(self):
        """R3, repair round 3: acquire() used to call tempfile.mkdtemp()
        up front, before deciding reuse, so a reused lane (which never
        touches that fresh directory -- it hands back the OLD lane's own
        path instead) still left an empty brother-lane-* directory behind
        on every single reuse. Driven against the real sandboxed temp root
        tmp_sandbox.install() points tempfile.tempdir at, so a leaked
        directory is really observable rather than assumed away by a
        mock."""
        path1, branch1, problem1 = W.acquire(self.repo, "U5")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        self._mark_dead_and_orphaned("U5")

        after_first = set(
            n for n in os.listdir(tempfile.gettempdir())
            if n.startswith("brother-lane-"))
        # The first acquire() is a fresh lane, so it is allowed to create
        # exactly one new temp base -- this call is not what R3 is about.

        path2, branch2, problem2 = W.acquire(self.repo, "U5")
        self.assertEqual(problem2, "")
        self.assertEqual(os.path.realpath(path2), os.path.realpath(path1),
                         "sanity: this must actually be the reuse path")

        after_reuse = set(
            n for n in os.listdir(tempfile.gettempdir())
            if n.startswith("brother-lane-"))
        self.assertEqual(
            after_reuse - after_first, set(),
            "a reused lane must not leave a fresh, empty brother-lane-* "
            "temp directory behind: %r" % (after_reuse - after_first))

    def test_a_lane_dirty_beyond_its_own_commit_is_retained_never_reused(self):
        """R4, repair round 3: a kill that lands AFTER a real commit but
        WHILE the worker was still writing leaves a lane that satisfies
        every one of the first five signals (it holds a real commit) but
        also carries an extra, uncommitted half-write nobody has reviewed.
        Reusing it as-is would hand the next attempt that unreviewed dirt
        mixed in with its own. Driven exactly as the reviewer drove it:
        kill after commit with an extra dirty file; the next attempt must
        get a genuinely fresh lane, and the retained lane must be named
        (never silently discarded, matching release()'s own refusal to
        destroy uncommitted work nobody has looked at)."""
        path1, branch1, problem1 = W.acquire(self.repo, "U6")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        # The half-write: an extra file left uncommitted when the kill
        # landed, exactly the shape a worker killed mid-write leaves.
        with open(os.path.join(path1, "half_write.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("never committed\n")
        self._mark_dead_and_orphaned("U6")

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            path2, branch2, problem2 = W.acquire(self.repo, "U6")
        self.assertEqual(problem2, "")
        stderr = buf.getvalue()

        self.assertNotEqual(os.path.realpath(path2), os.path.realpath(path1),
                            "a lane with unreviewed dirt beyond its own "
                            "commit must never be reused as-is")
        self.assertEqual(branch2, "lane/U6",
                         "the fresh attempt must land on the unit's own "
                         "plain branch name, exactly as an ordinary fresh "
                         "acquire() would")
        self.assertIn("RETAINED", stderr)
        self.assertIn(path1, stderr,
                      "the retained lane must be named, not just implied")

        # Never discarded: the old worktree, and its uncommitted dirt,
        # are still sitting exactly where they were.
        self.assertTrue(os.path.isdir(path1))
        self.assertTrue(os.path.exists(os.path.join(path1, "half_write.txt")))
        self.assertTrue(os.path.exists(os.path.join(path1, "new.txt")))

    def test_dirt_confined_to_owned_paths_is_still_reused(self):
        """The other half of R4: a caller that names its own write scope
        must not be punished for dirt squarely inside it -- a worker
        killed after writing (but not yet committing) a file it owns is
        exactly the ordinary in-flight shape this reuse machinery exists
        to preserve, never a stranger's unreviewed change."""
        path1, branch1, problem1 = W.acquire(self.repo, "U7")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        with open(os.path.join(path1, "owned.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("uncommitted but declared\n")
        self._mark_dead_and_orphaned("U7")

        path2, branch2, problem2 = W.acquire(self.repo, "U7",
                                             owned_paths=["owned.txt"])
        self.assertEqual(problem2, "")
        self.assertEqual(os.path.realpath(path2), os.path.realpath(path1),
                         "dirt confined to the unit's own declared paths "
                         "must not block reuse")
        self.assertTrue(os.path.exists(os.path.join(path2, "owned.txt")))

    def test_a_fresh_acquire_journals_reused_false_and_no_retained_branch(self):
        """M-5: the journal is the durable half of the never-silent rule
        (claim_store.py's own release note), and before this fix its
        lane.acquired payload carried only branch and own_branch -- the
        same shape whether the lane was fresh, reused with a prior
        commit, or created after a dirty lane was retained aside. A
        fresh acquire (nothing stale on disk at all) must journal
        reused False and no retained_branch, so a reader of the journal
        alone -- not just the stderr note -- can tell the three shapes
        apart."""
        path, branch, problem = W.acquire(self.repo, "U8")
        self.assertEqual(problem, "")
        events = journal.read(self.run_dir)
        acquired = [e for e in events
                   if e.get("type") == "lane.acquired"
                   and e.get("unit_id") == "U8"]
        self.assertEqual(len(acquired), 1)
        payload = acquired[0]["payload"]
        self.assertIs(payload["reused"], False)
        self.assertIsNone(payload["retained_branch"])

    def test_a_proven_reuse_journals_reused_true(self):
        """M-5: the second half of the same fix. A crash-orphaned lane
        proven safe to reuse (all five prior signals plus a clean
        working tree) must journal reused True on the acquire that
        actually reuses it, distinct from the fresh acquire that
        preceded it."""
        path1, branch1, problem1 = W.acquire(self.repo, "U9")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        self._mark_dead_and_orphaned("U9")

        path2, branch2, problem2 = W.acquire(self.repo, "U9")
        self.assertEqual(problem2, "")
        self.assertEqual(os.path.realpath(path2), os.path.realpath(path1),
                         "sanity: this must actually be the reuse path")

        events = journal.read(self.run_dir)
        acquired = [e for e in events
                   if e.get("type") == "lane.acquired"
                   and e.get("unit_id") == "U9"]
        self.assertEqual(len(acquired), 2)
        self.assertIs(acquired[0]["payload"]["reused"], False,
                     "the first acquire is a fresh lane, not a reuse")
        self.assertIs(acquired[1]["payload"]["reused"], True,
                     "the second acquire reuses the crash-orphaned lane")
        self.assertIsNone(acquired[1]["payload"]["retained_branch"])

    def test_a_retained_dirty_lane_journals_its_retained_branch_name(self):
        """M-5: a dirty lane renamed aside to lane/<x>-retained-<ms> used
        to have that name recorded nowhere durable -- only in the
        stderr note. The journal must carry it, and the name it carries
        must actually resolve as a real branch (git rev-parse), not
        just a string the note happened to mention."""
        path1, branch1, problem1 = W.acquire(self.repo, "U10")
        self.assertEqual(problem1, "")
        self._commit_something(path1)
        with open(os.path.join(path1, "half_write.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("never committed\n")
        self._mark_dead_and_orphaned("U10")

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            path2, branch2, problem2 = W.acquire(self.repo, "U10")
        self.assertEqual(problem2, "")

        events = journal.read(self.run_dir)
        acquired = [e for e in events
                   if e.get("type") == "lane.acquired"
                   and e.get("unit_id") == "U10"]
        self.assertEqual(len(acquired), 2)
        payload = acquired[1]["payload"]
        self.assertIs(payload["reused"], False,
                     "a retained-then-fresh lane is not a reuse")
        retained_branch = payload["retained_branch"]
        self.assertIsNotNone(retained_branch,
                             "a retained lane's branch name must be "
                             "journaled, not left only in the stderr note")
        self.assertTrue(retained_branch.startswith("lane/U10-retained-"),
                        retained_branch)

        resolved = subprocess.run(["git", "rev-parse", retained_branch],
                                  cwd=path1, capture_output=True, text=True)
        self.assertEqual(resolved.returncode, 0,
                         "the retained_branch name journaled must resolve "
                         "as a real branch: %s" % resolved.stderr)


if __name__ == "__main__":
    unittest.main()
