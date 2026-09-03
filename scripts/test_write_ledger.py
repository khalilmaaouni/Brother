"""Calibration for scripts/write_ledger.py, in both directions.

The property this file exists to assert is not that attribution works, it is
that UNATTRIBUTED never reads as MINE, and that RESTORE_OR_REMOVE is offered
LAST and ONLY for a self-attributed change. Treating silence as ownership is
the exact mechanism that deleted about 500 lines from a shared tree on
2026-08-29, and this file drives both the true positive (session A may remove
its own write) and the two refusals (a different session, and no ledger line
at all) so the gate cannot pass by accident.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import write_ledger as wl  # noqa: E402


def temp_ledger():
    fd, path = tempfile.mkstemp(suffix='.jsonl')
    os.close(fd)
    os.remove(path)  # record() must create it fresh; a missing ledger is not an error
    return path


class Normalize(unittest.TestCase):
    def test_an_absolute_and_a_relative_path_to_the_same_file_agree(self):
        rel = 'scripts/write_ledger.py'
        abs_ = os.path.join(REPO_ROOT, rel)
        self.assertEqual(wl.normalize(rel), wl.normalize(abs_))


class Attribution(unittest.TestCase):
    """The three answers, and the one that must never collapse into another."""

    def setUp(self):
        self.ledger = temp_ledger()

    def tearDown(self):
        if os.path.exists(self.ledger):
            os.remove(self.ledger)

    def test_a_path_never_written_is_UNATTRIBUTED(self):
        verdict, _why = wl.attribute('never/touched.py', 'session-a', self.ledger)
        self.assertEqual(verdict, wl.UNATTRIBUTED)

    def test_UNATTRIBUTED_NEVER_READS_AS_MINE(self):
        """The load-bearing assertion of this file. A write that bypassed the
        ledger (an editor, a script outside the harness) must never be treated
        as belonging to whoever happens to ask."""
        verdict, _why = wl.attribute('bypassed/the/tool/layer.py', 'session-a', self.ledger)
        self.assertNotEqual(verdict, wl.MINE)
        self.assertEqual(verdict, wl.UNATTRIBUTED)

    def test_the_writing_session_sees_MINE(self):
        wl.record('shared/file.py', 'session-a', ledger_path=self.ledger)
        verdict, _why = wl.attribute('shared/file.py', 'session-a', self.ledger)
        self.assertEqual(verdict, wl.MINE)

    def test_a_different_session_sees_THEIRS_naming_the_real_owner(self):
        wl.record('shared/file.py', 'session-a', ledger_path=self.ledger)
        verdict, why = wl.attribute('shared/file.py', 'session-b', self.ledger)
        self.assertEqual(verdict, wl.THEIRS)
        self.assertIn('session-a', why)

    def test_the_most_recently_appended_write_wins(self):
        wl.record('shared/file.py', 'session-a', ledger_path=self.ledger)
        wl.record('shared/file.py', 'session-b', ledger_path=self.ledger)
        verdict_b, _why = wl.attribute('shared/file.py', 'session-b', self.ledger)
        verdict_a, why_a = wl.attribute('shared/file.py', 'session-a', self.ledger)
        self.assertEqual(verdict_b, wl.MINE)
        self.assertEqual(verdict_a, wl.THEIRS)
        self.assertIn('session-b', why_a)

    def test_a_corrupt_line_is_skipped_not_raised(self):
        wl.record('shared/file.py', 'session-a', ledger_path=self.ledger)
        with open(self.ledger, 'a', encoding='utf-8') as fh:
            fh.write('{not valid json\n')
        wl.record('other/file.py', 'session-b', ledger_path=self.ledger)
        verdict_a, _ = wl.attribute('shared/file.py', 'session-a', self.ledger)
        verdict_b, _ = wl.attribute('other/file.py', 'session-b', self.ledger)
        self.assertEqual(verdict_a, wl.MINE)
        self.assertEqual(verdict_b, wl.MINE)


class AppendOnly(unittest.TestCase):
    def setUp(self):
        self.ledger = temp_ledger()

    def tearDown(self):
        if os.path.exists(self.ledger):
            os.remove(self.ledger)

    def test_record_never_truncates_a_prior_line(self):
        wl.record('a.py', 'session-a', ledger_path=self.ledger)
        wl.record('b.py', 'session-b', ledger_path=self.ledger)
        with open(self.ledger, encoding='utf-8') as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['path'], 'a.py')
        self.assertEqual(lines[0]['session'], 'session-a')
        self.assertEqual(lines[1]['path'], 'b.py')


class RecoveryOrdering(unittest.TestCase):
    """options_for is pure, driven off the verdict alone, both directions."""

    def test_MINE_offers_all_four_with_remove_last(self):
        options = wl.options_for(wl.MINE)
        self.assertEqual(options,
                          [wl.RESCUE, wl.DECLARE, wl.BREAK_GLASS, wl.RESTORE_OR_REMOVE])

    def test_THEIRS_never_offers_remove(self):
        options = wl.options_for(wl.THEIRS)
        self.assertNotIn(wl.RESTORE_OR_REMOVE, options)
        self.assertEqual(options[0], wl.RESCUE)

    def test_UNATTRIBUTED_never_offers_remove_either(self):
        options = wl.options_for(wl.UNATTRIBUTED)
        self.assertNotIn(wl.RESTORE_OR_REMOVE, options)
        self.assertEqual(options[0], wl.RESCUE)

    def test_rescue_is_first_in_every_verdict(self):
        for verdict in (wl.MINE, wl.THEIRS, wl.UNATTRIBUTED):
            self.assertEqual(wl.options_for(verdict)[0], wl.RESCUE)


class DrivenDemonstration(unittest.TestCase):
    """The exact scenario named in the brief, run through the real ledger and
    the real recovery_options(), not just the pure verdict function."""

    def setUp(self):
        self.ledger = temp_ledger()

    def tearDown(self):
        if os.path.exists(self.ledger):
            os.remove(self.ledger)

    def test_session_a_writes_session_b_is_refused_naming_a(self):
        wl.record('contested/file.py', 'session-a', ledger_path=self.ledger)
        options, verdict, why = wl.recovery_options('contested/file.py', 'session-b', self.ledger)
        self.assertEqual(verdict, wl.THEIRS)
        self.assertIn('session-a', why)
        self.assertNotIn(wl.RESTORE_OR_REMOVE, options)

    def test_session_a_is_offered_removal_last_after_rescue(self):
        wl.record('contested/file.py', 'session-a', ledger_path=self.ledger)
        options, verdict, _why = wl.recovery_options('contested/file.py', 'session-a', self.ledger)
        self.assertEqual(verdict, wl.MINE)
        self.assertEqual(options[0], wl.RESCUE)
        self.assertEqual(options[-1], wl.RESTORE_OR_REMOVE)

    def test_an_unattributed_path_offers_rescue_first_and_never_removal(self):
        options, verdict, _why = wl.recovery_options('nobody/wrote/this.py', 'session-a',
                                                       self.ledger)
        self.assertEqual(verdict, wl.UNATTRIBUTED)
        self.assertEqual(options[0], wl.RESCUE)
        self.assertNotIn(wl.RESTORE_OR_REMOVE, options)


class ExitCodes(unittest.TestCase):
    """The CLI's exit codes, since a hook can only branch on those, never on
    printed text. Exit captured from subprocess.run, never through a pipe."""

    def setUp(self):
        self.ledger = temp_ledger()
        self.script = os.path.join(REPO_ROOT, 'scripts', 'write_ledger.py')

    def tearDown(self):
        if os.path.exists(self.ledger):
            os.remove(self.ledger)

    def run_cli(self, *args):
        result = subprocess.run(
            [sys.executable, self.script] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode, result.stdout, result.stderr

    def test_record_exits_0(self):
        code, _out, _err = self.run_cli('record', '--path', 'x.py', '--session', 'a',
                                         '--ledger', self.ledger)
        self.assertEqual(code, 0)

    def test_remove_check_exits_1_for_a_different_session(self):
        self.run_cli('record', '--path', 'x.py', '--session', 'a', '--ledger', self.ledger)
        code, _out, err = self.run_cli('remove-check', '--path', 'x.py', '--session', 'b',
                                        '--ledger', self.ledger)
        self.assertEqual(code, 1)
        self.assertIn('REFUSED', err)

    def test_remove_check_exits_0_for_the_writing_session(self):
        self.run_cli('record', '--path', 'x.py', '--session', 'a', '--ledger', self.ledger)
        code, out, _err = self.run_cli('remove-check', '--path', 'x.py', '--session', 'a',
                                        '--ledger', self.ledger)
        self.assertEqual(code, 0)
        self.assertIn('ALLOWED', out)

    def test_remove_check_exits_1_for_an_unattributed_path(self):
        code, _out, err = self.run_cli('remove-check', '--path', 'never.py', '--session', 'a',
                                        '--ledger', self.ledger)
        self.assertEqual(code, 1)
        self.assertIn('REFUSED', err)

    def test_attribute_exit_codes_are_0_1_2_for_MINE_THEIRS_UNATTRIBUTED(self):
        self.run_cli('record', '--path', 'x.py', '--session', 'a', '--ledger', self.ledger)
        code_mine, _o, _e = self.run_cli('attribute', '--path', 'x.py', '--session', 'a',
                                          '--ledger', self.ledger)
        code_theirs, _o, _e = self.run_cli('attribute', '--path', 'x.py', '--session', 'b',
                                            '--ledger', self.ledger)
        code_unattr, _o, _e = self.run_cli('attribute', '--path', 'y.py', '--session', 'a',
                                            '--ledger', self.ledger)
        self.assertEqual(code_mine, 0)
        self.assertEqual(code_theirs, 1)
        self.assertEqual(code_unattr, 2)


if __name__ == '__main__':
    unittest.main()
