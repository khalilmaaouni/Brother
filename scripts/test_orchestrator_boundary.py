#!/usr/bin/env python3
"""Tests for scripts/orchestrator_boundary.py (ORCH-18).

THE BAD STATE A GREEN RUN OF THIS FILE WOULD ALSO PASS if it were not
guarded against: guard_canonical() refusing EVERYTHING, including a
caller holding the integrator capability. Every test that asserts a
refusal (test_direct_merge_denied, test_direct_ref_update_denied,
test_push_denied, test_stale_epoch_refused_at_use, test_red_action_refused,
test_unknown_action_refused, test_single_use) would still pass under an
all-refusing implementation, because they only ever check that a
BoundaryRefused was raised. test_integrator_is_allowed is the one test
that would go RED under that bad implementation: it asserts, explicitly,
that guard_canonical() returns None (no exception) for an integrator's
merge, push and update-ref. A module that refuses every command passes
every other test here and fails only this one, which is exactly why it
exists and is not merely one more case among the rest.

Plain unittest, runnable directly: python3 scripts/test_orchestrator_boundary.py -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fable_authority
import orchestrator_authority
import orchestrator_boundary as boundary


class BoundaryTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="orch-boundary-test-")
        self.store_path = os.path.join(self.tmpdir, "authority.json")
        self.red_path = os.path.join(self.tmpdir, "red-queue.jsonl")
        self.run_id = "RUN-TEST"
        self.scope = "canonical"
        self.instance_a = "orch-a"
        self.instance_b = "orch-b"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _acquire(self, instance, ttl_seconds=1000.0, now=0.0):
        return orchestrator_authority.acquire(
            self.store_path, self.run_id, self.scope, "fable", instance,
            ttl_seconds, now=now)

    def _authorize(self, instance, epoch, action="CANONICAL-WRITE",
                  now=0.0, integrator_token=None):
        return boundary.authorize(
            action, store_path=self.store_path, run_id=self.run_id,
            scope=self.scope, instance=instance, epoch=epoch, now=now,
            integrator_token=integrator_token, red_queue_path=self.red_path)


class TestDirectCanonicalWritesDenied(BoundaryTestBase):
    """Tests 1 and 2: the exact bypass invariant 6 exists to close. A
    caller with no integrator capability cannot merge, update a ref
    directly, or push, even holding a perfectly live authorization."""

    def test_direct_merge_denied(self):
        lease = self._acquire(self.instance_a)
        auth = self._authorize(self.instance_a, lease.epoch)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.guard_canonical(
                ["merge", "--no-ff", "-m", "msg", "lane/x"],
                authorization=auth, now=0.0)
        self.assertEqual(ctx.exception.invariant,
                         "no direct agent merge to canonical")

    def test_direct_ref_update_denied(self):
        lease = self._acquire(self.instance_a)
        auth = self._authorize(self.instance_a, lease.epoch)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.guard_canonical(
                ["update-ref", "refs/heads/main", "deadbeef"],
                authorization=auth, now=0.0)
        self.assertEqual(ctx.exception.invariant,
                         "no direct agent merge to canonical")

    def test_push_denied(self):
        lease = self._acquire(self.instance_a)
        auth = self._authorize(self.instance_a, lease.epoch)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.guard_canonical(["push"], authorization=auth, now=0.0)
        self.assertEqual(ctx.exception.invariant,
                         "no direct agent merge to canonical")

    def test_merge_base_is_not_a_merge(self):
        """THE TRAP the worker contract warns about: a real git subcommand
        containing the substring 'merge' that does not mutate anything. A
        substring test on the word 'merge' would misclassify this as a
        write; matching argv[0] exactly does not."""
        lease = self._acquire(self.instance_a)
        auth = self._authorize(self.instance_a, lease.epoch)
        result = boundary.guard_canonical(
            ["merge-base", "main", "lane/x"], authorization=auth, now=0.0)
        self.assertIsNone(result)


class TestIntegratorIsAllowed(BoundaryTestBase):
    """Test 3, and the guard against the bad state named in this file's
    module docstring: the boundary must be a boundary, not a wall."""

    def test_integrator_is_allowed(self):
        token = boundary.canonical_write_token(self.instance_a)
        lease = self._acquire(self.instance_a)

        auth_merge = self._authorize(self.instance_a, lease.epoch,
                                     integrator_token=token)
        result = boundary.guard_canonical(
            ["merge", "--no-ff", "-m", "msg", "lane/x"],
            authorization=auth_merge, now=0.0)
        self.assertIsNone(result)

        auth_push = self._authorize(self.instance_a, lease.epoch,
                                    integrator_token=token)
        result = boundary.guard_canonical(["push"], authorization=auth_push,
                                          now=0.0)
        self.assertIsNone(result)

        auth_ref = self._authorize(self.instance_a, lease.epoch,
                                   integrator_token=token)
        result = boundary.guard_canonical(
            ["update-ref", "refs/heads/main", "deadbeef"],
            authorization=auth_ref, now=0.0)
        self.assertIsNone(result)

    def test_someone_elses_token_does_not_count(self):
        """A token issued to instance_b never makes instance_a's
        authorization an integrator's: is_integrator() checks the token
        was issued to the SAME instance the authorization was granted to."""
        others_token = boundary.canonical_write_token(self.instance_b)
        lease = self._acquire(self.instance_a)
        auth = self._authorize(self.instance_a, lease.epoch,
                               integrator_token=others_token)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.guard_canonical(["push"], authorization=auth, now=0.0)
        self.assertEqual(ctx.exception.invariant,
                         "no direct agent merge to canonical")


class TestStaleEpochRefusedAtUse(BoundaryTestBase):
    """Test 4: the authorization is refused at the MOMENT OF USE, once its
    epoch is no longer current, not merely re-validated at grant time."""

    def test_stale_epoch_refused_at_moment_of_use(self):
        lease = self._acquire(self.instance_a, ttl_seconds=5.0, now=0.0)
        auth = self._authorize(self.instance_a, lease.epoch, now=0.0)

        # Another instance takes over once the first lease has expired:
        # the epoch on disk is now lease.epoch + 1, held by instance_b.
        orchestrator_authority.takeover(
            self.store_path, self.run_id, self.scope, "fable",
            self.instance_b, ttl_seconds=1000.0, reason="test takeover",
            now=10.0)

        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.guard_canonical(["status"], authorization=auth,
                                     now=10.0)
        self.assertEqual(ctx.exception.invariant, "no stale authority")

    def test_authorize_itself_refuses_a_stale_epoch(self):
        """The same guard applies to granting a fresh authorization: an
        epoch nobody currently holds grants nothing."""
        lease = self._acquire(self.instance_a, ttl_seconds=5.0, now=0.0)
        orchestrator_authority.takeover(
            self.store_path, self.run_id, self.scope, "fable",
            self.instance_b, ttl_seconds=1000.0, reason="test takeover",
            now=10.0)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            self._authorize(self.instance_a, lease.epoch, now=10.0)
        self.assertEqual(ctx.exception.invariant, "no stale authority")


class TestRedActionRefusedAndQueued(BoundaryTestBase):
    """Test 5: a RED action is refused and queued, and that refusal is
    scoped to itself, never a halt on the run."""

    def test_red_action_refused_and_queued(self):
        lease = self._acquire(self.instance_a)
        red_action = {"name": "DISPATCH",
                      "text": "delete the stale remote branch"}
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            self._authorize(self.instance_a, lease.epoch, action=red_action,
                            now=0.0)
        self.assertEqual(ctx.exception.invariant, "no RED action automated")
        self.assertTrue(os.path.isfile(self.red_path),
                        "a RED refusal must queue a record for the founder")
        with open(self.red_path, encoding="utf-8") as fh:
            queued = fh.read()
        self.assertIn("delete the stale remote branch", queued)
        self.assertIn("AWAITING FOUNDER", queued)

    def test_red_refusal_does_not_block_an_unrelated_safe_action(self):
        lease = self._acquire(self.instance_a)
        red_action = {"name": "DISPATCH",
                      "text": "delete the stale remote branch"}
        with self.assertRaises(boundary.BoundaryRefused):
            self._authorize(self.instance_a, lease.epoch, action=red_action,
                            now=0.0)

        safe_action = {"name": "DISPATCH",
                      "text": "dispatch unit ORCH-19 to a worker"}
        auth = self._authorize(self.instance_a, lease.epoch,
                               action=safe_action, now=0.0)
        self.assertIsInstance(auth, boundary.Authorization)


class TestUnknownActionRefused(BoundaryTestBase):
    """Test 6: an unrecognised action name is refused, never defaulted to
    permitted."""

    def test_unknown_action_refused(self):
        lease = self._acquire(self.instance_a)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            self._authorize(self.instance_a, lease.epoch,
                            action="not-a-real-action", now=0.0)
        self.assertEqual(ctx.exception.invariant, "unknown action")

    def test_unknown_task_state_refused(self):
        lease = self._acquire(self.instance_a)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.authorize(
                "CANONICAL-WRITE", store_path=self.store_path,
                run_id=self.run_id, scope=self.scope, instance=self.instance_a,
                epoch=lease.epoch, task_state="NOT-A-REAL-STATE", now=0.0)
        self.assertEqual(ctx.exception.invariant, "unknown action")


class TestAuthorizationSingleUse(BoundaryTestBase):
    """Test 7: a granted authorization is spent by one use."""

    def test_single_use_is_enforced(self):
        lease = self._acquire(self.instance_a)
        auth = self._authorize(self.instance_a, lease.epoch)
        result = boundary.guard_canonical(["status"], authorization=auth,
                                          now=0.0)
        self.assertIsNone(result)
        with self.assertRaises(boundary.BoundaryRefused) as ctx:
            boundary.guard_canonical(["status"], authorization=auth,
                                     now=0.0)
        self.assertEqual(ctx.exception.invariant, "single use")


class TestUnreadableStore(BoundaryTestBase):
    """A corrupt authority store is never read as 'go ahead': it raises
    BoundaryUnreadable, matching orchestrator_authority's own refusal to
    treat an unreadable store as a free scope."""

    def test_corrupt_store_raises_unreadable(self):
        with open(self.store_path, "w", encoding="utf-8") as fh:
            fh.write("not json at all {{{")
        with self.assertRaises(boundary.BoundaryUnreadable):
            self._authorize(self.instance_a, 1, now=0.0)


if __name__ == "__main__":
    unittest.main()
