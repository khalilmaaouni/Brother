#!/usr/bin/env python3
"""writer_lock's own suite. Every case uses a temporary lock path, so it never touches the real one."""
import json
import os
import tempfile
import unittest
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writer_lock as W  # noqa: E402


class OnlyOneHolder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = os.path.join(self.dir, "w.lock")

    def test_the_first_caller_gets_it(self):
        self.assertTrue(W.acquire(self.p, pid=111))

    def test_the_second_caller_is_refused_and_does_not_block(self):
        W.acquire(self.p, pid=111)
        self.assertFalse(W.acquire(self.p, pid=222))

    def test_releasing_frees_it_for_the_next(self):
        W.acquire(self.p, pid=111)
        self.assertTrue(W.release(self.p, pid=111))
        self.assertTrue(W.acquire(self.p, pid=222))

    def test_one_holder_cannot_release_anothers_lock(self):
        """Releasing somebody else's lock is how two writers end up each believing they are alone."""
        W.acquire(self.p, pid=111)
        self.assertFalse(W.release(self.p, pid=222))
        self.assertFalse(W.acquire(self.p, pid=333))

    def test_the_holder_is_recorded(self):
        W.acquire(self.p, pid=111, what="LAND")
        row = W.read(self.p)
        self.assertEqual(row["pid"], 111)
        self.assertEqual(row["what"], "LAND")

    def test_an_absent_lock_reads_as_free(self):
        self.assertIsNone(W.read(self.p))

    def test_a_corrupt_lock_reads_as_HELD_not_free(self):
        """A file that exists means somebody meant to hold it; reading it as free is the dangerous direction."""
        open(self.p, "w").write("{not json")
        self.assertIsNotNone(W.read(self.p))


class ADeadHolderDoesNotStopTheNight(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = os.path.join(self.dir, "w.lock")

    def gone(self, pid, sig):
        raise ProcessLookupError()

    def running(self, pid, sig):
        return None

    def test_a_dead_holders_lock_is_broken(self):
        W.acquire(self.p, pid=111)
        broke, why = W.break_if_dead(self.p, probe=self.gone)
        self.assertTrue(broke)
        self.assertIsNone(W.read(self.p))

    def test_a_live_holders_lock_is_never_broken_however_old(self):
        """A slow landing is not a stuck one, and stealing the tree from it is worse than waiting."""
        W.acquire(self.p, pid=111, now=0.0)
        broke, why = W.break_if_dead(self.p, probe=self.running)
        self.assertFalse(broke)
        self.assertIsNotNone(W.read(self.p))

    def test_breaking_an_absent_lock_is_not_an_error(self):
        broke, why = W.break_if_dead(self.p, probe=self.gone)
        self.assertFalse(broke)
        self.assertIn("no lock", why)

    def test_an_unknown_pid_counts_as_alive(self):
        self.assertTrue(W.alive(None))
        self.assertTrue(W.alive("x"))
        self.assertTrue(W.alive(0))
        self.assertTrue(W.alive(True))

    def test_a_permission_error_counts_as_alive(self):
        def denied(pid, sig):
            raise PermissionError()
        self.assertTrue(W.alive(999, denied))


class TheContextManagerIsHonest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = os.path.join(self.dir, "w.lock")

    def test_it_yields_the_row_when_we_hold_it(self):
        with W.held(self.p, what="LAND") as lock:
            self.assertIsNotNone(lock)
            self.assertEqual(lock["what"], "LAND")

    def test_it_releases_on_the_way_out(self):
        with W.held(self.p):
            pass
        self.assertIsNone(W.read(self.p))

    def test_it_releases_even_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with W.held(self.p):
                raise ValueError("the landing blew up")
        self.assertIsNone(W.read(self.p))

    def test_it_yields_none_when_a_live_holder_has_it(self):
        W.acquire(self.p, pid=111)
        with W.held(self.p, probe=lambda pid, sig: None) as lock:
            self.assertIsNone(lock)
        self.assertIsNotNone(W.read(self.p))   # and it did not release the other holder's lock


if __name__ == "__main__":
    unittest.main(verbosity=1)
