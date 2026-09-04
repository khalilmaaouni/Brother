"""What the repair loop must keep true.

Two of these matter more than the rest. The cap must actually stop, because an
unbounded repair loop against a wrong diagnosis applies a bad edit forever. And
NO-DATA must never be repaired, because repairing on NO-DATA sets a worker to
rewrite code that was never shown to be broken, using a diagnosis derived from a
check that produced no evidence at all.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_repair as R  # noqa: E402
import bm_verify as V  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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


class Worker(object):
    """Records every brief it is handed, which is how the retry-must-differ
    assertions are made at all."""

    def __init__(self, status="returned"):
        self.briefs = []
        self.status = status

    def run(self, brief):
        self.briefs.append(brief)
        return {"worker_claim": "attempt %d" % len(self.briefs),
                "artifacts": [], "cost": {"tokens": 0, "minutes": 0},
                "status": self.status}


def healing_verifier(pass_on_call):
    state = {"n": 0}

    def _v(unit, cwd=None):
        state["n"] += 1
        if state["n"] >= pass_on_call:
            return {"verdict": V.PASS, "reason": "ok", "exit_code": 0}
        return {"verdict": V.FAIL, "reason": "still red", "exit_code": 1}
    return _v


NEVER = lambda unit, cwd=None: {"verdict": V.FAIL, "reason": "still red",  # noqa: E731
                                "exit_code": 1}
NO_RECALL = lambda q, cwd=None: ("", "")  # noqa: E731
RED = {"verdict": V.FAIL, "reason": "the done_check ran and exited 1",
       "exit_code": 1, "stderr": "AssertionError: 3 != 4"}
UNIT = {"objective": "make the thing work", "done_check": "x"}


class NoDataIsNeverRepaired(unittest.TestCase):
    """The single most important refusal in the module."""

    def test_a_NO_DATA_verdict_spends_no_attempt_at_all(self):
        w = Worker()
        got = R.repair(UNIT, {"verdict": V.NO_DATA, "reason": "no done_check"},
                       w, verifier=NEVER, recall=NO_RECALL)
        self.assertEqual(got["outcome"], R.NOT_REPAIRABLE)
        self.assertEqual(got["attempts"], [])
        self.assertEqual(w.briefs, [], "a worker was dispatched against a unit "
                                       "nothing is known about")

    def test_the_refusal_explains_that_the_check_must_be_fixed_first(self):
        got = R.repair(UNIT, {"verdict": V.NO_DATA, "reason": "exit 127"},
                       Worker(), verifier=NEVER, recall=NO_RECALL)
        self.assertIn("never ran", got["reason"])
        self.assertIn("exit 127", got["reason"])


class TheCapIsReal(unittest.TestCase):
    def test_a_hopeless_unit_stops_after_exactly_its_budget(self):
        w = Worker()
        got = R.repair(UNIT, RED, w, verifier=NEVER, recall=NO_RECALL,
                       max_attempts=3)
        self.assertEqual(got["outcome"], R.EXHAUSTED)
        self.assertEqual(len(got["attempts"]), 3)
        self.assertEqual(len(w.briefs), 3)

    def test_a_cap_of_one_dispatches_once(self):
        w = Worker()
        R.repair(UNIT, RED, w, verifier=NEVER, recall=NO_RECALL, max_attempts=1)
        self.assertEqual(len(w.briefs), 1)

    def test_a_cap_below_one_dispatches_nothing(self):
        w = Worker()
        got = R.repair(UNIT, RED, w, verifier=NEVER, recall=NO_RECALL,
                       max_attempts=0)
        self.assertEqual(got["outcome"], R.EXHAUSTED)
        self.assertEqual(w.briefs, [])

    def test_the_exhausted_reason_says_the_cap_stopped_it(self):
        got = R.repair(UNIT, RED, Worker(), verifier=NEVER, recall=NO_RECALL,
                       max_attempts=2)
        self.assertIn("cap stopped", got["reason"])


class TheRetryIsADifferentAttempt(unittest.TestCase):
    """A retry handed the same brief is not a repair, it is the same dispatch
    twice. bm_controller carries prior_failure_note for exactly this."""

    def test_every_brief_carries_what_the_previous_attempt_did(self):
        w = Worker()
        R.repair(UNIT, RED, w, verifier=NEVER, recall=NO_RECALL, max_attempts=2)
        for brief in w.briefs:
            self.assertTrue(brief["prior_failure_note"].strip())
            self.assertIn("must differ", brief["prior_failure_note"])

    def test_the_note_carries_the_concrete_failure_not_just_that_it_failed(self):
        w = Worker()
        R.repair(UNIT, RED, w, verifier=NEVER, recall=NO_RECALL, max_attempts=1)
        note = w.briefs[0]["prior_failure_note"]
        self.assertIn("exited 1", note)
        self.assertIn("AssertionError", note)

    def test_the_attempt_number_advances(self):
        w = Worker()
        R.repair(UNIT, RED, w, verifier=NEVER, recall=NO_RECALL, max_attempts=3)
        self.assertEqual([b["attempt"] for b in w.briefs], [2, 3, 4])

    def test_the_original_unit_is_not_mutated(self):
        """The brief is a copy. A repair that edits the unit in place would
        leave prior_failure_note on it forever."""
        unit = dict(UNIT)
        R.repair(unit, RED, Worker(), verifier=NEVER, recall=NO_RECALL,
                 max_attempts=1)
        self.assertEqual(unit, UNIT)


class MemoryIsConsultedBeforeTheRetry(unittest.TestCase):
    def test_recall_runs_before_the_worker_is_dispatched(self):
        order = []

        class _W(object):
            def run(self, brief):
                order.append("dispatch")
                return {"worker_claim": "", "artifacts": [], "status": "returned",
                        "cost": {"tokens": 0, "minutes": 0}}

        def _recall(q, cwd=None):
            order.append("recall")
            return "a lesson", ""

        R.repair(UNIT, RED, _W(), verifier=NEVER, recall=_recall, max_attempts=1)
        self.assertEqual(order, ["recall", "dispatch"],
                         "a lesson recalled after the work changed nothing")

    def test_the_recalled_lesson_reaches_the_brief(self):
        w = Worker()
        R.repair(UNIT, RED, w, verifier=NEVER, max_attempts=1,
                 recall=lambda q, cwd=None: ("do not use a bare except", ""))
        self.assertIn("bare except", w.briefs[0]["recalled_lesson"])

    def test_a_failing_recall_does_not_stop_the_repair(self):
        """Knowing less is a reason to be careful, not a reason to abandon a
        red unit."""
        w = Worker()
        got = R.repair(UNIT, RED, w, verifier=healing_verifier(1),
                       recall=lambda q, cwd=None: ("", "vault unavailable"),
                       max_attempts=2)
        self.assertEqual(got["outcome"], R.REPAIRED)
        self.assertEqual(got["attempts"][0]["recall_note"], "vault unavailable")
        self.assertFalse(got["attempts"][0]["lesson_recalled"])


class TheHappyPaths(unittest.TestCase):
    def test_a_unit_that_heals_on_the_first_attempt_reports_REPAIRED(self):
        got = R.repair(UNIT, RED, Worker(), verifier=healing_verifier(1),
                       recall=NO_RECALL)
        self.assertEqual(got["outcome"], R.REPAIRED)
        self.assertEqual(len(got["attempts"]), 1)

    def test_it_stops_dispatching_the_moment_it_goes_green(self):
        w = Worker()
        R.repair(UNIT, RED, w, verifier=healing_verifier(2), recall=NO_RECALL,
                 max_attempts=5)
        self.assertEqual(len(w.briefs), 2, "it kept working after it passed")

    def test_an_already_passing_verdict_does_nothing(self):
        w = Worker()
        got = R.repair(UNIT, {"verdict": V.PASS, "reason": "ok"}, w,
                       verifier=NEVER, recall=NO_RECALL)
        self.assertEqual(got["outcome"], R.NOTHING_TO_DO)
        self.assertEqual(w.briefs, [])

    def test_the_four_outcomes_are_four_distinct_words(self):
        self.assertEqual(len({R.REPAIRED, R.EXHAUSTED, R.NOT_REPAIRABLE,
                              R.NOTHING_TO_DO}), 4)


class ItNeverRaisesOnAWorkerProblem(unittest.TestCase):
    def test_an_unavailable_worker_becomes_a_recorded_attempt(self):
        """A repair loop that throws leaves the unit in no state at all."""
        got = R.repair(UNIT, RED, Worker(status="unavailable"), verifier=NEVER,
                       recall=NO_RECALL, max_attempts=1)
        self.assertEqual(got["outcome"], R.EXHAUSTED)
        self.assertEqual(got["attempts"][0]["worker_status"], "unavailable")


class ARetryNeverEscapesItsLane(unittest.TestCase):
    """Found live 2026-08-30: retries ran the worker with no cwd and a repair
    committed an unrelated checkout's files. Backwards proof: against the
    pre-fix code (worker.run(brief) with no cwd), the sentinel below lands in
    the PROCESS working directory and the first assertion fails."""

    def test_the_sentinel_lands_only_in_the_lane(self):
        import tempfile, os
        lane = tempfile.mkdtemp(prefix="repair-lane-")
        launch_dir = os.getcwd()

        class LaneWorker(object):
            def run(self, brief, cwd=None):
                with open(os.path.join(cwd, "sentinel.txt"), "w") as fh:
                    fh.write("here")
                return {"status": "returned"}

        calls = {"n": 0}
        def verifier(unit, cwd=None):
            calls["n"] += 1
            return {"verdict": "PASS", "reason": "sentinel accepted"}

        got = R.repair(UNIT, RED, LaneWorker(), verifier=verifier,
                       recall=NO_RECALL, cwd=lane, max_attempts=1)
        self.assertEqual(got["outcome"], R.REPAIRED)
        self.assertTrue(os.path.exists(os.path.join(lane, "sentinel.txt")))
        self.assertFalse(os.path.exists(os.path.join(launch_dir, "sentinel.txt")))
        self.assertEqual(calls["n"], 1, "repaired output must be re-verified")

    def test_a_worker_that_cannot_take_a_lane_is_refused_not_run_shared(self):
        class NoLaneWorker(object):
            def run(self, brief):
                raise AssertionError("must never run: a shared-tree retry is the defect")

        got = R.repair(UNIT, RED, NoLaneWorker(), verifier=NEVER,
                       recall=NO_RECALL, cwd="/tmp/some-lane", max_attempts=3)
        self.assertEqual(got["outcome"], R.NOT_REPAIRABLE)
        self.assertIn("Refusing to retry in a shared tree", got["reason"])
        self.assertEqual(got["attempts"][0]["worker_status"], "refused-no-lane")


class TheSelftestIsRunnableByHand(unittest.TestCase):
    def test_selftest_exits_zero(self):
        self.assertEqual(R.main(["--selftest"]), 0)

    def test_a_bare_invocation_refuses(self):
        self.assertEqual(R.main([]), 2)


if __name__ == "__main__":
    unittest.main()
