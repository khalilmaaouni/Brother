import os
import sys
import tempfile
import unittest

try:
    from . import build_pipeline
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_pipeline


class BuildPipelineM53Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _table(self, name='verdicts.jsonl'):
        return os.path.join(self.tmp, name)

    def _row(self, lane_id='L1', status='PASS'):
        return {'lane_id': lane_id, 'status': status, 'judge_called': True}

    def test_incremental_row_per_lane(self):
        table = self._table()
        build_pipeline.append_verdict_row(table, self._row('L1'))
        self.assertEqual(len(build_pipeline.read_verdict_table(table)), 1)
        build_pipeline.append_verdict_row(table, self._row('L2'))
        rows = build_pipeline.read_verdict_table(table)
        self.assertEqual([r['lane_id'] for r in rows], ['L1', 'L2'])

    def test_missing_dir_created(self):
        table = os.path.join(self.tmp, 'new', 'deep', 'verdicts.jsonl')
        build_pipeline.append_verdict_row(table, self._row('L1'))
        self.assertTrue(os.path.isfile(table))

    def test_unwritable_dir_blocks(self):
        blocker = os.path.join(self.tmp, 'blocker')
        with open(blocker, 'w') as f:
            f.write('x')
        table = os.path.join(blocker, 'verdicts.jsonl')
        with self.assertRaises(build_pipeline.VerdictRowError) as cm:
            build_pipeline.append_verdict_row(table, self._row('L1'))
        self.assertIn(build_pipeline.BLOCKED, str(cm.exception))
        self.assertIn(table, str(cm.exception))

    def test_corrupt_row_not_written(self):
        table = self._table()
        corrupt = {'status': 'PASS'}
        with self.assertRaises(build_pipeline.VerdictRowError):
            build_pipeline.append_verdict_row(table, corrupt)
        self.assertEqual(build_pipeline.read_verdict_table(table), [])

    def test_validate_rejects_corrupt_row(self):
        ok, reason = build_pipeline.validate_verdict_row(None)
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)
        ok, reason = build_pipeline.validate_verdict_row({'status': 'PASS'})
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)
        ok, reason = build_pipeline.validate_verdict_row({'lane_id': 'L1', 'status': 'MAYBE'})
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)

    def test_nan_row_rejected(self):
        row = self._row('L1')
        row['seconds'] = float('nan')
        ok, reason = build_pipeline.validate_verdict_row(row)
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)
        table = self._table()
        with self.assertRaises(build_pipeline.VerdictRowError):
            build_pipeline.append_verdict_row(table, row)
        self.assertEqual(build_pipeline.read_verdict_table(table), [])

    def test_non_string_key_rejected(self):
        row = self._row('L1')
        row[1] = 'x'
        ok, reason = build_pipeline.validate_verdict_row(row)
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)

    def test_validate_unhashable_key_attempt(self):
        class UnhashableRow(dict):
            def get(self, key, default=None):
                raise TypeError("unhashable type: 'list'")
        row = UnhashableRow()
        ok, reason = build_pipeline.validate_verdict_row(row)
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)

    def test_read_missing_returns_empty(self):
        self.assertEqual(build_pipeline.read_verdict_table(self._table('missing.jsonl')), [])

    def test_read_partial_returns_rows_in_order(self):
        table = self._table()
        build_pipeline.append_verdict_row(table, self._row('L1'))
        build_pipeline.append_verdict_row(table, self._row('L2'))
        rows = build_pipeline.read_verdict_table(table)
        self.assertEqual([r['lane_id'] for r in rows], ['L1', 'L2'])

    def test_hostile_input_refused(self):
        with self.assertRaises(build_pipeline.VerdictRowError):
            build_pipeline.read_verdict_table(None)
        with self.assertRaises(build_pipeline.VerdictRowError):
            build_pipeline.read_verdict_table(b'bytes')
        with self.assertRaises(build_pipeline.VerdictRowError):
            build_pipeline.append_verdict_row(None, self._row('L1'))
        with self.assertRaises(build_pipeline.VerdictRowError):
            build_pipeline.append_verdict_row(self._table(), None)
        ok, reason = build_pipeline.validate_verdict_row([])
        self.assertFalse(ok)
        self.assertIn(build_pipeline.NO_DATA, reason)


if __name__ == '__main__':
    unittest.main()
