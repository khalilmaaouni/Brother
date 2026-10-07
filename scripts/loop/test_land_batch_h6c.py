#!/usr/bin/env python3
'''Tests for H6.c blended USD on the LANDED line.'''
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import unit_ledger
import land_batch


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def write_json(path, obj):
    write(path, json.dumps(obj))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='h6c-')
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.runs = os.path.join(self.root, 'runs')
        os.makedirs(self.runs)
        self.ledger = os.path.join(self.root, 'ledger.jsonl')

    def add_run(self, sub='D1.1', stamp='000001', round_n=0, bid=None, jobs_mtime=1000.0, results_mtime=1000.0):
        bid = bid or sub + '-r0'
        rd = os.path.join(self.runs, '%s-%s' % (sub, stamp), 'round%d' % round_n)
        os.makedirs(os.path.join(rd, 'grades'), exist_ok=True)
        write_json(os.path.join(rd, 'jobs.json'), [{'id': bid, 'model': 'deepseek', 'estimated_cost': 0.02}])
        write_json(os.path.join(rd, 'results.json'), [{'id': bid, 'model': 'deepseek', 'ok': True, 'actual_model': 'deepseek/v', 'seconds': 1.0, 'bytes': 2}])
        write(os.path.join(rd, 'grades', bid + '.txt'), 'PASS\n')
        os.utime(os.path.join(rd, 'jobs.json'), (jobs_mtime, jobs_mtime))
        os.utime(os.path.join(rd, 'results.json'), (results_mtime, results_mtime))
        return bid, rd

    def add_ledger(self, rows):
        with open(self.ledger, 'w', encoding='utf-8') as fh:
            for row in rows:
                if isinstance(row, str):
                    fh.write(row + '\n')
                else:
                    fh.write(json.dumps(row) + '\n')

    def good_pair(self, holder='D1.1-r0', reservation='res-1', cost=0.03, at=1050.0):
        return [
            {'type': 'RESERVE', 'reservation_id': reservation, 'holder_id': holder, 'at': at - 10},
            {'type': 'RECONCILE', 'reservation_id': reservation, 'actual_cost': cost, 'at': at},
        ]


