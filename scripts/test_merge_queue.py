"""Calibration for scripts/merge_queue.py.

TWO HALVES. The PLANNING half (below, unchanged since this file's first
version) is pure logic, no git repo, no subprocess: two submissions with
disjoint write sets land in ONE batch; two with overlapping write sets land
in separate, sequential batches; a submission whose check command was never
declared is HELD, appearing in neither a batch nor a rejected list.

THE EXECUTION half (ExecutionAgainstRealGit and below) actually drives the
row's own done_check against REAL git repositories, because the property
under test -- 'tested together' -- can only be proven by actually merging
two branches into one tree and showing a check run there sees BOTH sides,
never by asserting it about a plan. Every one of the three clauses is
driven BOTH ways: a positive case proving the behavior, and a companion
case that would fail on an implementation missing it (calling the same
primitive with the mechanism disabled -- unbatched, out of order, or with a
check double that cannot produce a result -- and showing the SAME check
then reads differently). Mirrors test_integrate.py's own fixture style
(temp git repos, real subprocess, no mocked git)."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import merge_queue as mq  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


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


# --------------------------------------------------------- real git fixtures --

def canon_repo(files=None):
    """A fresh canonical repository, one commit, real git."""
    d = tempfile.mkdtemp(prefix='mq-canon-')
    run = lambda *a: subprocess.run(['git'] + list(a), cwd=d,  # noqa: E731
                                    capture_output=True, text=True)
    run('init', '-q', '-b', 'main')
    run('config', 'user.email', 'a@b.c')
    run('config', 'user.name', 't')
    for name, body in (files or {'base.py': 'BASE = 1\n'}).items():
        with open(os.path.join(d, name), 'w', encoding='utf-8') as fh:
            fh.write(body)
    run('add', '-A')
    run('commit', '-q', '-m', 'R0')
    return d


def branch(repo, name, files, msg):
    """A submission's own branch: forked from whatever HEAD is right now,
    one commit, then back to the branch this call started on -- so canon's
    own tip is never left checked out onto a feature branch."""
    cur = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    subprocess.run(['git', 'checkout', '-qb', name], cwd=repo,
                   capture_output=True, check=True)
    for fname, body in files.items():
        with open(os.path.join(repo, fname), 'w', encoding='utf-8') as fh:
            fh.write(body)
    subprocess.run(['git', 'add', '-A'], cwd=repo, capture_output=True, check=True)
    subprocess.run(['git', 'commit', '-qm', msg], cwd=repo, capture_output=True, check=True)
    subprocess.run(['git', 'checkout', '-q', cur], cwd=repo, capture_output=True, check=True)


def tip(repo):
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


class ClauseOneBatchedAndTestedTogether(unittest.TestCase):
    """'Two submissions with disjoint write sets are batched into ONE
    speculative merge and tested together.' Proven by a check that can
    only pass if the SIBLING's file is ALSO present in the tree at test
    time -- true only when both were actually merged into one tree before
    either check ran."""

    def test_two_disjoint_submissions_see_each_others_files_when_tested(self):
        repo = canon_repo()
        branch(repo, 'feat/a', {'a.py': 'A = 1\n'}, 'A')
        branch(repo, 'feat/b', {'b.py': 'B = 1\n'}, 'B')
        subs = [
            {'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'],
             'check_cmd': ['test', '-f', 'b.py']},   # only true if B is ALSO in the tree
            {'id': 'B', 'branch': 'feat/b', 'owns': ['b.py'],
             'check_cmd': ['test', '-f', 'a.py']},   # only true if A is ALSO in the tree
        ]
        result = mq.execute_queue(repo, subs)
        self.assertEqual(sorted(s['id'] for s, _d, _c in result['merged']), ['A', 'B'])
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['held'], [])
        self.assertTrue(os.path.exists(os.path.join(repo, 'a.py')))
        self.assertTrue(os.path.exists(os.path.join(repo, 'b.py')))

    def test_the_disproving_half_the_same_check_fails_when_not_batched(self):
        """Without the together-mechanism, landing A alone (its sibling
        never merged in) must fail this exact check -- proof that the pass
        above came from batching, not from a check that would have passed
        on its own regardless."""
        repo = canon_repo()
        branch(repo, 'feat/a', {'a.py': 'A = 1\n'}, 'A')
        branch(repo, 'feat/b', {'b.py': 'B = 1\n'}, 'B')
        sub_a_alone = {'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'],
                       'check_cmd': ['test', '-f', 'b.py']}
        r0 = tip(repo)
        verdict, _detail, canonical = mq._land_one(repo, sub_a_alone)
        self.assertEqual(verdict, mq.REJECTED)
        self.assertEqual(tip(repo), r0, 'a rejected landing must be unwound')


class ClauseTwoSerializedAndRevalidatedAgainstTheNewTip(unittest.TestCase):
    """'Two with overlapping sets are serialized' -- one lands, then the
    next is re-validated against the NEW tip before it lands, mirroring
    test_integrate.py's own advancing-base scenario, driven through
    merge_queue's plan_queue + execute_queue rather than integrate_one
    directly."""

    def setUp(self):
        self.repo = canon_repo({'lib.py': 'GREETING = "hello"\n'})
        branch(self.repo, 'feat/a', {'lib.py': 'GREETING = "bonjour"\n'}, 'A')
        branch(self.repo, 'feat/b',
              {'b.py': 'import lib\nassert lib.GREETING == "hello"\n'}, 'B')
        # A and B both declare lib.py: OVERLAPPING, so plan_queue serializes
        # them into two separate batches rather than one speculative merge.
        self.subs = [
            {'id': 'A', 'branch': 'feat/a', 'owns': ['lib.py'],
             'check_cmd': ['grep', '-q', 'bonjour', 'lib.py']},
            {'id': 'B', 'branch': 'feat/b', 'owns': ['lib.py'],
             'check_cmd': ['python3', 'b.py']},
        ]

    def test_A_lands_then_B_is_caught_against_As_new_tip(self):
        plan = mq.plan_queue(self.subs)
        self.assertEqual(len(plan['batches']), 2, 'overlap must force two batches')
        r0 = tip(self.repo)
        result = mq.execute_queue(self.repo, self.subs)
        self.assertEqual([s['id'] for s, _d, _c in result['merged']], ['A'])
        self.assertEqual([s['id'] for s, _d in result['rejected']], ['B'])
        self.assertNotEqual(tip(self.repo), r0)
        with open(os.path.join(self.repo, 'lib.py'), encoding='utf-8') as fh:
            self.assertIn('bonjour', fh.read())

    def test_the_disproving_half_B_would_have_landed_cleanly_gone_first(self):
        """B is a perfectly good change against the ORIGINAL tip. Its
        rejection above is proof the queue re-validated it against A's NEW
        tip, never a defect in B, exactly the order-dependent point
        test_integrate.py's TheAdvancingBaseScenario makes for
        integrate_one directly."""
        verdict, _detail, _canonical = mq._land_one(self.repo, self.subs[1])
        self.assertEqual(verdict, mq.MERGED)


class ClauseThreeHeldIsNeitherMergedNorRejected(unittest.TestCase):
    """'A submission whose checks could not run is HELD rather than merged
    or rejected.' Driven both ways: a check that genuinely could not run
    (missing executable, timeout) reads HELD, never REJECTED (which would
    mean it ran and disproved the change -- a different fact); and a
    sibling with a real, checkable failure in the SAME run still reads
    REJECTED, proving the two verdicts do not collapse into each other."""

    def test_a_check_naming_no_such_executable_is_held_not_rejected(self):
        sub_a = {'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'],
                 'check_cmd': ['no-such-executable-xyz']}
        status, detail = mq._run_submission_check(sub_a, tempfile.gettempdir())
        self.assertEqual(status, 'held')
        self.assertIn('could not run', detail)

    def test_a_check_that_times_out_is_held_not_rejected(self):
        sub_a = {'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'],
                 'check_cmd': ['sleep', '5']}
        status, detail = mq._run_submission_check(
            sub_a, tempfile.gettempdir(), timeout=0.05)
        self.assertEqual(status, 'held')
        self.assertIn('timed out', detail)

    def test_a_check_that_runs_and_fails_is_rejected_not_held(self):
        """The disproving half: a REAL failure must never read as HELD --
        collapsing the two is exactly what would let an unprovable change
        merge, or a real defect hide as 'could not verify'."""
        sub_a = {'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'],
                 'check_cmd': ['false']}
        status, _detail = mq._run_submission_check(sub_a, tempfile.gettempdir())
        self.assertEqual(status, 'fail')

    def test_a_speculative_batch_member_that_cannot_run_is_held_while_its_disjoint_sibling_still_proves(self):
        repo = canon_repo()
        branch(repo, 'feat/a', {'a.py': 'A = 1\n'}, 'A')
        branch(repo, 'feat/b', {'b.py': 'B = 1\n'}, 'B')
        sub_a = {'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'], 'check_cmd': ['true']}
        sub_b = {'id': 'B', 'branch': 'feat/b', 'owns': ['b.py'],
                 'check_cmd': ['no-such-executable-xyz']}
        proven, verdicts = mq._prove_batch_together(repo, [sub_a, sub_b])
        self.assertEqual([s['id'] for s in proven], ['A'])
        self.assertEqual(verdicts['B'][0], mq.HELD)

    def test_execute_queue_never_places_a_structurally_held_submission_in_merged_or_rejected(self):
        repo = canon_repo()
        branch(repo, 'feat/a', {'a.py': 'A = 1\n'}, 'A')
        subs = [{'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'], 'check_cmd': None}]
        result = mq.execute_queue(repo, subs)
        self.assertEqual(result['merged'], [])
        self.assertEqual(result['rejected'], [])
        self.assertEqual([s['id'] for s, _d in result['held']], ['A'])

    def test_a_singleton_submission_whose_check_genuinely_fails_is_rejected_and_unwound(self):
        """Companion to the HELD tests above, run through the full
        execute_queue path: a real failure must land in 'rejected', not
        'held', and canonical must be unwound exactly as integrate.py's
        own NEEDS_REPAIR path already proves for a single unit."""
        repo = canon_repo()
        branch(repo, 'feat/a', {'a.py': 'A = 1\n'}, 'A')
        subs = [{'id': 'A', 'branch': 'feat/a', 'owns': ['a.py'],
                 'check_cmd': ['test', '-f', 'nope-this-file-does-not-exist.py']}]
        r0 = tip(repo)
        result = mq.execute_queue(repo, subs)
        self.assertEqual(result['merged'], [])
        self.assertEqual([s['id'] for s, _d in result['rejected']], ['A'])
        self.assertEqual(result['held'], [])
        self.assertEqual(tip(repo), r0)


class ReceiptsBindToARevision(unittest.TestCase):
    """The row's third named advantage: 'receipts that BIND to a revision,
    so the integrator can trust a bound receipt instead of re-running
    everything' -- and never accepted blind once canonical has moved."""

    def test_a_receipt_bound_to_the_current_tip_is_valid(self):
        sub = {'id': 'A', 'receipt': {'canonical_rev': 'deadbeef', 'exit_code': 0}}
        self.assertTrue(mq.receipt_still_valid(sub, 'deadbeef'))

    def test_a_receipt_bound_to_a_moved_tip_is_stale_never_accepted_blind(self):
        sub = {'id': 'A', 'receipt': {'canonical_rev': 'deadbeef', 'exit_code': 0}}
        self.assertFalse(mq.receipt_still_valid(sub, 'a-different-tip'))

    def test_a_receipt_with_a_nonzero_exit_code_is_invalid_at_any_revision(self):
        sub = {'id': 'A', 'receipt': {'canonical_rev': 'deadbeef', 'exit_code': 1}}
        self.assertFalse(mq.receipt_still_valid(sub, 'deadbeef'))

    def test_no_receipt_at_all_is_invalid(self):
        self.assertFalse(mq.receipt_still_valid({'id': 'A'}, 'deadbeef'))


if __name__ == '__main__':
    unittest.main()
