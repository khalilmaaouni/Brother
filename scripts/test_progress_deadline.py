"""What the progress deadline must keep true.

The research that informed this module carried an honest warning worth repeating
here: one agent framework's execution timeout was reported in its own issue
tracker as a parameter that did not actually enforce anything. A documented
limit and an enforced one are different things.

So this file DRIVES the deadline rather than asserting it, including the exact
seventy two minute gap this estate produced on 2026-08-29, when a session made
tool calls continuously while three ready nodes waited and every liveness signal
read healthy.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import progress_deadline as P  # noqa: E402

NOW = 1_000_000
D = P.DEFAULT_DEADLINE_SECONDS


class TheRealFailureIsCaught(unittest.TestCase):
    def test_the_seventy_two_minute_gap_is_STALLED(self):
        """The measured failure, not a hypothetical one."""
        got, why = P.verdict([NOW - 72 * 60], NOW, D, ready_work=3)
        self.assertEqual(got, P.STALLED)
        self.assertIn("72 minute", why)

    def test_the_reason_says_output_does_not_count(self):
        """Because during those 72 minutes the session was busy the whole time,
        and any signal that counted activity would have said healthy."""
        why = P.verdict([NOW - 72 * 60], NOW, D, ready_work=3)[1]
        self.assertIn("prose", why)

    def test_a_recent_commit_is_ADVANCING(self):
        self.assertEqual(P.verdict([NOW - 120], NOW, D)[0], P.ADVANCING)


class TheDeadlineIsENFORCED_NotDocumented(unittest.TestCase):
    """Driven at the boundary in both directions, because a limit nobody
    exercised is a limit nobody knows works."""

    def test_just_inside_the_deadline_is_advancing(self):
        self.assertEqual(P.verdict([NOW - (D - 60)], NOW, D, 3)[0], P.ADVANCING)

    def test_just_outside_the_deadline_is_stalled(self):
        self.assertEqual(P.verdict([NOW - (D + 60)], NOW, D, 3)[0], P.STALLED)

    def test_a_custom_deadline_is_honoured_rather_than_the_default(self):
        """Proves the parameter is consulted, not decoration."""
        five = 5 * 60
        self.assertEqual(P.verdict([NOW - 6 * 60], NOW, five, 3)[0], P.STALLED)
        self.assertEqual(P.verdict([NOW - 6 * 60], NOW, D, 3)[0], P.ADVANCING)


class RestIsNotAStall(unittest.TestCase):
    """Ready work does not change whether a worker is stalled, but it changes
    whether anyone should care, and the verdict must say which."""

    def test_no_events_with_work_waiting_is_STALLED(self):
        self.assertEqual(P.verdict([], NOW, D, ready_work=3)[0], P.STALLED)

    def test_no_events_with_NOTHING_waiting_is_UNKNOWN_not_stalled(self):
        got, why = P.verdict([], NOW, D, ready_work=0)
        self.assertEqual(got, P.UNKNOWN)
        self.assertIn("rest", why)

    def test_a_stall_with_ready_work_names_the_count(self):
        why = P.verdict([NOW - 60 * 60], NOW, D, ready_work=7)[1]
        self.assertIn("7 unit", why)


class SpendWithoutDeliveryIsAlsoAStall(unittest.TestCase):
    """Borrowed from the coding agent that caps COST rather than steps, because
    step counts vary about fivefold across model families while cost does not."""

    def test_advancing_on_time_but_over_the_ceiling_is_STALLED(self):
        got, why = P.verdict([NOW - 60], NOW, D, 0, spend=500, spend_ceiling=100)
        self.assertEqual(got, P.STALLED)
        self.assertIn("paid for", why)

    def test_a_ceiling_of_zero_disables_the_dimension(self):
        """So a caller that does not track spend is not silently failed by it."""
        self.assertEqual(
            P.verdict([NOW - 60], NOW, D, 0, spend=999999, spend_ceiling=0)[0],
            P.ADVANCING)

    def test_under_the_ceiling_is_advancing(self):
        self.assertEqual(
            P.verdict([NOW - 60], NOW, D, 0, spend=50, spend_ceiling=100)[0],
            P.ADVANCING)


class UnknownIsNeverAPass(unittest.TestCase):
    def test_an_unreadable_history_is_UNKNOWN_not_advancing(self):
        got, why = P.verdict(None, NOW, D, 3)
        self.assertEqual(got, P.UNKNOWN)
        self.assertIn("not the same as doing well", why)

    def test_the_three_exit_codes_are_distinct(self):
        self.assertEqual(len({P.EXIT_ADVANCING, P.EXIT_STALLED, P.EXIT_UNKNOWN}), 3)

    def test_unknown_does_not_exit_zero(self):
        """A caller reading only the exit code must not see UNKNOWN as healthy."""
        self.assertNotEqual(P.EXIT_UNKNOWN, P.EXIT_ADVANCING)


class ItCountsOnlyDurableEvents(unittest.TestCase):
    def test_the_durable_list_excludes_activity_signals(self):
        """The exclusions are the design. Every one of these would have called
        the seventy two minutes healthy."""
        for activity in ("stdout", "tokens", "tool_call", "cpu", "heartbeat"):
            self.assertNotIn(activity, P.DURABLE_EVENTS)

    def test_the_durable_list_is_things_that_outlive_the_process(self):
        for durable in ("commit", "check_passed", "state_changed",
                        "remote_verified"):
            self.assertIn(durable, P.DURABLE_EVENTS)


class ItReportsAndNeverKills(unittest.TestCase):
    def test_the_module_exposes_no_kill_or_interrupt(self):
        """Whether a stalled worker should be interrupted, redirected or left
        alone is a judgement, and the argument for this module is that the cheap
        mechanical part must not be tangled with the expensive one."""
        for name in ("kill", "interrupt", "stop_worker", "terminate"):
            self.assertFalse(hasattr(P, name), name)

    def test_it_runs_against_this_repository_and_returns_a_real_verdict(self):
        code = P.main(["--deadline", "1200"])
        self.assertIn(code, (P.EXIT_ADVANCING, P.EXIT_STALLED, P.EXIT_UNKNOWN))


if __name__ == "__main__":
    unittest.main()