class BlendedUsd(Base):
    def test_sums_reconciled_cost_in_window(self):
        self.add_run()
        self.add_ledger(self.good_pair())
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertAlmostEqual(usd, 0.03)
        self.assertEqual(skipped, 0)

    def test_two_rounds_are_summed(self):
        self.add_run(round_n=0, bid='D1.1-r0', jobs_mtime=1000, results_mtime=1000)
        self.add_run(round_n=1, bid='D1.1-r1', jobs_mtime=2000, results_mtime=2000)
        self.add_ledger([
            {'type': 'RESERVE', 'reservation_id': 'r0', 'holder_id': 'D1.1-r0'},
            {'type': 'RECONCILE', 'reservation_id': 'r0', 'actual_cost': 0.03, 'at': 1050},
            {'type': 'RESERVE', 'reservation_id': 'r1', 'holder_id': 'D1.1-r1'},
            {'type': 'RECONCILE', 'reservation_id': 'r1', 'actual_cost': 0.02, 'at': 2050},
        ])
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertAlmostEqual(usd, 0.05)
        self.assertEqual(skipped, 0)

    def test_old_reconcile_outside_window_ignored(self):
        self.add_run()
        self.add_ledger([
            {'type': 'RESERVE', 'reservation_id': 'old', 'holder_id': 'D1.1-r0'},
            {'type': 'RECONCILE', 'reservation_id': 'old', 'actual_cost': 9.99, 'at': 1},
        ])
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertIsNone(usd)
        self.assertEqual(skipped, 0)

    def test_no_cost_row_is_none_never_zero(self):
        self.add_run()
        self.add_ledger([])
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertIsNone(usd)
        self.assertEqual(skipped, 0)

    def test_no_run_folder_is_none(self):
        self.add_ledger(self.good_pair())
        usd, skipped = unit_ledger.blended_usd_detail('D9.9', self.runs, self.ledger)
        self.assertIsNone(usd)
        self.assertEqual(skipped, 0)

    def test_missing_ledger_is_none(self):
        self.add_run()
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, os.path.join(self.root, 'nope.jsonl'))
        self.assertIsNone(usd)
        self.assertEqual(skipped, 0)

    def test_sub_match_is_exact(self):
        self.add_run(sub='D1.10', stamp='000001', bid='D1.10-r0')
        self.add_run(sub='D1x1', stamp='000001', bid='D1x1-r0')
        self.add_ledger([
            {'type': 'RESERVE', 'reservation_id': 'a', 'holder_id': 'D1.10-r0'},
            {'type': 'RECONCILE', 'reservation_id': 'a', 'actual_cost': 0.03, 'at': 1050},
            {'type': 'RESERVE', 'reservation_id': 'b', 'holder_id': 'D1x1-r0'},
            {'type': 'RECONCILE', 'reservation_id': 'b', 'actual_cost': 0.04, 'at': 1050},
        ])
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertIsNone(usd)

    def test_corrupt_rows_skipped_and_counted(self):
        self.add_run()
        bad = [
            'not json',
            json.dumps([1, 2]),
            {'type': 'RECONCILE', 'reservation_id': 'b3', 'actual_cost': True, 'at': 1050},
            {'type': 'RECONCILE', 'reservation_id': 'b4', 'actual_cost': float('nan'), 'at': 1050},
            {'type': 'RECONCILE', 'reservation_id': 'b5', 'actual_cost': -0.01, 'at': 1050},
            {'type': 'RECONCILE', 'reservation_id': 'b6', 'actual_cost': '0.02', 'at': 1050},
            {'type': 'RECONCILE', 'reservation_id': 'b7', 'actual_cost': 0.01, 'at': 'x'},
            {'type': 'RECONCILE', 'reservation_id': [], 'actual_cost': 0.01, 'at': 1050},
        ]
        self.add_ledger(self.good_pair() + bad)
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertAlmostEqual(usd, 0.03)
        self.assertEqual(skipped, len(bad))

    def test_only_corrupt_rows_is_none_with_count(self):
        self.add_run()
        self.add_ledger(['not json', {'type': 'RECONCILE', 'reservation_id': 'x', 'actual_cost': True}])
        usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, self.ledger)
        self.assertIsNone(usd)
        self.assertGreater(skipped, 0)

    def test_hostile_arguments_refused(self):
        for sub in (None, 5, True, [], {}, '', float('nan')):
            with self.subTest(sub=sub):
                usd, skipped = unit_ledger.blended_usd_detail(sub, self.runs, self.ledger)
                self.assertIsNone(usd)
                self.assertEqual(skipped, 0)
        for runs in (None, 5, [], os.path.join(self.root, 'missing')):
            with self.subTest(runs=runs):
                usd, skipped = unit_ledger.blended_usd_detail('D1.1', runs, self.ledger)
                self.assertIsNone(usd)
                self.assertEqual(skipped, 0)

    def test_costs_hostile_ledger_never_crashes(self):
        # red team 2026-09-24: costs(None) raised a raw TypeError out of open(), never a refusal
        # costs() now returns (measured, abandoned) (R3, independent review of RS1, 2026-09-26):
        # a hostile ledger gives an empty pair, never a crash, on either half.
        for bad in (None, 5, True, [], {}, b'', bytearray(b'x'), 0.0):
            with self.subTest(ledger=bad):
                self.assertEqual(unit_ledger.costs(bad), ({}, {}))
        # blended_usd_detail must never pass a hostile ledger through to a crash either
        self.add_run()
        for bad in (None, 5, [], {}, b''):
            with self.subTest(ledger=bad):
                usd, skipped = unit_ledger.blended_usd_detail('D1.1', self.runs, bad)
                self.assertIsNone(usd)

    def test_costs_unchanged(self):
        self.add_ledger(self.good_pair())
        self.assertEqual(unit_ledger.costs(self.ledger), ({'D1.1-r0': [(1050.0, 0.03)]}, {}))


