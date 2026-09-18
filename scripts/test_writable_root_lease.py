"""What writable_root_lease must keep true.

The failure it exists to prevent, named in its own module docstring: two
agents in two DIFFERENT directories of the same repository both believe
they hold an exclusive lease and still race each other at the shared git
state (refs, the stash) neither directory owns alone. So the acceptance
test here is not "one lease excludes a second holder" (claim_store already
proves that); it is "two different writable roots of one repository still
collide, because they share a repository even though they do not share a
directory."
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store as C  # noqa: E402
import writable_root_lease as L  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


class FakeClock(object):
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, n):
        self.t += n


def store():
    return os.path.join(tempfile.mkdtemp(prefix="leases-"), "claims.json")


def plain_dir():
    """A writable root with no git repository behind it at all."""
    return tempfile.mkdtemp(prefix="plain-")


def a_repo():
    d = tempfile.mkdtemp(prefix="repo-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return d


def add_worktree(repo, branch):
    path = tempfile.mkdtemp(prefix="wt-")
    os.rmdir(path)  # git worktree add wants to create this itself
    proc = subprocess.run(
        ["git", "-C", repo, "worktree", "add", "-q", "-b", branch, path],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return path


def a_dead_pid():
    """A real, verifiably dead pid: spawned and reaped, never a made-up
    number that might collide with something actually running."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _patch_pid(store_path, unit_id, pid, hostname=None):
    with open(store_path, encoding="utf-8") as fh:
        data = json.load(fh)
    data[unit_id]["pid"] = pid
    data[unit_id]["hostname"] = hostname or C._hostname()
    with open(store_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class EmptyOrNoHolder(unittest.TestCase):
    def test_a_plain_directory_with_no_prior_claim_is_leased_at_once(self):
        p, root = store(), plain_dir()
        claims, problem = L.acquire(p, root, "session-a")
        self.assertEqual(problem, "")
        self.assertEqual(len(claims), 1, claims)
        self.assertIn(L.ROOT_PREFIX + os.path.realpath(root), claims)

    def test_a_git_root_with_no_prior_claim_gets_both_targets(self):
        p, root = store(), a_repo()
        claims, problem = L.acquire(p, root, "session-a")
        self.assertEqual(problem, "")
        self.assertEqual(len(claims), 2, claims)
        kinds = {k.split(":", 1)[0] for k in claims}
        self.assertEqual(kinds, {"root", "common"})


class ExactlyOneHolder(unittest.TestCase):
    def test_the_single_holder_reads_back_as_in_flight(self):
        p, root = store(), plain_dir()
        L.acquire(p, root, "session-a")
        found, problem = L.reconcile(p)
        self.assertIsNotNone(found, problem)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["status"], "in-flight")


class ASecondConcurrentActorIsRefusedByName(unittest.TestCase):
    def test_a_second_owner_on_the_same_root_is_refused_and_named(self):
        p, root = store(), plain_dir()
        first, problem = L.acquire(p, root, "session-a")
        self.assertEqual(problem, "")
        second, problem = L.acquire(p, root, "session-b")
        self.assertEqual(second, {})
        self.assertIn("session-a", problem)

    def test_two_different_directories_of_one_repository_still_collide(self):
        """THE ACCEPTANCE TEST. session-a leases worktree A; session-b
        leases worktree B, a DIFFERENT directory of the SAME repository.
        Their root keys never collide, so if this module leased only the
        directory, session-b would sail through. It must not: both share
        one repository, so both must contend for the one common-repository
        target, and session-b must be refused."""
        repo = a_repo()
        wt_a = repo
        wt_b = add_worktree(repo, "other-branch")
        p = L.default_store_path(wt_a)
        self.assertIsNotNone(p)
        self.assertEqual(p, L.default_store_path(wt_b),
                         "both worktrees must resolve to the same store")

        first, problem = L.acquire(p, wt_a, "session-a")
        self.assertEqual(problem, "", problem)

        second, problem = L.acquire(p, wt_b, "session-b")
        self.assertEqual(second, {},
                         "a different directory of the same repository must "
                         "still be refused, at the shared common target")
        self.assertIn("session-a", problem)
        self.assertIn(L.COMMON_PREFIX, problem)

        # Targets are tried in sorted order (common before root), so the
        # contested common target is hit first and the root target for
        # wt_b is never even attempted: nothing to roll back, and nothing
        # left dangling in the store for a key that was never touched.
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        wt_b_key = L.ROOT_PREFIX + os.path.realpath(wt_b)
        self.assertNotIn(wt_b_key, data)


class AcquireIsAllOrNothing(unittest.TestCase):
    def test_a_target_acquired_this_call_is_released_when_a_later_one_fails(self):
        """Seed contention on the ROOT target only (not the common one), so
        acquire()'s sorted order takes the common target first (nobody
        holds it) and only then hits the contested root target. That is
        the one shape that actually exercises the rollback path: a target
        this very call took must not survive the call failing."""
        repo = a_repo()
        p = store()
        root_key = L.ROOT_PREFIX + os.path.realpath(repo)
        seeded, problem = C.acquire(p, root_key, "other-owner")
        self.assertEqual(problem, "", problem)

        claims, problem = L.acquire(p, repo, "session-a")
        self.assertEqual(claims, {})
        self.assertIn("other-owner", problem)

        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        common_key = L.COMMON_PREFIX + L.common_git_dir(repo)
        self.assertNotEqual(data[common_key]["state"], "claimed",
                            "the common target this call itself took must "
                            "be released again once the root target lost")
        self.assertEqual(data[root_key]["owner"], "other-owner",
                         "the seeded root claim must be untouched")


