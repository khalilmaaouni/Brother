"""CUT2.e, second half: a stop is NAMED, a retry is bounded, and cleanup reclaims only what it owns.

A capacity or deadline stop is classified apart from an assertion failure (perturb_pool.classify_stop, reusing
resource_gate.classify's timeout rule), a slow suite that is still working apart from a stuck one, and a CAPACITY or
SLOW baseline gets exactly one more run at double the wall. Every one of them stays NO-DATA: no check is skipped and
none is narrowed. The admission half is test_cut_resource_admission.py. Fixtures live under the scratch root.
Run: python3 scripts/test_cut_stop_classes.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import perturb_pool as PP  # noqa: E402

SCARCE = {"disk_free_gib": 50.0, "cores_available": 6, "load1": 20.0, "mem_free_gib": 8.0, "errors": {}}
CLEAN = {"disk_free_gib": 50.0, "cores_available": 6, "load1": 1.0, "mem_free_gib": 8.0, "errors": {}}
UNREADABLE = {"disk_free_gib": None, "cores_available": None, "load1": None, "mem_free_gib": None, "errors": {}}
DEADLINE = "scripts/test_x.py did not finish within 495s"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="cut-stop-", dir=export_public.scratch_root()))
        self.addCleanup(shutil.rmtree, self.dir, True)


class TheStopIsNamed(unittest.TestCase):
    def kind(self, rc, tail, cpu, reading):
        return PP.classify_stop(rc, tail, 495.0, cpu, reading=reading)[0]

    def test_a_deadline_on_a_scarce_machine_is_capacity(self):
        self.assertEqual(self.kind(None, DEADLINE, 0.0, SCARCE), PP.CAPACITY)

    def test_a_failed_assertion_on_a_scarce_machine_is_still_an_assertion(self):
        self.assertEqual(self.kind(1, "FAILED (failures=1)", 0.0, SCARCE), PP.ASSERTION)

    def test_a_failed_assertion_on_a_clean_machine_is_an_assertion(self):
        self.assertEqual(self.kind(2, "FAILED", 400.0, CLEAN), PP.ASSERTION)

    def test_a_deadline_with_cpu_still_burning_is_slow(self):
        self.assertEqual(self.kind(None, DEADLINE, 400.0, CLEAN), PP.SLOW)

    def test_a_deadline_with_no_cpu_is_stuck(self):
        self.assertEqual(self.kind(None, DEADLINE, 0.5, CLEAN), PP.STUCK)

    def test_the_share_of_cpu_that_counts_as_progress_is_the_stated_one(self):
        edge = PP.PROGRESS_CPU_SHARE * 495.0
        self.assertEqual(self.kind(None, DEADLINE, edge, CLEAN), PP.SLOW)
        self.assertEqual(self.kind(None, DEADLINE, edge - 0.5, CLEAN), PP.STUCK)

    def test_a_suite_that_never_ran_is_unreachable_not_a_deadline(self):
        self.assertEqual(self.kind(None, "could not run scripts/test_x.py: boom", 0.0, CLEAN), PP.UNREACHABLE)

    def test_a_deadline_whose_group_could_not_be_killed_is_unreachable(self):
        self.assertEqual(self.kind(None, DEADLINE + " and its process group could not be killed: x", 400.0, CLEAN),
                         PP.UNREACHABLE)

    def test_exit_zero_is_green_and_nothing_else_is(self):
        self.assertEqual(self.kind(0, "OK", 1.0, CLEAN), PP.GREEN)
        for rc in (True, "0", None, 0.0):
            self.assertNotEqual(self.kind(rc, "OK", 1.0, CLEAN), PP.GREEN, rc)

    def test_an_unreadable_machine_reading_does_not_read_as_a_pass(self):
        self.assertEqual(self.kind(None, DEADLINE, 0.0, UNREADABLE), PP.CAPACITY)   # deferred, never a pass


class TheBaselineRetry(unittest.TestCase):
    def runner(self, answers):
        walls, queue = [], list(answers)

        class P(object):
            @staticmethod
            def run_suite(suite, root=None, timeout=None):
                walls.append(timeout)
                return queue.pop(0)
        return P, walls

    def classify(self, reading, cpu):
        return lambda rc, tail, wall, cpu_s: PP.classify_stop(rc, tail, wall, cpu, reading=reading)

    def test_a_capacity_stop_is_retried_once_at_double_the_wall(self):
        P, walls = self.runner([(None, DEADLINE), (0, "OK")])
        rc, _tail, kind, _why, spent = PP.run_baseline(P, "s", "c", 100.0, classify=self.classify(SCARCE, 0.0))
        self.assertEqual((rc, kind, walls, spent), (0, PP.GREEN, [100.0, 200.0], 200.0))

    def test_a_slow_stop_is_retried_once_at_double_the_wall(self):
        P, walls = self.runner([(None, DEADLINE), (0, "OK")])
        self.assertEqual(PP.run_baseline(P, "s", "c", 100.0, classify=self.classify(CLEAN, 90.0))[2], PP.GREEN)
        self.assertEqual(walls, [100.0, 200.0])

    def test_the_retry_is_bounded_at_one(self):
        P, walls = self.runner([(None, DEADLINE)] * 5)
        kind = PP.run_baseline(P, "s", "c", 100.0, classify=self.classify(SCARCE, 0.0))[2]
        self.assertEqual((kind, walls), (PP.CAPACITY, [100.0, 200.0]))

    def test_a_stuck_suite_is_not_retried(self):
        P, walls = self.runner([(None, DEADLINE)] * 5)
        self.assertEqual(PP.run_baseline(P, "s", "c", 100.0, classify=self.classify(CLEAN, 0.0))[2], PP.STUCK)
        self.assertEqual(walls, [100.0])

    def test_an_assertion_failure_is_not_retried(self):
        P, walls = self.runner([(1, "FAILED")] * 5)
        self.assertEqual(PP.run_baseline(P, "s", "c", 100.0, classify=self.classify(SCARCE, 0.0))[2], PP.ASSERTION)
        self.assertEqual(walls, [100.0])


class TheWorkerWordsEachStop(Fixture):
    """run_worker end to end with a stub release_note_perturb in a fixture copy."""

    STUB = ("def run_suite(rel, root=None, timeout=None):\n    return %s\n"
            "def covers(suite, file_rel, root=None, baseline=None):\n    return True, 'red'\n")

    def worker(self, run_suite_result, kind):
        copy = os.path.join(self.dir, "copy")
        os.makedirs(os.path.join(copy, "scripts"), exist_ok=True)
        with open(os.path.join(copy, "scripts", "release_note_perturb.py"), "w") as fh:
            fh.write(self.STUB % run_suite_result)
        out = os.path.join(self.dir, "out.json")
        sys.modules.pop("release_note_perturb", None)
        self.addCleanup(sys.modules.pop, "release_note_perturb", None)
        self.addCleanup(setattr, sys, "path", list(sys.path))
        with mock.patch.object(PP, "classify_stop", lambda *a, **k: (kind, "why")), mock.patch.object(PP, "RETRYABLE", ()):
            PP.run_worker(copy, [("scripts/a.py", "scripts/test_a.py")], 100.0, out)
        with open(out) as fh:
            return json.load(fh)[0]

    def test_an_assertion_keeps_the_old_words(self):
        row = self.worker("(1, 'FAILED')", PP.ASSERTION)
        self.assertEqual((row[2], row[3]), (None, "baseline in the copy is not green (1): FAILED"))

    def test_a_deadline_is_named_with_its_class_and_stays_no_data(self):
        for kind in (PP.CAPACITY, PP.SLOW, PP.STUCK):
            row = self.worker("(None, 'did not finish within 100s')", kind)
            self.assertIsNone(row[2], kind)
            self.assertTrue(row[3].startswith("%s (%s)" % (PP.DEADLINE_PREFIX, kind)), row[3])
            self.assertTrue(row[3].startswith(PP.INFRA_PREFIXES), "still infrastructure: the table is refused")

    def test_a_green_baseline_goes_on_to_the_row(self):
        row = self.worker("(0, 'OK')", PP.GREEN)
        self.assertEqual((row[2], row[3]), (True, "red"))


class TheScratchIsReclaimedOnlyIfOwned(Fixture):
    def setUp(self):
        super().setUp()
        self.base = os.path.join(self.dir, "base")
        os.makedirs(self.base)

    def make(self, name):
        path = os.path.join(self.base, name)
        os.makedirs(path)
        open(os.path.join(path, "f"), "w").close()
        return path

    def test_an_owned_completed_folder_is_removed(self):
        mine = self.make("scratch0")
        PP.reclaim_scratch(mine, self.base, {mine})
        self.assertFalse(os.path.exists(mine))

    def test_a_folder_this_run_did_not_make_is_left_alone(self):
        theirs = self.make("scratch9")
        with self.assertRaises(PP.CopyFailed):
            PP.reclaim_scratch(theirs, self.base, {os.path.join(self.base, "scratch0")})
        self.assertTrue(os.path.exists(theirs))

    def test_a_link_is_never_followed(self):
        target = self.make("precious")
        link = os.path.join(self.base, "scratch1")
        os.symlink(target, link)
        with self.assertRaises(PP.CopyFailed):
            PP.reclaim_scratch(link, self.base, {link})
        self.assertTrue(os.path.exists(os.path.join(target, "f")))

    def test_an_owned_name_outside_the_base_is_left_alone(self):
        outside = os.path.join(self.dir, "elsewhere")
        os.makedirs(outside)
        with self.assertRaises(PP.CopyFailed):
            PP.reclaim_scratch(outside, self.base, {outside})
        self.assertTrue(os.path.exists(outside))

    def test_a_path_that_is_not_there_is_refused(self):
        gone = os.path.join(self.base, "scratch5")
        with self.assertRaises(PP.CopyFailed):
            PP.reclaim_scratch(gone, self.base, {gone})


if __name__ == "__main__":
    unittest.main()