class LandBatchUsd(Base):
    def test_usd_text_number(self):
        self.add_run()
        self.add_ledger(self.good_pair())
        self.assertEqual(land_batch.usd_text('D1.1', self.runs, self.ledger, claude_path=os.path.join(self.root, 'no-claude.jsonl')), 'usd 0.03')

    def test_usd_text_no_data(self):
        self.add_run()
        out = land_batch.usd_text('D1.1', self.runs, os.path.join(self.root, 'missing.jsonl'), claude_path=os.path.join(self.root, 'no-claude.jsonl'))
        self.assertEqual(out, 'usd NO-DATA')
        self.assertNotIn('0.00', out)

    def test_usd_text_notes_skipped_rows(self):
        self.add_run()
        self.add_ledger(['not json', {'type': 'RECONCILE', 'reservation_id': 'x', 'actual_cost': True}])
        out = land_batch.usd_text('D1.1', self.runs, self.ledger, claude_path=os.path.join(self.root, 'no-claude.jsonl'))
        self.assertTrue(out.startswith('usd NO-DATA'))
        self.assertIn('corrupt row(s) skipped', out)

    def test_usd_text_hostile_sub(self):
        for sub in (None, 5, ''):
            with self.subTest(sub=sub):
                out = land_batch.usd_text(sub, self.runs, os.path.join(self.root, 'missing.jsonl'), claude_path=os.path.join(self.root, 'no-claude.jsonl'))
                self.assertEqual(out, 'usd NO-DATA')

    def test_landed_line_carries_usd_per_sub(self):
        line = land_batch.landed_line(['D4.c', 'D4.d'], 'D4', '/tmp/log', {'D4.c': 'usd 0.03', 'D4.d': 'usd NO-DATA'})
        self.assertIn('D4.c usd 0.03', line)
        self.assertIn('D4.d usd NO-DATA', line)
        self.assertIn('LANDED  D4.c, D4.d | units with every sub unit landed (run the unit done-check): D4 |', line)
        self.assertIn('| log /tmp/log', line)
        self.assertEqual(len(re.findall(r'usd (\d+\.\d\d|NO-DATA)', line)), 2)

    def test_landed_line_missing_or_bad_map(self):
        line = land_batch.landed_line(['D4.c'], 'D4', '/tmp/log', {})
        self.assertIn('D4.c usd NO-DATA', line)
        line = land_batch.landed_line(['D4.c'], 'D4', '/tmp/log', None)
        self.assertIn('D4.c usd NO-DATA', line)

    def test_landed_line_refuses_bad_landed(self):
        for bad in (None, [], 'D4.c', [5]):
            with self.subTest(bad=bad):
                self.assertEqual(land_batch.landed_line(bad, 'D4', '/tmp/log', {}), 'REFUSED: landed_line got no sub unit names')

    def test_mark_landed_hostile_argument_lists(self):
        # red team 2026-09-24: mark_landed(None, ...) and mark_landed(..., None) raised a raw TypeError
        self.assertEqual(land_batch.mark_landed(None, [], 'abc'), [])
        self.assertEqual(land_batch.mark_landed([], None, 'abc'), [])
        self.assertEqual(land_batch.mark_landed(None, None, 'abc'), [])
        self.assertEqual(land_batch.mark_landed(5, [1], 'abc'), [])
        self.assertEqual(land_batch.mark_landed([1], 5, 'abc'), [])

    def test_mark_landed_valid_unchanged(self):
        st = os.path.join(self.root, 'STATUS')
        write(st, 'READY x\n')
        written = land_batch.mark_landed([st], ['/x/b.json'], 'abc1234')
        self.assertEqual(written, [st])
        with open(st, encoding='utf-8') as fh:
            body = fh.read()
        self.assertTrue(body.startswith('LANDED /x/b.json at '))
        self.assertTrue(body.endswith(' commit abc1234\n'))

    def test_selftest_still_green(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = land_batch.selftest()
        self.assertEqual(rc, 0, buf.getvalue())


if __name__ == '__main__':
    unittest.main()
