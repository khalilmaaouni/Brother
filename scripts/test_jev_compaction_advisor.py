'''What scripts/jev_compaction_advisor.py must keep true.

Fixtures here use only made up task and tool ids. Nothing reads a live
repository document, nothing touches the network, and the test file
imports nothing outside the standard library and the module beside it.
'''
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_compaction_advisor as A  # noqa: E402


def turn(kind, tokens, **kwargs):
    value = {'type': kind, 'tokens': tokens}
    value.update(kwargs)
    return value


def base_turns():
    return [
        turn('user', 10, content='task text'),
        turn('tool_call', 20, tool_call_id='a'),
        turn('tool_result', 5, tool_call_id='a'),
        turn('assistant', 60),
    ]


class PairTurnsTests(unittest.TestCase):
    def test_empty_transcript_has_no_units(self):
        self.assertEqual(A.pair_turns([]), [])

    def test_call_and_result_pair_into_one_unit(self):
        turns = [
            turn('tool_call', 10, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a'),
        ]
        self.assertEqual(A.pair_turns(turns), [[0, 1]])

    def test_call_without_result_is_its_own_unit(self):
        turns = [turn('tool_call', 10, tool_call_id='a')]
        self.assertEqual(A.pair_turns(turns), [[0]])

    def test_result_without_call_is_its_own_unit(self):
        turns = [turn('tool_result', 5, tool_call_id='a')]
        self.assertEqual(A.pair_turns(turns), [[0]])

    def test_hostile_turns_refused(self):
        for bad in (None, 'not a list', 42, [None], [1]):
            with self.assertRaises(ValueError):
                A.pair_turns(bad)


class ScoringStateTests(unittest.TestCase):
    def test_returns_task_call_and_result(self):
        turns = [
            turn('user', 10, content='do the thing'),
            turn('tool_call', 20, tool_call_id='a'),
            turn('tool_result', 30, tool_call_id='a'),
        ]
        state = A.scoring_state(turns, [1, 2])
        self.assertEqual(state['task'], 'do the thing')
        self.assertEqual(state['call']['tool_call_id'], 'a')
        self.assertEqual(state['result']['tool_call_id'], 'a')

    def test_unit_without_result_refuses(self):
        turns = [turn('tool_call', 10, tool_call_id='a')]
        with self.assertRaises(ValueError):
            A.scoring_state(turns, [0])

    def test_hostile_arguments_refused(self):
        turns = [
            turn('user', 10, content='t'),
            turn('tool_call', 10, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a'),
        ]
        for bad_turns in (None, 'x', 42):
            with self.assertRaises(ValueError):
                A.scoring_state(bad_turns, [1, 2])
        for bad_unit in (None, 'x', 42, [None], [True], [99], [-1]):
            with self.assertRaises(ValueError):
                A.scoring_state(turns, bad_unit)


class PlanCompactionTests(unittest.TestCase):
    def test_under_limit_is_empty(self):
        plan = A.plan_compaction(
            base_turns(), {0: 0.1, 1: 0.1, 2: 0.1},
            used_tokens=99, limit_tokens=100)
        self.assertEqual(plan['actions'], [])
        self.assertFalse(plan['worth_it'])
        self.assertEqual(plan['freed_tokens'], 0)

    def test_exactly_at_limit_makes_a_plan(self):
        plan = A.plan_compaction(
            base_turns(), {0: 0.1, 1: 0.1, 2: 0.1},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertTrue(plan['worth_it'])
        self.assertEqual(len(plan['actions']), 3)

    def test_score_exactly_threshold_is_kept(self):
        plan = A.plan_compaction(
            base_turns(), {0: 0.1, 1: 0.5, 2: 0.1},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['actions'][1]['action'], 'keep')

    def test_score_below_threshold_is_dropped(self):
        plan = A.plan_compaction(
            base_turns(), {0: 0.1, 1: 0.49, 2: 0.1},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['actions'][1]['action'], 'drop')

    def test_frees_exactly_bar_is_worth_it(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('tool_call', 20, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a'),
            turn('assistant', 65),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.9, 1: 0.1, 2: 0.9},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.25, pinned_recent=0)
        self.assertTrue(plan['worth_it'])
        self.assertEqual(plan['freed_tokens'], 25)

    def test_one_token_under_bar_is_empty(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('tool_call', 20, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a'),
            turn('assistant', 65),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.9, 1: 0.1, 2: 0.9},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.26, pinned_recent=0)
        self.assertEqual(plan['actions'], [])
        self.assertFalse(plan['worth_it'])
        self.assertEqual(plan['freed_tokens'], 0)

    def test_pinned_recent_unit_is_kept(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('tool_call', 20, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a'),
            turn('assistant', 60),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.9, 1: 0.1, 2: 0.1},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=1)
        self.assertEqual(plan['actions'][2]['action'], 'keep')

    def test_reasoning_payload_unit_is_kept(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('reasoning', 50),
            turn('tool_call', 20, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a'),
            turn('assistant', 60),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.9, 1: 0.1, 2: 0.1, 3: 0.9},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['actions'][1]['action'], 'keep')

    def test_unit_sharing_assistant_turn_with_reasoning_is_kept(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('reasoning', 5, link='t1'),
            turn('tool_call', 20, tool_call_id='a', link='t1'),
            turn('tool_result', 5, tool_call_id='a', link='t1'),
            turn('assistant', 60),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.9, 1: 0.9, 2: 0.1, 3: 0.9},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['actions'][2]['action'], 'keep')

    def test_error_result_is_kept(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('tool_call', 20, tool_call_id='a'),
            turn('tool_result', 5, tool_call_id='a', is_error=True),
            turn('assistant', 60),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.9, 1: 0.1, 2: 0.9},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['actions'][1]['action'], 'keep')

    def test_unit_with_no_score_is_kept(self):
        plan = A.plan_compaction(
            base_turns(), {}, used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        for entry in plan['actions']:
            self.assertEqual(entry['action'], 'keep')

    def test_unpaired_call_is_kept(self):
        turns = [
            turn('user', 10, content='task text'),
            turn('tool_call', 20, tool_call_id='a'),
            turn('assistant', 60),
        ]
        plan = A.plan_compaction(
            turns, {0: 0.1, 1: 0.1, 2: 0.1},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['actions'][1]['action'], 'keep')

    def test_rewritten_tokens_after_earliest_changed(self):
        plan = A.plan_compaction(
            base_turns(), {0: 0.9, 1: 0.1, 2: 0.9},
            used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(plan['freed_tokens'], 25)
        self.assertEqual(plan['rewritten_tokens'], 60)

    def test_plan_is_identical_when_asked_twice(self):
        scores = {0: 0.9, 1: 0.1, 2: 0.9}
        first = A.plan_compaction(
            base_turns(), scores, used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        second = A.plan_compaction(
            base_turns(), scores, used_tokens=100, limit_tokens=100,
            min_freed_fraction=0.0, pinned_recent=0)
        self.assertEqual(first, second)

    def test_missing_token_count_refuses(self):
        turns = [turn('user', 10, content='t'), {'type': 'assistant'}]
        with self.assertRaises(ValueError):
            A.plan_compaction(turns, {}, used_tokens=10, limit_tokens=10)

    def test_hostile_arguments_refused(self):
        turns = base_turns()
        cases = [
            (None, {}, 100, 100),
            (turns, None, 100, 100),
            (turns, 'x', 100, 100),
            (turns, {1: float('nan')}, 100, 100),
            (turns, {True: 0.1}, 100, 100),
            (turns, {1.5: 0.1}, 100, 100),
            (turns, {}, None, 100),
            (turns, {}, True, 100),
            (turns, {}, 100, None),
            (turns, {}, -1, 100),
            (turns, {}, 100, -1),
            (turns, {}, 101, 100),
        ]
        for args in cases:
            with self.assertRaises(ValueError):
                A.plan_compaction(*args)

    def test_hostile_keyword_arguments_refused(self):
        turns = base_turns()
        cases = [
            {'keep_at': None},
            {'keep_at': True},
            {'keep_at': float('nan')},
            {'pinned_recent': None},
            {'pinned_recent': True},
            {'pinned_recent': -1},
            {'min_freed_fraction': None},
            {'min_freed_fraction': True},
            {'min_freed_fraction': float('nan')},
            {'min_freed_fraction': -1},
        ]
        for kwargs in cases:
            with self.assertRaises(ValueError):
                A.plan_compaction(
                    turns, {}, used_tokens=100, limit_tokens=100, **kwargs)


class RecallReportTests(unittest.TestCase):
    def test_counts_same_and_different_exactly(self):
        report = A.recall_report(['a', 'b'], ['a', 'c'])
        self.assertEqual(report['same'], 1)
        self.assertEqual(report['different'], 1)
        self.assertEqual(report['recall'], 0.5)

    def test_empty_lists_report_full_recall(self):
        report = A.recall_report([], [])
        self.assertEqual(report['same'], 0)
        self.assertEqual(report['different'], 0)
        self.assertEqual(report['recall'], 1.0)

    def test_mismatched_lengths_refuse(self):
        with self.assertRaises(ValueError):
            A.recall_report(['a'], [])

    def test_hostile_answers_refused(self):
        for bad in (None, 'x', 42):
            with self.assertRaises(ValueError):
                A.recall_report(bad, [])
            with self.assertRaises(ValueError):
                A.recall_report([], bad)
        for bad in ([1], [None], [42]):
            with self.assertRaises(ValueError):
                A.recall_report(bad, bad)


if __name__ == '__main__':
    unittest.main()
