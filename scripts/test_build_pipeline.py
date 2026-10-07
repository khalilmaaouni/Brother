import os
import sys
import unittest

try:
    from . import build_pipeline
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_pipeline


class BuildPipelineTests(unittest.TestCase):
    def test_lane_grades_first_build(self):
        calls = []
        def grade_fn(build):
            calls.append(build['id'])
            return {'status': build_pipeline.PASS}
        lane = {
            'id': 'L1',
            'builds': [
                {'id': 'first', 'ok': True, 'seconds': 1},
                {'id': 'second', 'ok': True, 'seconds': 2},
            ],
        }
        out = build_pipeline.run_lane(
            lane,
            grade_fn,
            lambda g: {'status': build_pipeline.PASS, 'adversary': 'adv'},
            lambda p: {'status': build_pipeline.PASS},
        )
        self.assertEqual(calls, ['first'])
        self.assertEqual(out['status'], build_pipeline.PASS)

    def test_single_build_grades_at_once(self):
        calls = []
        def grade_fn(build):
            calls.append(build['id'])
            return {'status': build_pipeline.PASS}
        lane = {'id': 'L1', 'builds': [{'id': 'only', 'ok': True, 'seconds': 1}]}
        out = build_pipeline.run_lane(
            lane,
            grade_fn,
            lambda g: {'status': build_pipeline.PASS, 'adversary': 'adv'},
            lambda p: {'status': build_pipeline.PASS},
        )
        self.assertEqual(calls, ['only'])
        self.assertEqual(out['status'], build_pipeline.PASS)

    def test_empty_lane_blocked_no_judge(self):
        judge_calls = []
        def judge_fn(p):
            judge_calls.append(p)
            return {'status': build_pipeline.PASS}
        lane = {'id': 'L1', 'builds': []}
        out = build_pipeline.run_lane(
            lane,
            lambda b: {'status': build_pipeline.PASS},
            lambda g: {'status': build_pipeline.PASS, 'adversary': 'adv'},
            judge_fn,
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)
        self.assertEqual(judge_calls, [])
        self.assertFalse(out.get('judge_called', False))

    def test_missing_probe_no_data(self):
        out = build_pipeline.probe_lane(
            {'status': build_pipeline.PASS},
            lambda g: None,
        )
        self.assertEqual(out['status'], build_pipeline.NO_DATA)
        self.assertNotEqual(out['status'], build_pipeline.PASS)

    def test_missing_adversary_no_data(self):
        out = build_pipeline.judge_lane(
            [{'status': build_pipeline.PASS}],
            lambda p: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.NO_DATA)
        self.assertNotEqual(out['status'], build_pipeline.PASS)

    def test_empty_adversary_no_data(self):
        out = build_pipeline.judge_lane(
            [{'status': build_pipeline.PASS, 'adversary': ''}],
            lambda p: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.NO_DATA)
        self.assertNotEqual(out['status'], build_pipeline.PASS)

    def test_run_lane_missing_adversary_no_data_no_judge(self):
        judge_calls = []
        def judge_fn(p):
            judge_calls.append(p)
            return {'status': build_pipeline.PASS}
        lane = {'id': 'L1', 'builds': [{'id': 'b', 'ok': True, 'seconds': 1}]}
        out = build_pipeline.run_lane(
            lane,
            lambda b: {'status': build_pipeline.PASS},
            lambda g: {'status': build_pipeline.PASS},
            judge_fn,
        )
        self.assertEqual(out['status'], build_pipeline.NO_DATA)
        self.assertEqual(judge_calls, [])
        self.assertFalse(out.get('judge_called', False))

    def test_malformed_build_ok_string_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': 'b', 'ok': 'yes', 'seconds': 1}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_seconds_bool_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': 'b', 'ok': True, 'seconds': True}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_seconds_inf_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': 'b', 'ok': True, 'seconds': float('inf')}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_seconds_nan_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': 'b', 'ok': True, 'seconds': float('nan')}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_id_bytes_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': b'\xff\xfe', 'ok': True, 'seconds': 1}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_id_path_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': '../x', 'ok': True, 'seconds': 1}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_id_none_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': None, 'ok': True, 'seconds': 1}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_id_unhashable_blocked(self):
        out = build_pipeline.grade_lane(
            [{'id': [], 'ok': True, 'seconds': 1}],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_malformed_build_none_blocked(self):
        out = build_pipeline.grade_lane(
            [None],
            lambda b: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_lane_id_path_blocked(self):
        out = build_pipeline.run_lane(
            {'id': '../x', 'builds': [{'id': 'b', 'ok': True, 'seconds': 1}]},
            lambda b: {'status': build_pipeline.PASS},
            lambda g: {'status': build_pipeline.PASS, 'adversary': 'adv'},
            lambda p: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_grade_fn_raises_blocked(self):
        def grade_fn(build):
            raise RuntimeError('boom')
        out = build_pipeline.grade_lane(
            [{'id': 'b', 'ok': True, 'seconds': 1}],
            grade_fn,
        )
        self.assertEqual(out['status'], build_pipeline.BLOCKED)

    def test_corrupt_build_gives_fail_grade(self):
        def grade_fn(build):
            return {'status': build_pipeline.FAIL}
        out = build_pipeline.run_lane(
            {'id': 'L1', 'builds': [{'id': 'b1', 'ok': False, 'seconds': 1}]},
            grade_fn,
            lambda g: {'status': build_pipeline.PASS, 'adversary': 'adv'},
            lambda p: {'status': build_pipeline.PASS},
        )
        self.assertEqual(out['status'], build_pipeline.FAIL)

    def test_hostile_input_refused(self):
        self.assertEqual(
            build_pipeline.run_lane(None, lambda b: {}, lambda g: {}, lambda p: {})['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.grade_lane('not a list', lambda b: {})['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.grade_lane([None], lambda b: {})['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.grade_lane(
                [{'id': 'b', 'ok': True, 'seconds': float('nan')}], lambda b: {}
            )['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.grade_lane(
                [{'id': 'b', 'ok': True, 'seconds': True}], lambda b: {}
            )['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.grade_lane([{'id': [], 'ok': True}], lambda b: {})['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.probe_lane(None, lambda g: {})['status'],
            build_pipeline.BLOCKED,
        )
        self.assertEqual(
            build_pipeline.judge_lane('not a list', lambda p: {})['status'],
            build_pipeline.BLOCKED,
        )

    def test_judge_uses_pass_adversary(self):
        calls = []
        def judge_fn(probe):
            calls.append(probe)
            return {'status': build_pipeline.PASS}
        probes = [
            {'status': build_pipeline.FAIL, 'adversary': 'a'},
            {'status': build_pipeline.PASS, 'adversary': 'b'},
            {'status': build_pipeline.PASS, 'adversary': 'c'},
        ]
        out = build_pipeline.judge_lane(probes, judge_fn)
        self.assertEqual(out['status'], build_pipeline.PASS)
        self.assertEqual(calls[0]['adversary'], 'b')


if __name__ == '__main__':
    unittest.main()
