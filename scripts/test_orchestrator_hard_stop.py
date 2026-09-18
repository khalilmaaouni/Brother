#!/usr/bin/env python3
"""Tests for scripts/orchestrator_hard_stop.py. Plain unittest, no sleeps,
no real clock: every `now` is a literal number handed in by the test.

Run: python3 scripts/test_orchestrator_hard_stop.py -v
"""

import unittest

import orchestrator_hard_stop as hs
import orchestrator_invariants as invariants


DRAIN_START = 1000.0
HARD_STOP = 1100.0


class PhaseBoundaries(unittest.TestCase):
    def test_before_drain_start_is_open(self):
        self.assertEqual(
            hs.phase(DRAIN_START - 1, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "OPEN")

    def test_exactly_at_drain_start_is_draining(self):
        # Inclusive at drain_start: drain rules apply from this instant on.
        self.assertEqual(
            hs.phase(DRAIN_START, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "DRAINING")

    def test_one_tick_after_drain_start_is_draining(self):
        self.assertEqual(
            hs.phase(DRAIN_START + 1, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "DRAINING")

    def test_one_tick_before_hard_stop_is_draining(self):
        self.assertEqual(
            hs.phase(HARD_STOP - 1, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "DRAINING")

    def test_exactly_at_hard_stop_is_stopped(self):
        # Inclusive at hard_stop: rule 4, nothing admitted AT the stop.
        self.assertEqual(
            hs.phase(HARD_STOP, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "STOPPED")

    def test_one_tick_after_hard_stop_is_stopped(self):
        self.assertEqual(
            hs.phase(HARD_STOP + 1, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "STOPPED")

    def test_far_past_hard_stop_is_stopped(self):
        self.assertEqual(
            hs.phase(HARD_STOP + 1e9, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "STOPPED")

    def test_now_far_before_drain_start_is_open(self):
        self.assertEqual(
            hs.phase(DRAIN_START - 1e9, drain_start=DRAIN_START, hard_stop=HARD_STOP),
            "OPEN")

    def test_zero_width_drain_window(self):
        # drain_start == hard_stop: OPEN right up to it, then STOPPED,
        # DRAINING is never observed for any `now`.
        same = 500.0
        self.assertEqual(hs.phase(same - 1, drain_start=same, hard_stop=same), "OPEN")
        self.assertEqual(hs.phase(same, drain_start=same, hard_stop=same), "STOPPED")
        self.assertEqual(hs.phase(same + 1, drain_start=same, hard_stop=same), "STOPPED")

    def test_drain_start_after_hard_stop_is_refused(self):
        with self.assertRaises(hs.HardStopError):
            hs.phase(0, drain_start=HARD_STOP, hard_stop=DRAIN_START)


def _unit(task_class="implementation", **extra):
    unit = {"id": "u1", "task_class": task_class}
    unit.update(extra)
    return unit


class Admits(unittest.TestCase):
    def test_open_admits_long_unit(self):
        ok, reason = hs.admits(_unit(), "OPEN", estimated_cost="LONG")
        self.assertTrue(ok)
        self.assertIn("OPEN", reason)

    def test_open_admits_with_no_declared_cost(self):
        ok, _reason = hs.admits(_unit(), "OPEN")
        self.assertTrue(ok)

    def test_draining_refuses_long_unit(self):
        ok, reason = hs.admits(_unit(), "DRAINING", estimated_cost="LONG")
        self.assertFalse(ok)
        self.assertIn("LONG", reason)

    def test_draining_refuses_undeclared_cost(self):
        ok, reason = hs.admits(_unit(), "DRAINING")
        self.assertFalse(ok)
        self.assertIn("LONG", reason)

    def test_draining_refuses_unrecognised_cost_string(self):
        ok, _reason = hs.admits(_unit(), "DRAINING", estimated_cost="medium")
        self.assertFalse(ok)

    def test_draining_admits_short_unit(self):
        ok, reason = hs.admits(_unit(), "DRAINING", estimated_cost="SHORT")
        self.assertTrue(ok)
        self.assertIn("SHORT", reason)

    def test_draining_refuses_repair_even_when_short(self):
        # Rule 3: risk, not duration, decides this one.
        ok, reason = hs.admits(_unit(task_class="repair"), "DRAINING",
                                estimated_cost="SHORT")
        self.assertFalse(ok)
        self.assertIn("repair", reason)

    def test_stopped_refuses_short_unit(self):
        ok, reason = hs.admits(_unit(), "STOPPED", estimated_cost="SHORT")
        self.assertFalse(ok)
        self.assertIn("STOPPED", reason)

    def test_stopped_refuses_everything_unconditionally(self):
        for cost in (None, "SHORT", "LONG", "whatever"):
            ok, _reason = hs.admits(_unit(), "STOPPED", estimated_cost=cost)
            self.assertFalse(ok)

    def test_unknown_phase_raises(self):
        with self.assertRaises(ValueError):
            hs.admits(_unit(), "SOMETHING-ELSE")

    def test_unknown_task_class_raises(self):
        unit = {"id": "u1", "task_class": "not-a-real-class"}
        with self.assertRaises(ValueError):
            hs.admits(unit, "DRAINING", estimated_cost="SHORT")

    def test_missing_task_class_raises(self):
        with self.assertRaises(ValueError):
            hs.admits({"id": "u1"}, "OPEN")


class DrainScenarios(unittest.TestCase):
    def _run(self, units, phase="DRAINING", now=1050.0):
        integrated = []
        parked = []

        def integrate(unit):
            integrated.append(unit["id"])
            return "DONE"

        def park(unit, record):
            parked.append((unit["id"], record))

        result = hs.drain(units, phase=phase, integrate=integrate, park=park, now=now)
        return result, integrated, parked

    def test_empty_unit_list(self):
        result, integrated, parked = self._run([])
        self.assertEqual(result.integrated, [])
        self.assertEqual(result.parked, [])
        self.assertEqual(result.already_terminal, [])
        self.assertEqual(result.units, {})
        self.assertEqual(integrated, [])
        self.assertEqual(parked, [])

    def test_already_terminal_unit_is_left_alone(self):
        units = [{"id": "u1", "state": "DONE"}]
        result, integrated, parked = self._run(units)
        self.assertEqual(result.already_terminal, ["u1"])
        self.assertEqual(result.units["u1"], "DONE")
        self.assertEqual(integrated, [])
        self.assertEqual(parked, [])

    def test_green_unit_integrates_not_parks(self):
        # THE BAD STATE A GREEN RUN WOULD ALSO PASS: a drain that parks
        # everything immediately would satisfy assert_no_ambiguous_state
        # too, while destroying in-flight work. This is the test that
        # would catch it.
        units = [{"id": "u1", "state": "WORKER-PASS", "ready": True}]
        result, integrated, parked = self._run(units)
        self.assertEqual(result.integrated, ["u1"])
        self.assertEqual(result.parked, [])
        self.assertEqual(integrated, ["u1"])
        self.assertEqual(result.units["u1"], "DONE")

    def test_drain_lands_green_work_not_just_parks_it(self):
        units = [
            {"id": "green", "state": "WORKER-PASS", "ready": True},
            {"id": "still-running", "state": "RUNNING"},
        ]
        result, integrated, parked = self._run(units)
        self.assertEqual(result.integrated, ["green"])
        self.assertEqual(result.parked, ["still-running"])
        self.assertEqual(integrated, ["green"])
        self.assertEqual([p[0] for p in parked], ["still-running"])

    def test_not_ready_unit_is_parked_with_exact_state(self):
        units = [{
            "id": "u1", "state": "WORKER-CLAIMED", "task_class": "implementation",
            "attempt_count": 2, "last_failure": "timeout",
        }]
        result, integrated, parked = self._run(units, now=1075.0)
        self.assertEqual(result.parked, ["u1"])
        self.assertEqual(integrated, [])
        [(pid, record)] = parked
        self.assertEqual(pid, "u1")
        self.assertEqual(record["unit_id"], "u1")
        self.assertEqual(record["state_at_park"], "WORKER-CLAIMED")
        self.assertEqual(record["attempt_count"], 2)
        self.assertEqual(record["last_failure"], "timeout")
        self.assertEqual(record["parked_at"], 1075.0)
        self.assertIn("next_action", record)
        self.assertTrue(record["next_action"])
        self.assertIn("reason", record)

    def test_repair_park_record_names_repair_as_next_action(self):
        units = [{
            "id": "u1", "state": "REPAIRABLE", "task_class": "repair",
            "attempt_count": 1,
        }]
        _result, _integrated, parked = self._run(units)
        [(_pid, record)] = parked
        self.assertIn("repair", record["next_action"])
        self.assertIn("2", record["next_action"])  # attempt_count + 1

    def test_missing_ready_flag_defaults_to_parked_not_integrated(self):
        # Safe default: absence of an explicit "ready" is never read as
        # "assume ready".
        units = [{"id": "u1", "state": "VERIFYING"}]
        result, integrated, _parked = self._run(units)
        self.assertEqual(result.parked, ["u1"])
        self.assertEqual(integrated, [])

    def test_integration_failure_parks_instead_of_raising(self):
        units = [{"id": "u1", "state": "WORKER-PASS", "ready": True}]

        def failing_integrate(unit):
            raise RuntimeError("canonical regression on merge")

        parked = []

        def park(unit, record):
            parked.append((unit["id"], record))

        result = hs.drain(units, phase="DRAINING", integrate=failing_integrate,
                           park=park, now=1050.0)
        self.assertEqual(result.integrated, [])
        self.assertEqual(result.parked, ["u1"])
        [(_pid, record)] = parked
        self.assertIn("canonical regression", record["last_failure"])
        self.assertEqual(result.units["u1"], "WORKER-PASS")  # state untouched

    def test_park_raising_propagates_unchanged(self):
        units = [{"id": "u1", "state": "RUNNING"}]

        def integrate(unit):
            return "DONE"

        def failing_park(unit, record):
            raise OSError("disk full")

        with self.assertRaises(OSError):
            hs.drain(units, phase="DRAINING", integrate=integrate,
                      park=failing_park, now=1050.0)

    def test_unknown_state_raises(self):
        units = [{"id": "u1", "state": "NOT-A-REAL-STATE"}]

        def integrate(unit):
            return "DONE"

        def park(unit, record):
            pass

        with self.assertRaises(ValueError):
            hs.drain(units, phase="DRAINING", integrate=integrate, park=park,
                      now=1050.0)

    def test_drain_refuses_phase_open(self):
        with self.assertRaises(hs.HardStopError):
            hs.drain([], phase="OPEN", integrate=lambda u: None,
                      park=lambda u, r: None, now=1000.0)

    def test_drain_works_at_phase_stopped_too(self):
        units = [
            {"id": "green", "state": "CANONICAL-VERIFY", "ready": True},
            {"id": "still-running", "state": "RUNNING"},
        ]
        result, integrated, parked = self._run(units, phase="STOPPED", now=1150.0)
        self.assertEqual(result.integrated, ["green"])
        self.assertEqual(result.parked, ["still-running"])

    def test_unrecognised_integrate_return_raises(self):
        units = [{"id": "u1", "state": "WORKER-PASS", "ready": True}]

        def integrate(unit):
            return "NOT-A-REAL-STATE"

        with self.assertRaises(ValueError):
            hs.drain(units, phase="DRAINING", integrate=integrate,
                      park=lambda u, r: None, now=1050.0)


class AssertNoAmbiguousState(unittest.TestCase):
    def test_all_terminal_passes_silently(self):
        units = [
            {"id": "a", "state": "DONE"},
            {"id": "b", "state": "PARKED"},
            {"id": "c", "state": "EXHAUSTED"},
            {"id": "d", "state": "AWAITING-HUMAN"},
            {"id": "e", "state": "CANCELLED"},
        ]
        self.assertIsNone(hs.assert_no_ambiguous_state(units))

    def test_empty_passes_silently(self):
        self.assertIsNone(hs.assert_no_ambiguous_state([]))
        self.assertIsNone(hs.assert_no_ambiguous_state({}))

    def test_non_terminal_unit_raises_naming_it(self):
        units = [{"id": "stuck", "state": "RUNNING"}]
        with self.assertRaises(hs.HardStopError) as ctx:
            hs.assert_no_ambiguous_state(units)
        self.assertIn("stuck", str(ctx.exception))
        self.assertIn("RUNNING", str(ctx.exception))

    def test_names_every_offender_not_just_the_first(self):
        units = [
            {"id": "a", "state": "RUNNING"},
            {"id": "b", "state": "DONE"},
            {"id": "c", "state": "WORKER-CLAIMED"},
        ]
        with self.assertRaises(hs.HardStopError) as ctx:
            hs.assert_no_ambiguous_state(units)
        message = str(ctx.exception)
        self.assertIn("'a'", message)
        self.assertIn("'c'", message)
        self.assertNotIn("'b'", message)

    def test_accepts_drain_result_units_mapping_directly(self):
        mapping = {"a": "DONE", "b": "RUNNING"}
        with self.assertRaises(hs.HardStopError) as ctx:
            hs.assert_no_ambiguous_state(mapping)
        self.assertIn("b", str(ctx.exception))

    def test_unrecognised_state_is_named_not_silently_passed(self):
        units = [{"id": "weird", "state": "NOT-A-REAL-STATE"}]
        with self.assertRaises(hs.HardStopError) as ctx:
            hs.assert_no_ambiguous_state(units)
        self.assertIn("weird", str(ctx.exception))

    def test_reuses_invariants_terminal_states_not_a_private_copy(self):
        # If TERMINAL_STATES ever grew a new member, this module must see
        # it without being edited: proof that it imports rather than
        # restates the set.
        for state in invariants.TERMINAL_STATES:
            self.assertIsNone(hs.assert_no_ambiguous_state([{"id": "x", "state": state}]))


class EndToEndDrainThenAssert(unittest.TestCase):
    def test_full_run_lands_clean_after_drain(self):
        units = [
            {"id": "green1", "state": "WORKER-PASS", "ready": True},
            {"id": "green2", "state": "REVIEW", "ready": True},
            {"id": "unfinished", "state": "RUNNING", "attempt_count": 1},
            {"id": "already-done", "state": "DONE"},
        ]

        def integrate(unit):
            return "DONE"

        parked_records = {}

        def park(unit, record):
            parked_records[unit["id"]] = record

        result = hs.drain(units, phase="DRAINING", integrate=integrate,
                           park=park, now=1090.0)
        # Every unit drain() touched must now read terminal: this is the
        # exact handoff assert_no_ambiguous_state expects.
        self.assertIsNone(hs.assert_no_ambiguous_state(result.units))
        self.assertEqual(sorted(result.integrated), ["green1", "green2"])
        self.assertEqual(result.parked, ["unfinished"])
        self.assertIn("unfinished", parked_records)


if __name__ == "__main__":
    unittest.main()
