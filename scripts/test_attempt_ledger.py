"""What the breaker must keep true.

The first class here is a REGRESSION TEST ON A REAL WEEK, not a hypothetical. It
replays the attempt sequence recorded in two vault failure notes and asserts the
ledger stops it at three. If that test ever goes green by accident, the tool has
stopped being a control and become a comment.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attempt_ledger as A  # noqa: E402

P = "the room cue is invisible"
CLASS = "light on the painting"


def rows(n_failed, klass=CLASS, problem=P):
    return [{"problem": problem, "class": klass, "outcome": "failed"}
            for _ in range(n_failed)]


class TheRealWeekIsStopped(unittest.TestCase):
    """A visual cue took a week: three builds tuning something never drawn, then
    six attempts at one class, every one measured green, scored 0 of 5."""

    def test_the_third_attempt_at_the_same_class_is_refused(self):
        self.assertEqual(A.check(rows(2), P, CLASS)[0], A.REFUSE)

    def test_the_first_two_are_allowed(self):
        self.assertEqual(A.check(rows(0), P, CLASS)[0], A.ALLOW)
        self.assertEqual(A.check(rows(1), P, CLASS)[0], A.ALLOW)

    def test_the_second_is_told_to_make_it_decisive(self):
        """Because the useful intervention is one attempt earlier than the
        refusal: the last shot should be an experiment, not an adjustment."""
        self.assertIn("decisive", A.check(rows(1), P, CLASS)[1])

    def test_the_refusal_offers_BOTH_moves_and_neither_is_another_try(self):
        why = A.check(rows(2), P, CLASS)[1]
        self.assertIn("CHANGE THE CLASS", why)
        self.assertIn("GO AND FIND OUT", why)

    def test_the_refusal_names_the_decisive_experiment_shape(self):
        """The thing that actually ended the first half of that week: a test
        that cannot fail if the mechanism works."""
        self.assertIn("cannot fail if the mechanism works",
                      A.check(rows(2), P, CLASS)[1])

    def test_the_refusal_tells_you_to_reread_the_literal_ask(self):
        """The answer was in the source file the whole time. The ask said
        tooltip, the six copy strings existed, six rounds of light were built."""
        self.assertIn("literal ask", A.check(rows(2), P, CLASS)[1])


class ChangingTheClassIsThePoint(unittest.TestCase):
    def test_a_different_class_is_allowed_after_a_refusal(self):
        """Otherwise this is a wall rather than a redirection."""
        self.assertEqual(A.check(rows(5), P, "the item name label")[0], A.ALLOW)

    def test_the_same_class_on_a_DIFFERENT_problem_is_allowed(self):
        self.assertEqual(A.check(rows(5), "some other problem", CLASS)[0], A.ALLOW)

    def test_a_passed_attempt_is_not_a_strike(self):
        r = [{"problem": P, "class": CLASS, "outcome": "passed"} for _ in range(9)]
        self.assertEqual(A.check(r, P, CLASS)[0], A.ALLOW)

    def test_the_strike_limit_is_honoured_rather_than_decorative(self):
        self.assertEqual(A.check(rows(2), P, CLASS, strikes=3)[0], A.ALLOW)
        self.assertEqual(A.check(rows(3), P, CLASS, strikes=3)[0], A.REFUSE)


class TheOutsideView(unittest.TestCase):
    """Reference class forecasting: an estimate made from inside a problem is
    systematically optimistic, so the number has to come from outside it."""

    def test_it_counts_across_problems_not_within_one(self):
        r = (rows(2, problem="a") + rows(3, problem="b")
             + [{"problem": "c", "class": CLASS, "outcome": "passed"}])
        tried, worked, _ = A.base_rate(r, CLASS)
        self.assertEqual((tried, worked), (6, 1))

    def test_an_unseen_class_is_NO_DATA_rather_than_encouraging(self):
        tried, worked, note = A.base_rate(rows(3), "never tried this")
        self.assertEqual((tried, worked), (0, 0))
        self.assertIn(A.NODATA, note)
        self.assertIn("not encouraging", note)

    def test_the_refusal_carries_the_outside_view(self):
        self.assertIn("worked 0", A.check(rows(2), P, CLASS)[1])


class NoDataIsNeverPermission(unittest.TestCase):
    def test_an_unreadable_ledger_is_NO_DATA_not_ALLOW(self):
        got, why = A.check(None, P, CLASS)
        self.assertEqual(got, A.NODATA)
        self.assertIn("not permission to try again", why)

    def test_the_three_exit_codes_are_distinct(self):
        self.assertEqual(len({A.EXIT_ALLOW, A.EXIT_REFUSE, A.EXIT_NODATA}), 3)

    def test_a_refusal_does_not_exit_zero(self):
        self.assertNotEqual(A.EXIT_REFUSE, A.EXIT_ALLOW)

    def test_one_corrupt_line_does_not_blind_the_whole_ledger(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "attempts.jsonl")
        A.record(P, CLASS, "failed", store=store)
        with open(store, "a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
        A.record(P, CLASS, "failed", store=store)
        self.assertEqual(A.check(A.read(store), P, CLASS)[0], A.REFUSE)


class ItWritesAndReadsBack(unittest.TestCase):
    def test_a_recorded_failure_moves_the_verdict(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "attempts.jsonl")
        self.assertEqual(A.check(A.read(store), P, CLASS)[0], A.ALLOW)
        A.record(P, CLASS, "failed", store=store)
        A.record(P, CLASS, "failed", store=store)
        self.assertEqual(A.check(A.read(store), P, CLASS)[0], A.REFUSE)

    def test_a_missing_store_reads_as_empty_not_as_None(self):
        """Empty means nothing has been tried, which is knowable. None means the
        store could not be read, which is not."""
        self.assertEqual(A.read(os.path.join(tempfile.mkdtemp(), "nope.jsonl")), [])


class TheRefusalDoesTheReadingItself(unittest.TestCase):
    """The refusal used to end in prose telling a human to go reread the ask
    and go find out how somebody else solved it. Prose did not stop the room
    week either: the refusal now invokes a research step and quotes what it
    found, instead of handing back a chore."""

    def _lessons_file(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "lessons.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"trigger": CLASS, "solves": CLASS,
                                  "note": "use the reserved holder, not opacity"}) + "\n")
        return path

    def test_one_and_two_attempts_trigger_nothing(self):
        """Driven backwards: while the class is still allowed, nothing is
        researched. None, not an empty finding, because there is nothing yet
        to refuse."""
        lessons = self._lessons_file()
        self.assertIsNone(A.refusal_research(rows(0), P, CLASS, lessons_path=lessons))
        self.assertIsNone(A.refusal_research(rows(1), P, CLASS, lessons_path=lessons))

    def test_the_third_attempt_triggers_research_with_a_real_reference(self):
        finding = A.refusal_research(rows(2), P, CLASS, lessons_path=self._lessons_file())
        self.assertIsNotNone(finding)
        self.assertEqual(finding["reread_ask"], P)
        self.assertTrue(finding["lesson"]["found"])
        self.assertIn("reserved holder", finding["lesson"]["note"])

    def test_a_missing_lessons_file_is_NO_DATA_never_fabricated(self):
        finding = A.refusal_research(rows(2), P, CLASS,
                                      lessons_path="/nonexistent/nope.jsonl")
        self.assertFalse(finding["lesson"]["found"])
        self.assertIn(A.NODATA, finding["lesson"]["reason"])

    def test_the_failing_checks_own_output_is_the_fallback_reference(self):
        """No lesson on hand: the ledger's own recorded note (what
        run_evidence.py stores per failed attempt) resolves instead."""
        r = [{"problem": P, "class": CLASS, "outcome": "failed",
              "note": "AssertionError: 4 != 5"},
             {"problem": P, "class": CLASS, "outcome": "failed",
              "note": "AssertionError: 4 != 5, second run"}]
        finding = A.refusal_research(r, P, CLASS, lessons_path="/nonexistent/nope.jsonl")
        self.assertFalse(finding["lesson"]["found"])
        self.assertTrue(finding["check_output"]["found"])
        self.assertIn("AssertionError", finding["check_output"]["note"])

    def test_a_prior_solution_path_is_named_when_one_exists(self):
        d = tempfile.mkdtemp()
        other_store = os.path.join(d, "attempts.jsonl")
        A.record("a different project's version of this", CLASS, "passed", store=other_store)
        finding = A.refusal_research(rows(2), P, CLASS, lessons_path=self._lessons_file(),
                                      other_ledgers=[other_store])
        self.assertTrue(finding["prior_solution"]["found"])
        self.assertEqual(finding["prior_solution"]["path"], other_store)

    def test_no_other_ledgers_given_is_NO_DATA_never_guessed(self):
        finding = A.refusal_research(rows(2), P, CLASS, lessons_path=self._lessons_file())
        self.assertFalse(finding["prior_solution"]["found"])
        self.assertIn(A.NODATA, finding["prior_solution"]["reason"])

    def test_the_refusal_text_quotes_the_finding_not_a_go_read_instruction(self):
        why = A.check(rows(2), P, CLASS, lessons_path=self._lessons_file())[1]
        self.assertIn(P, why)
        self.assertIn("reserved holder", why)
        self.assertNotIn("reading the literal ask again", why)
        self.assertNotIn("reading how somebody else already solved this", why)


if __name__ == "__main__":
    unittest.main()