class AStaleLeaseFromADeadPid(unittest.TestCase):
    def test_a_dead_owners_lease_is_reclaimed_without_waiting_out_the_ttl(self):
        p, root = store(), plain_dir()
        clock = FakeClock()
        claims, problem = L.acquire(p, root, "crashed-session",
                                    ttl=3600, clock=clock)
        self.assertEqual(problem, "")
        key = next(iter(claims))
        _patch_pid(p, key, a_dead_pid())

        clock.advance(5)  # nowhere near the 3600s lease
        claims2, problem2 = L.acquire(p, root, "fresh-session", clock=clock)
        self.assertEqual(problem2, "", problem2)
        self.assertEqual(claims2[key]["owner"], "fresh-session")


class TheSameActorReacquiringAfterRestart(unittest.TestCase):
    def test_the_same_owner_may_retake_its_own_live_lease(self):
        """A session that restarts keeps its own identity (owner string).
        It must be able to reassert its lease without waiting for the TTL
        or being told a stranger holds it: this is not the second-actor
        case, it is the same actor coming back."""
        p, root = store(), plain_dir()
        clock = FakeClock()
        first, problem = L.acquire(p, root, "session-a", ttl=3600, clock=clock)
        self.assertEqual(problem, "")

        clock.advance(1)  # far from expiry: the old lease is still live
        second, problem = L.acquire(p, root, "session-a", ttl=3600, clock=clock)
        self.assertEqual(problem, "", problem)
        key = next(iter(second))
        self.assertEqual(second[key]["owner"], "session-a")
        self.assertGreater(second[key]["attempt"], first[key]["attempt"])


class ACorruptLeaseFileBlocks(unittest.TestCase):
    def test_unreadable_json_refuses_rather_than_reading_as_free(self):
        p, root = store(), plain_dir()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")

        claims, problem = L.acquire(p, root, "session-a")
        self.assertEqual(claims, {})
        self.assertNotEqual(problem, "")

        # Still refuses on a second attempt: a corrupt store is never
        # quietly treated as an empty, available one.
        claims2, problem2 = L.acquire(p, root, "session-b")
        self.assertEqual(claims2, {})
        self.assertNotEqual(problem2, "")


class TwoWorktreesContendAtTheCommonRepository(unittest.TestCase):
    """The other half of the acceptance test above, from the release/renew
    side: releasing from one worktree's root must free the SAME common
    target a sibling worktree is waiting on."""

    def test_releasing_from_one_worktree_frees_the_common_target_for_the_other(self):
        repo = a_repo()
        wt_b = add_worktree(repo, "other-branch")
        p = L.default_store_path(repo)

        L.acquire(p, repo, "session-a")
        blocked, problem = L.acquire(p, wt_b, "session-b")
        self.assertEqual(blocked, {})

        released, problems = L.release(p, repo, "session-a")
        self.assertEqual(problems, [])
        self.assertEqual(len(released), 2)  # root AND common, both freed

        claims, problem = L.acquire(p, wt_b, "session-b")
        self.assertEqual(problem, "", problem)
        self.assertEqual(len(claims), 2)


class CommonGitDirResolution(unittest.TestCase):
    def test_a_plain_directory_has_no_common_git_dir(self):
        self.assertIsNone(L.common_git_dir(plain_dir()))
        self.assertIsNone(L.default_store_path(plain_dir()))

    def test_a_repository_resolves_to_an_absolute_existing_directory(self):
        repo = a_repo()
        common = L.common_git_dir(repo)
        self.assertIsNotNone(common)
        self.assertTrue(os.path.isabs(common))
        self.assertTrue(os.path.isdir(common))

    def test_git_not_runnable_is_treated_as_no_repository(self):
        def broken_runner(cmd, **kw):
            raise OSError("git not found")
        self.assertIsNone(L.common_git_dir(plain_dir(), runner=broken_runner))


class RenewKeepsEveryTargetAlive(unittest.TestCase):
    def test_renew_pushes_out_every_target_a_root_implies(self):
        repo = a_repo()
        p = store()
        clock = FakeClock()
        claims, _ = L.acquire(p, repo, "session-a", ttl=10, clock=clock)
        before = {k: c["expires_at"] for k, c in claims.items()}

        clock.advance(5)
        renewed, problems = L.renew(p, repo, "session-a", ttl=10, clock=clock)
        self.assertEqual(problems, [])
        self.assertEqual(set(renewed), set(claims))

        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in claims:
            self.assertGreater(data[key]["expires_at"], before[key])


if __name__ == "__main__":
    unittest.main()
