"""Calibration for scripts/merge_queue.py.

The property under test is the done-check itself: two submissions with
disjoint write sets land in ONE batch and are tested together; two with
overlapping write sets land in separate, sequential batches (serialized);
and a submission whose check could not run is HELD, appearing in neither a
batch nor a rejected list. All fixtures are pure logic, no git repo, no
subprocess, so the suite runs in well under a second.
"""
import contextlib
import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import merge_queue as mq  # noqa: E402


def sub(sid, owns=(), check_cmd=('true',), branch=None):
    return {'id': sid, 'branch': branch or ('feat/%s' % sid.lower()),
            'owns': list(owns) if owns is not None else None,
            'check_cmd': list(check_cmd) if check_cmd is not None else None}


class HeldReason(unittest.TestCase):
    def test_a_declared_submission_with_a_check_command_is_not_held(self):
        self.assertIsNone(mq.held_reason(sub('A', owns=['x.py'])))

    def test_a_missing_check_command_is_held(self):
        reason = mq.held_reason(sub('A', owns=['x.py'], check_cmd=None))
        self.assertIn('check command', reason)

    def test_an_undeclared_write_set_is_held(self):
        """owns is None (never declared) is different from owns=[] (declared
        read-only). Only the first is unreadable and must be held."""
        reason = mq.held_reason(sub('A', owns=None))
        self.assertIn('unreadable write set', reason)

    def test_a_read_only_submission_is_not_held(self):
        self.assertIsNone(mq.held_reason(sub('A', owns=[])))


class PlanQueue(unittest.TestCase):
    def test_two_disjoint_submissions_are_batched_together(self):
        result = mq.plan_queue([sub('A', owns=['x.py']), sub('B', owns=['y.py'])])
        self.assertEqual(len(result['batches']), 1)
        self.assertEqual(sorted(s['id'] for s in result['batches'][0]), ['A', 'B'])
        self.assertEqual(result['held'], [])

    def test_two_overlapping_submissions_are_serialized_into_separate_batches(self):
        """THE LOAD-BEARING CASE. Without this the queue is a FIFO with extra
        steps: it would merge two writers of the same path into one plan."""
        result = mq.plan_queue([sub('A', owns=['x.py']), sub('B', owns=['x.py'])])
        self.assertEqual(len(result['batches']), 2)
        self.assertEqual([s['id'] for b in result['batches'] for s in b], ['A', 'B'])
        for batch in result['batches']:
            self.assertEqual(len(batch), 1)

    def test_a_submission_whose_check_could_not_run_is_held_not_merged_or_rejected(self):
        result = mq.plan_queue([sub('A', owns=['x.py']),
                                 sub('B', owns=['y.py'], check_cmd=None)])
        batched_ids = [s['id'] for b in result['batches'] for s in b]
        self.assertEqual(batched_ids, ['A'])
        self.assertEqual(len(result['held']), 1)
        held_sub, reason = result['held'][0]
        self.assertEqual(held_sub['id'], 'B')
        self.assertIn('check command', reason)

    def test_an_undeclared_write_set_is_held_rather_than_batched_or_serialized(self):
        result = mq.plan_queue([sub('A', owns=None)])
        self.assertEqual(result['batches'], [])
        self.assertEqual(len(result['held']), 1)
        self.assertEqual(result['held'][0][0]['id'], 'A')

    def test_a_third_submission_overlapping_only_the_first_joins_a_second_batch(self):
        """A and B are disjoint (batch 1). C overlaps A but not B, so it must
        still be kept out of batch 1 even though it would fit alongside B
        alone; first-fit against the WHOLE batch, not just one member."""
        result = mq.plan_queue([sub('A', owns=['x.py']), sub('B', owns=['y.py']),
                                 sub('C', owns=['x.py'])])
        self.assertEqual(len(result['batches']), 2)
        self.assertEqual(sorted(s['id'] for s in result['batches'][0]), ['A', 'B'])
        self.assertEqual([s['id'] for s in result['batches'][1]], ['C'])

    def test_read_only_submissions_never_conflict_and_all_batch_together(self):
        result = mq.plan_queue([sub('A', owns=[]), sub('B', owns=[]), sub('C', owns=[])])
        self.assertEqual(len(result['batches']), 1)
        self.assertEqual(len(result['batches'][0]), 3)

    def test_directory_containment_still_conflicts_across_submissions(self):
        """Reused from graph_loop.conflicts(): owning a directory owns what is
        inside it. Proves this module did not fork a weaker copy of the rule."""
        result = mq.plan_queue([sub('A', owns=['docs/plan']),
                                 sub('B', owns=['docs/plan/one.md'])])
        self.assertEqual(len(result['batches']), 2)


class CommandsForBatch(unittest.TestCase):
    def test_emits_merge_and_check_commands_never_a_push(self):
        cmds = mq.commands_for_batch([sub('A', owns=['x.py']), sub('B', owns=['y.py'])])
        joined = '\n'.join(cmds)
        self.assertIn('git merge --no-ff origin/feat/a', joined)
        self.assertIn('git merge --no-ff origin/feat/b', joined)
        self.assertIn('true  # A check', joined)
        self.assertNotIn('git push', joined)


class RenderAndMain(unittest.TestCase):
    def test_render_names_batched_serialized_and_held(self):
        result = mq.plan_queue([sub('A', owns=['x.py']), sub('B', owns=['y.py']),
                                 sub('C', owns=['x.py']), sub('D', owns=['z.py'], check_cmd=None)])
        text = mq.render(result)
        self.assertIn('BATCHED', text)
        self.assertIn('SERIALIZED', text)
        self.assertIn('HELD (1), never merged, never rejected:', text)
        self.assertIn('D', text)

    def test_demo_fixture_drives_all_three_verdicts_at_exit_zero(self):
        """The module's own --demo path, exercised as a library call so the
        suite stays fast: no subprocess needed to prove the CLI's fixture is
        wired to the same plan_queue() the tests above check directly."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mq.main(['--demo'])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn('BATCHED', out)
        self.assertIn('SERIALIZED', out)
        self.assertIn('HELD', out)

    def test_unreadable_submissions_file_is_no_data_exit_2(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code = mq.main(['--submissions', '/no/such/file-xyz.json'])
        self.assertEqual(code, 2)
        self.assertIn('NO-DATA', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
