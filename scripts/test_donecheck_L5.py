#!/usr/bin/env python3
'''Tests for scripts/donecheck_L5.py (FX-15.2).'''
import contextlib
import io
import json
import os
import tempfile
import unittest

import donecheck_L5


def _receipt(cmd):
    return ' UNIT DONE 2026-09-29 00:00 : every sub unit landed, and the unit done_check was re-run after the last edit. `%s` printed: PASS: x.' % cmd


def _base_units():
    units = []
    for sid in donecheck_L5.SIBLINGS:
        cmd = 'python3 scripts/donecheck_%s.py' % sid
        units.append({'id': sid, 'state': 'DONE', 'done_check': cmd, 'evidence': _receipt(cmd)})
    return units


class DonecheckL5Test(unittest.TestCase):
    def _write(self, units):
        tmp = tempfile.mkdtemp()
        plan_dir = os.path.join(tmp, 'docs', 'plan')
        os.makedirs(plan_dir)
        with open(os.path.join(plan_dir, 'BROTHER-1.1.0-LAUNCH-WBS.json'), 'w', encoding='utf-8') as handle:
            json.dump({'units': units}, handle)
        return tmp

    def _run(self, tmp, extra=None):
        argv = ['--root', tmp]
        if extra:
            argv.extend(extra)
        return donecheck_L5.main(argv)

    def test_green_fixture_passes(self):
        tmp = self._write(_base_units())
        self.assertEqual(0, self._run(tmp))

    def test_one_sibling_open_is_red(self):
        units = _base_units()
        units[-1]['state'] = 'PARTIAL'
        tmp = self._write(units)
        self.assertEqual(1, self._run(tmp))

    def test_missing_sibling_is_nodata(self):
        units = _base_units()[:-1]
        tmp = self._write(units)
        self.assertEqual(2, self._run(tmp))

    def test_duplicate_sibling_is_nodata(self):
        units = _base_units() + [_base_units()[0]]
        tmp = self._write(units)
        self.assertEqual(2, self._run(tmp))

    def test_done_without_receipt_is_nodata(self):
        units = _base_units()
        units[0]['evidence'] = 'DONE but no receipt'
        tmp = self._write(units)
        self.assertEqual(2, self._run(tmp))

    def test_receipt_for_a_different_command_is_nodata(self):
        units = _base_units()
        units[0]['evidence'] = _receipt('python3 scripts/donecheck_other.py')
        tmp = self._write(units)
        self.assertEqual(2, self._run(tmp))

    def test_unknown_state_is_red_not_green(self):
        units = _base_units()
        units[0]['state'] = 'WEIRD'
        tmp = self._write(units)
        self.assertEqual(1, self._run(tmp))

    def test_screen_done_check_refuses_hostile_input(self):
        for bad in (None, 123, b'python3 x.py', ['python3', 'x.py'], 'python3 -c print(1)', 'python3 x.py; rm -rf /'):
            self.assertIsNone(donecheck_L5.screen_done_check(bad))

    def test_rerun_refuses_launch_tree(self):
        tmp = self._write(_base_units())
        old = os.environ.get('BROTHER_LAUNCH_WORKTREE')
        os.environ['BROTHER_LAUNCH_WORKTREE'] = tmp
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = self._run(tmp, ['--rerun'])
            self.assertEqual(2, code)
            self.assertIn('launch tree', buf.getvalue())
        finally:
            if old is None:
                os.environ.pop('BROTHER_LAUNCH_WORKTREE', None)
            else:
                os.environ['BROTHER_LAUNCH_WORKTREE'] = old

    def test_rerun_without_launch_tree_is_nodata(self):
        tmp = self._write(_base_units())
        old = os.environ.pop('BROTHER_LAUNCH_WORKTREE', None)
        try:
            self.assertEqual(2, self._run(tmp, ['--rerun']))
        finally:
            if old is not None:
                os.environ['BROTHER_LAUNCH_WORKTREE'] = old

    def test_main_rejects_bool_argv(self):
        self.assertEqual(2, donecheck_L5.main(True))

    def test_main_rejects_int_argv(self):
        self.assertEqual(2, donecheck_L5.main(7))

    def test_main_rejects_non_string_root_value(self):
        for bad in (True, float('inf'), float('nan')):
            self.assertEqual(2, donecheck_L5.main(['--root', bad]))

    def test_main_rejects_unhashable_argv_element(self):
        self.assertEqual(2, donecheck_L5.main([{}]))
        self.assertEqual(2, donecheck_L5.main([[]]))


if __name__ == '__main__':
    unittest.main()
