#!/usr/bin/env python3
'''H4.b tests: strict worker mix parsing and hostile input refusals.'''
import contextlib
import io
import os
import random
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'loop'))
import worker_mix


class ParseMixTests(unittest.TestCase):
    def test_valid_mix(self):
        self.assertEqual(worker_mix.parse_mix('deepseek:8,muse:2'), [('deepseek', 8), ('muse', 2)])
        self.assertEqual(worker_mix.parse_mix(' DeepSeek:3 , muse:1 '), [('deepseek', 3), ('muse', 1)])

    def test_empty_and_none_refused(self):
        with self.assertRaises(ValueError) as cm:
            worker_mix.parse_mix('')
        self.assertIn('token', str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            worker_mix.parse_mix(None)
        self.assertIn('token', str(cm.exception))

    def test_malformed_token_refused(self):
        for bad in ('deepseek=5', 'deepseek:x', 'deepseek:', ',deepseek:5', 'deepseek:5,'):
            with self.assertRaises(ValueError) as cm:
                worker_mix.parse_mix(bad)
            self.assertIn('token', str(cm.exception))

    def test_zero_refused(self):
        with self.assertRaises(ValueError) as cm:
            worker_mix.parse_mix('deepseek:0')
        self.assertIn('deepseek:0', str(cm.exception))
        with self.assertRaises(ValueError):
            worker_mix.parse_mix('muse:-1')

    def test_repeated_summed_and_said(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = worker_mix.parse_mix('deepseek:5,deepseek:2,muse:1')
        self.assertEqual(got, [('deepseek', 7), ('muse', 1)])
        self.assertIn('repeated', buf.getvalue().lower())
        self.assertIn('deepseek', buf.getvalue())

    def test_parse_strict_alias(self):
        with self.assertRaises(ValueError):
            worker_mix.parse('')


class HostileInputTests(unittest.TestCase):
    def test_arm_of_non_str_returns_none(self):
        self.assertIsNone(worker_mix.arm_of(True))
        self.assertIsNone(worker_mix.arm_of(5))
        self.assertIsNone(worker_mix.arm_of(None))
        self.assertIsNone(worker_mix.arm_of(['deepseek']))
        self.assertEqual(worker_mix.arm_of('deepseek-v4'), 'deepseek')

    def test_arm_stats_none_path_returns_empty(self):
        self.assertEqual(worker_mix.arm_stats(None), {})
        self.assertEqual(worker_mix.arm_stats(5), {})

    def test_patience_none_ledger_no_crash(self):
        got = worker_mix.patience(['deepseek'], {'deepseek': 'bridge'}, ledger_path=None)
        self.assertIsInstance(got, int)

    def test_patience_nan_since_refused(self):
        with self.assertRaises(ValueError):
            worker_mix.patience(['deepseek'], {'deepseek': 'bridge'}, since_s=float('nan'))

    def test_patience_bad_transports_refused(self):
        for bad in (['bridge'], 'bridge'):
            with self.assertRaises(ValueError):
                worker_mix.patience(['deepseek'], bad)
        self.assertIsInstance(worker_mix.patience(['deepseek'], None), int)

    def test_picks_unhashable_known_returns_empty(self):
        self.assertEqual(worker_mix.picks(3, ['deepseek'], [['deepseek']]), [])
        self.assertEqual(worker_mix.picks(3, [['deepseek']], {'deepseek'}), [])

    def test_picks_bool_n_returns_empty(self):
        self.assertEqual(worker_mix.picks(True, ['deepseek'], {'deepseek'}), [])

    def test_shifted_none_mix_refused(self):
        with self.assertRaises(ValueError):
            worker_mix.shifted(None, 3, {}, random.Random(1))
        with self.assertRaises(ValueError):
            worker_mix.shifted('deepseek', 3, {}, random.Random(1))

    def test_picks_float_base_returns_empty(self):
        self.assertEqual(worker_mix.picks(3, 5.0, {'deepseek'}), [])

    def test_picks_float_base_returns_empty_under_a_pin(self):
        # the loop runs with BROTHER_PIN_MODEL set: a hostile base is still refused, never answered by the pin
        self.assertEqual(worker_mix.picks(3, 5.0, {'deepseek'}, env={'BROTHER_PIN_MODEL': 'deepseek'}), [])
        self.assertEqual(worker_mix.picks(3, ['deepseek'], {'deepseek'}, env={'BROTHER_PIN_MODEL': 'deepseek'}), ['deepseek'] * 3)

    def test_picks_float_known_returns_empty(self):
        self.assertEqual(worker_mix.picks(3, ['deepseek'], 5.0), [])

    def test_picks_env_none_with_float_known_returns_empty(self):
        self.assertEqual(worker_mix.picks(3, ['deepseek'], 5.0, env=None), [])

    def test_picks_generator_known_with_non_str_returns_empty(self):
        def gen():
            yield 5
        self.assertEqual(worker_mix.picks(2, ['deepseek'], gen()), [])

    def test_picks_base_unhashable_returns_empty_no_crash(self):
        self.assertEqual(worker_mix.picks(3, [['deepseek']], {'deepseek'}), [])
        self.assertEqual(worker_mix.picks(3, (['deepseek'],), {'deepseek'}), [])


class TheSelftestVerdictMatchesItsExit(unittest.TestCase):
    """A selftest that prints FAILED must not exit 0 (a lane G sweep survivor, 2026-09-27: nothing broke a case)."""

    def test_a_failing_case_prints_failed_and_returns_1(self):
        from unittest import mock
        buffer = io.StringIO()
        with mock.patch.object(worker_mix, 'arm_stats', lambda *a, **k: {}), contextlib.redirect_stdout(buffer):
            code = worker_mix.selftest()
        self.assertIn('FAILED', buffer.getvalue())
        self.assertEqual(code, 1)


class AnEmptyModelNameIsRefused(unittest.TestCase):
    def test_a_token_with_no_model_name_is_refused(self):
        with self.assertRaises(ValueError):
            worker_mix.parse_mix(':3')


class OneBuildIsOneSample(unittest.TestCase):
    '''Finding 7, 2026-09-27: a concurrent append left two physical ledger rows for one build, and the mix counted
    both, so one passing build became two rewards. Readers take one row per (run, round, build).'''

    def ledger(self, rows):
        import json, tempfile
        d = tempfile.mkdtemp(prefix='worker-mix-g7-')
        path = os.path.join(d, 'unit-ledger.jsonl')
        with open(path, 'w') as fh:
            fh.write(''.join(json.dumps(r) + '\n' for r in rows))
        return path

    def test_arm_stats_counts_one_build_once(self):
        import time
        now = time.time()
        row = {'run': 'A.1-120000', 'round': 0, 'build': 'A.1-r0', 'actual_model': 'deepseek/v', 'grade': 'PASS',
               'run_at': now, 'unit_class': 'code'}
        self.assertEqual(worker_mix.arm_stats(self.ledger([row, row]), now=now), {'deepseek': [1, 0]})

    def test_an_empty_answer_fails_the_arm_that_was_asked(self):
        import time
        now = time.time()
        empty = {'run': 'A.1-120000', 'round': 0, 'build': 'A.1-r1', 'model': 'muse', 'actual_model': None, 'ok': False,
                 'grade': 'NO-DATA', 'status': 'RUNNING', 'run_at': now, 'unit_class': 'code', 'call_error': 'RuntimeError: answer is not valid JSON: Expecting value'}
        refused = dict(empty, build='A.1-r2', status='UNFUNDED')
        ungraded = dict(empty, build='A.1-r3', ok=True, actual_model='muse/v', call_error='')
        budget = dict(empty, build='A.1-r4', call_error='BudgetExceeded: reserving $0.02 would bring spend over the cap')
        drained = dict(empty, build='A.1-r5', call_error='DrainRefused: a call of 300 s would outlive the deadline')
        stopped = dict(empty, build='A.1-r6', call_error='Stopped: this process was told to stop')
        unknown = {k: v for k, v in dict(empty, build='A.1-r7').items() if k != 'call_error'}
        blank = dict(empty, build='A.1-r8', call_error='')
        rows = [empty, refused, ungraded, budget, drained, stopped, unknown, blank]
        self.assertEqual(worker_mix.arm_stats(self.ledger(rows), now=now), {'muse': [0, 1]})

    def test_a_call_that_ran_out_of_time_teaches_patience_and_is_not_charged(self):
        import time
        now = time.time()
        rows = [{'run': 'P.1-1', 'round': 0, 'build': 'P.1-r%d' % i, 'model': 'deepseek', 'actual_model': 'deepseek/v',
                 'ok': True, 'seconds': 100.0, 'grade': 'NO-DATA', 'run_at': now, 'call_error': ''} for i in range(6)]
        rows += [{'run': 'P.1-1', 'round': 1, 'build': 'P.1-r%d' % i, 'model': 'deepseek', 'actual_model': None, 'ok': False,
                  'seconds': 420.0, 'grade': 'NO-DATA', 'status': 'RUNNING', 'run_at': now, 'call_error': e}
                 for i, e in enumerate(['STALLED_AFTER_DEADLINE', 'TimeoutExpired: Command timed out',
                                        'RuntimeError: bridge exit 44: no answer from deepseek within 405s'])]
        path = self.ledger(rows)
        self.assertEqual(worker_mix.patience(['deepseek'], {}, ledger_path=path, now=now), 420)
        self.assertEqual(worker_mix.arm_stats(path, now=now), {})

    def test_patience_follows_the_newest_calls_not_the_week(self):
        import time
        now = time.time()
        old = [{'run': 'O.1-1', 'round': 0, 'build': 'O.1-r%d' % i, 'model': 'deepseek', 'ok': True, 'seconds': 100.0,
                'grade': 'NO-DATA', 'run_at': now - 86400 + i} for i in range(1000)]
        new = [{'run': 'N.1-1', 'round': 0, 'build': 'N.1-r%d' % i, 'model': 'deepseek', 'ok': True, 'seconds': 400.0,
                'grade': 'NO-DATA', 'run_at': now - 60 + i * 0.1} for i in range(200)]
        self.assertEqual(worker_mix.patience(['deepseek'], {}, ledger_path=self.ledger(new + old), now=now), 400)

    def test_patience_counts_one_build_once(self):
        import time
        now = time.time()
        rows = [{'run': 'S-1', 'round': 0, 'build': 'S-r%d' % i, 'model': 'sonnet', 'ok': True, 'seconds': 600, 'run_at': now}
                for i in range(4)]
        # four builds, each on disk twice: four samples, under PATIENCE_MIN, so the transport default applies
        self.assertEqual(worker_mix.patience(['sonnet'], {'sonnet': 'claude'}, self.ledger(rows + rows), now=now), 420)


ON, OFF = {'BROTHER_ONE_DEADLINE': 'on'}, {'BROTHER_ONE_DEADLINE': 'off'}
TR = {'deepseek': 'bridge', 'muse': 'bridge', 'sonnet': 'claude', 'astra': 'codex'}


class _Ledger(unittest.TestCase):
    def setUp(self):
        import tempfile, time
        self.d = tempfile.TemporaryDirectory(prefix='worker-mix-fx10-')
        self.addCleanup(self.d.cleanup)
        self.now = time.time()

    def ledger(self, rows):
        import json
        path = os.path.join(self.d.name, 'unit-ledger-%d.jsonl' % len(os.listdir(self.d.name)))
        with open(path, 'w') as fh:
            fh.write(''.join(json.dumps(r) + '\n' for r in rows))
        return path

    def rows(self, model, n, seconds, clock='span', span=None, ok=True, error=''):
        return [{'run': 'F-%s' % model, 'round': 0, 'build': '%s-%s-r%d' % (model, clock, i), 'model': model, 'ok': ok,
                 'seconds': seconds, 'model_seconds': span if clock == 'span' else None, 'model_clock': clock,
                 'run_at': self.now, 'call_error': error} for i in range(n)]

    def pat(self, models, rows, env=ON, registry=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return worker_mix.patience(models, TR, self.ledger(rows), now=self.now, registry=registry, env=env)


class ModelClock(_Ledger):
    '''FX-10.2: with BROTHER_ONE_DEADLINE=on a bridge sample is the dispatcher's reserve to settle span, never the fan
    out's wall clock (which counts the slot wait); switch off, today's clock.'''

    def test_span_is_the_bridge_sample(self):
        self.assertEqual(self.pat(['deepseek'], self.rows('deepseek', 5, 400.0, span=200.0)), 200)

    def test_switch_default_is_today(self):
        rows = self.rows('deepseek', 5, 400.0, span=200.0)
        self.assertEqual(self.pat(['deepseek'], rows, env={}), 400)
        self.assertEqual(self.pat(['deepseek'], rows, env=OFF), 400)
        self.assertFalse(worker_mix.one_deadline({}))

    def test_unknown_switch_value_keeps_today(self):
        rows = self.rows('deepseek', 5, 400.0, span=200.0)
        for value in ('yes', 'ON ', 'On', b'on', '1'):
            with self.subTest(value=value):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    got = worker_mix.patience(['deepseek'], TR, self.ledger(rows), now=self.now, env={'BROTHER_ONE_DEADLINE': value})
                self.assertEqual(got, 400)
                self.assertIn('ONE-DEADLINE NO-DATA', out.getvalue())

    def test_bridge_row_without_span_is_not_a_sample(self):
        self.assertEqual(self.pat(['deepseek'], self.rows('deepseek', 5, 700.0, clock='none')), 150)

    def test_off_bridge_row_uses_seconds(self):
        self.assertEqual(self.pat(['sonnet'], self.rows('sonnet', 5, 380.0, clock='none')), 380)

    def test_open_or_corrupt_rows_are_not_samples(self):
        rows = self.rows('deepseek', 3, 700.0, clock='open') + self.rows('deepseek', 3, 700.0, clock='corrupt')
        self.assertEqual(self.pat(['deepseek'], rows), 150)

    def test_timed_out_call_counts_at_its_span(self):
        rows = self.rows('deepseek', 5, 900.0, span=500.0, ok=False, error='STALLED_AFTER_DEADLINE')
        self.assertEqual(self.pat(['deepseek'], rows), 500)

    def test_one_span_sample_is_too_few(self):
        self.assertEqual(self.pat(['deepseek'], self.rows('deepseek', 1, 800.0, span=800.0)), 150)

    def test_many_models_take_the_slowest_on_the_model_clock(self):
        rows = self.rows('deepseek', 5, 800.0, span=200.0) + self.rows('muse', 5, 800.0, span=320.0)
        self.assertEqual(self.pat(['deepseek', 'muse'], rows), 320)

    def test_model_clock_absent_ledger_uses_defaults(self):
        with contextlib.redirect_stdout(io.StringIO()):
            got = worker_mix.measured(['deepseek', 'astra', 'mystery'], TR, ledger_path=os.path.join(self.d.name, 'absent'))
        self.assertEqual({m: (b['p75_s'], b['source']) for m, b in got.items()},
                         {'deepseek': (150, 'default'), 'astra': (300, 'default'), 'mystery': (420, 'default')})

    def test_measured_block_carries_p90_and_count(self):
        rows = self.rows('deepseek', 11, 900.0, span=None)
        for i, r in enumerate(rows): r['model_seconds'] = 100.0 + 10 * i
        got = worker_mix.measured(['deepseek'], TR, ledger_path=self.ledger(rows), now=self.now)['deepseek']
        self.assertEqual(got, {'p75_s': 170.0, 'p90_s': 190.0, 'n': 11, 'source': 'ledger'})

    def test_hostile_env_is_refused(self):
        with self.assertRaises(ValueError):
            worker_mix.patience(['deepseek'], TR, env='on')


class MeasuredBlock(_Ledger):
    '''FX-10.2: the registry row's optional measured block stands in below PATIENCE_MIN samples; it is read, never
    written, and a corrupt one is NO-DATA and the transport default, never a shorter wait.'''

    def reg(self, block):
        return {'sonnet': {'transport': 'claude', 'measured': block}}

    def test_registry_block_used_below_min(self):
        self.assertEqual(self.pat(['sonnet'], [], registry=self.reg({'p75_s': 600})), 600)

    def test_corrupt_measured_block_is_nodata_not_fast(self):
        for block in ({'p75_s': 'x'}, {'p75_s': float('nan')}, {'p75_s': True}, {'p75_s': 0}, {'p75_s': -5},
                      {'p75_s': float('inf')}, 'not a dict', {}):
            with self.subTest(block=block):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    got = worker_mix.measured(['sonnet'], TR, registry=self.reg(block), ledger_path=self.ledger([]), now=self.now)
                self.assertEqual((got['sonnet']['p75_s'], got['sonnet']['source']), (420, 'default'))
                self.assertIn('MEASURED NO-DATA', out.getvalue())

    def test_ledger_beats_registry_block(self):
        self.assertEqual(self.pat(['sonnet'], self.rows('sonnet', 5, 250.0, clock='none'), registry=self.reg({'p75_s': 600})), 250)

    def test_switch_off_never_reads_the_block(self):
        self.assertEqual(self.pat(['sonnet'], [], env=OFF, registry=self.reg({'p75_s': 600})), 420)

    def test_findings_name_a_long_tail(self):
        block = {'a': {'p75_s': 200, 'p90_s': 301}, 'b': {'p75_s': 200, 'p90_s': 300}, 'c': {'p75_s': 200, 'p90_s': None}}
        self.assertEqual([l.split()[2] for l in worker_mix.findings(block)], ['a'])


class Deadlines(unittest.TestCase):
    '''FX-10.2: the one deadline source. The behaviour table of the spec, row by row, plus its refusals.'''

    def test_the_behaviour_table(self):
        self.assertEqual(worker_mix.deadlines(360), {'call': 540, 'round': 540, 'wave': 1110, 'reap': 1140})
        self.assertEqual(worker_mix.deadlines(900), {'call': 900, 'round': 900, 'wave': 1830, 'reap': 1860})

    def test_deadlines_at_the_floor(self):
        self.assertEqual(worker_mix.deadlines(150), {'call': 420, 'round': 420, 'wave': 870, 'reap': 900})

    def test_round_cut_equals_call_deadline(self):
        for pat in (150, 200, 360, 600, 900):
            d = worker_mix.deadlines(pat)
            self.assertEqual(d['round'], d['call'], pat)

    def test_deadline_is_clipped_to_900(self):
        self.assertEqual(worker_mix.deadlines(800)['call'], 900)

    def test_the_cap_override_lifts_the_one_deadline_and_refuses_a_bad_value(self):
        # owner 2026-10-02: more time for Claude builds; unset keeps 900, a bad value is refused, never read as 900
        old = os.environ.pop('BROTHER_DEADLINE_CAP_S', None)
        self.addCleanup(lambda: os.environ.__setitem__('BROTHER_DEADLINE_CAP_S', old) if old is not None
                        else os.environ.pop('BROTHER_DEADLINE_CAP_S', None))
        self.assertEqual(worker_mix.deadlines(150, knob=1800)['call'], 900)
        os.environ['BROTHER_DEADLINE_CAP_S'] = '1800'
        self.assertEqual(worker_mix.deadlines(150, knob=1800), {'call': 1800, 'round': 1800, 'wave': 3630, 'reap': 3660})
        self.assertEqual(worker_mix.deadlines(800)['call'], 1200)
        for bad in ('abc', '299', '3601', '1e3', '-900'):
            os.environ['BROTHER_DEADLINE_CAP_S'] = bad
            with self.assertRaises(ValueError, msg=bad):
                worker_mix.deadlines(150)
        os.environ['BROTHER_DEADLINE_CAP_S'] = ''
        self.assertEqual(worker_mix.deadlines(800)['call'], 900)

    def test_knob_is_a_lower_bound(self):
        self.assertEqual(worker_mix.deadlines(150, knob=700)['call'], 700)
        self.assertEqual(worker_mix.deadlines(360, knob=1)['call'], 540)

    def test_wave_covers_slot_wait_and_call(self):
        d = worker_mix.deadlines(360)
        self.assertGreaterEqual(d['wave'], 2 * d['call'])
        self.assertLess(d['wave'], d['reap'])

    def test_grace_not_above_settle_is_refused(self):
        for grace, settle in ((30, 30), (10, 30), (60, -1)):
            with self.subTest(grace=grace, settle=settle), self.assertRaises(ValueError):
                worker_mix.deadlines(360, grace=grace, settle=settle)

    def test_hostile_values_are_refused(self):
        for kw in ({'pat': True}, {'pat': '360'}, {'pat': 360.0}, {'pat': 149}, {'pat': 901},
                   {'pat': 360, 'knob': 0}, {'pat': 360, 'knob': -1}, {'pat': 360, 'knob': True}, {'pat': 360, 'grace': None}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                worker_mix.deadlines(**kw)


if __name__ == '__main__':
    unittest.main()
