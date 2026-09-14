"""What wbs.py must keep true, pinned after it reported a correctly decomposed
node as violating the 100 percent rule.

THE DEFECT THIS SUITE EXISTS FOR: hours() knew two spellings of the hours key
(effort_hours, estimate_hours) and the subtask checks read a third ('hours')
directly, bypassing it. So a parent written with one spelling and children
written with another summed to zero, and the tool blamed the node. The node was
correct and the reader was wrong, which is the worst way for a checker to fail:
it manufactures a violation and sends someone to fix work that was already fine.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbs as W  # noqa: E402


def _git(args, cwd):
    r = subprocess.run(['git'] + args, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def _init_repo(tmp):
    _git(['init', '-q'], tmp)
    _git(['-c', 'user.email=a@b.c', '-c', 'user.name=t', 'commit', '--allow-empty',
          '-q', '-m', 'root'], tmp)


class HoursReadsEverySpelling(unittest.TestCase):
    def test_all_three_keys_answer_the_same_question(self):
        for key in ('effort_hours', 'estimate_hours', 'hours'):
            self.assertEqual(W.hours({key: 7}), 7, key)

    def test_a_node_with_no_hours_at_all_is_zero_not_an_error(self):
        self.assertEqual(W.hours({}), 0)


class TheHundredPercentRule(unittest.TestCase):
    """The parent and its children may be written in DIFFERENT spellings, because
    two authors on two days did exactly that. The sum must still be recognised."""

    def _node(self, parent_key, child_key):
        return {'id': 'X', 'status': 'SCHEDULED', parent_key: 12,
                'subtasks': [{'id': 'X.%d' % i, child_key: 4,
                              'owns': ['a.py'], 'done_check': 'true'}
                             for i in range(3)]}

    def test_mixed_spellings_still_add_up(self):
        for pk in ('effort_hours', 'estimate_hours'):
            for ck in ('effort_hours', 'estimate_hours', 'hours'):
                problems = W.check_node(self._node(pk, ck))
                bad = [p for p in problems if '100 percent' in p]
                self.assertEqual(bad, [], 'parent=%s child=%s said: %s' % (pk, ck, bad))

    def test_a_node_whose_parts_really_do_NOT_add_up_still_fails(self):
        """Calibration. A check that cannot go red verifies nothing, so this
        proves the rule survived the fix rather than being disabled by it."""
        n = self._node('effort_hours', 'hours')
        n['subtasks'] = n['subtasks'][:1]          # 4h of children against a 12h parent
        problems = W.check_node(n)
        self.assertTrue([p for p in problems if '100 percent' in p], problems)

    def test_an_oversize_work_package_is_still_caught_in_every_spelling(self):
        for ck in ('effort_hours', 'estimate_hours', 'hours'):
            n = {'id': 'Y', 'status': 'SCHEDULED', 'effort_hours': 9,
                 'subtasks': [{'id': 'Y.1', ck: 9, 'owns': ['a.py'],
                               'done_check': 'true'}]}
            problems = W.check_node(n)
            self.assertTrue([p for p in problems if 'work package limit' in p],
                            '%s: %s' % (ck, problems))


class TheThirdVerdict(unittest.TestCase):
    """An honestly undecomposable node. G1-M4 ships fixes for whatever G1-M3 marks
    blocking, and G1-M3 has not run, so its packages cannot be written without
    inventing the findings of a test that never executed."""

    def test_a_recorded_refusal_exempts_the_node(self):
        n = {'id': 'Z', 'status': 'SCHEDULED', 'effort_hours': 120,
             'cannot_decompose_yet': 'the measurement it depends on has not run'}
        self.assertEqual(W.check_node(n), [])

    def test_the_SAME_node_without_a_reason_still_FAILS(self):
        """Calibration, and the point of the whole clause. The exemption is a
        recorded decision, never a key that quiets the checker: remove the reason
        and the node is just undecomposed again."""
        n = {'id': 'Z', 'status': 'SCHEDULED', 'effort_hours': 120}
        self.assertTrue([p for p in W.check_node(n) if 'DECOMPOSED' in p], W.check_node(n))

    def test_an_exempt_node_is_REPORTED_and_never_silent(self):
        """An exemption nobody sees is indistinguishable from a node nobody checked."""
        doc = {'rows': [{'id': 'Z', 'status': 'SCHEDULED', 'effort_hours': 120,
                         'cannot_decompose_yet': 'because'}], 'features': []}
        self.assertEqual([n['id'] for n in W.nodata_nodes(doc)], ['Z'])

    def test_a_DONE_or_SUPERSEDED_node_is_not_reported_as_pending_NO_DATA(self):
        for st in ('DONE', 'SUPERSEDED'):
            doc = {'rows': [{'id': 'Z', 'status': st, 'effort_hours': 0,
                             'cannot_decompose_yet': 'because'}], 'features': []}
            self.assertEqual(W.nodata_nodes(doc), [], st)


class ExtractPackagesReadsBothPlanSchemas(unittest.TestCase):
    """THE DEFECT THIS CLASS EXISTS FOR: --prove was designed against the
    roadmap's rows/features/subtasks shape, then pointed at a real scoped
    initiative plan (lanes[].work_packages[], flat, no subtasks nesting) and
    would have silently found zero packages -- a false clean bill, the exact
    failure class this checker exists to catch, self-inflicted."""

    def test_lanes_shape_is_read_flat_with_no_subtask_nesting(self):
        doc = {'status': 'X', 'lanes': [
            {'work_packages': [{'id': 'D1', 'status': 'CLOSED-2026'}]},
            {'work_packages': [{'id': 'E1', 'status': 'OPEN'}]}]}
        top, pkgs = W.extract_packages(doc)
        self.assertEqual(top, 'X')
        self.assertEqual([p['id'] for p in pkgs], ['D1', 'E1'])

    def test_roadmap_shape_still_reads_leaves_and_subtasks_as_before(self):
        doc = {'status': 'Y', 'rows': [
            {'id': 'R1', 'status': 'DONE'},  # leaf, no subtasks
            {'id': 'R2', 'status': 'SCHEDULED',
             'subtasks': [{'id': 'R2.1', 'status': 'CLOSED'}]}],
            'features': []}
        top, pkgs = W.extract_packages(doc)
        self.assertEqual(top, 'Y')
        self.assertEqual(sorted(p['id'] for p in pkgs), ['R1', 'R2.1'])


class ProveVerifiesTheRefNotTheWorkingTree(unittest.TestCase):
    """THE DEFECT THIS CLASS EXISTS FOR: a plan marked ten work packages
    CLOSED after a local session; three of the ten were dropped by a later
    same-branch cleanup commit before the branch was merged, and nothing
    re-checked the plan against what actually landed. This pins --prove
    catching exactly that shape: present locally, absent from the ref."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix='wbs-prove-test-')
        self.addCleanup(lambda: subprocess.run(
            ['rm', '-rf', self.repo], capture_output=True))
        _init_repo(self.repo)
        with open(os.path.join(self.repo, 'shipped.py'), 'w') as fh:
            fh.write('# real\n')
        _git(['add', 'shipped.py'], self.repo)
        _git(['-c', 'user.email=a@b.c', '-c', 'user.name=t', 'commit', '-q',
              '-m', 'ships one file'], self.repo)
        self.ref = _git(['rev-parse', 'HEAD'], self.repo).strip()

    def _plan(self, path, doc):
        import json
        with open(path, 'w') as fh:
            json.dump(doc, fh)

    def test_a_closed_package_whose_file_never_reached_the_ref_is_caught(self):
        plan = os.path.join(self.repo, 'plan.json')
        self._plan(plan, {'status': 'CLOSED', 'lanes': [{'work_packages': [
            {'id': 'B2', 'status': 'CLOSED-2026', 'owns': ['never_landed.py']}]}]})
        ok, problems = W.prove(plan, self.ref)
        self.assertFalse(ok)
        self.assertTrue(any('B2' in p and 'never_landed.py' in p for p in problems), problems)

    def test_a_closed_package_whose_file_really_is_in_the_ref_passes(self):
        plan = os.path.join(self.repo, 'plan.json')
        self._plan(plan, {'status': 'CLOSED', 'lanes': [{'work_packages': [
            {'id': 'D1', 'status': 'CLOSED-2026', 'owns': ['shipped.py']}]}]})
        ok, problems = W.prove(plan, self.ref)
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])

    def test_not_started_parent_over_closed_children_is_caught(self):
        plan = os.path.join(self.repo, 'plan.json')
        self._plan(plan, {'status': 'NOT-STARTED', 'lanes': [{'work_packages': [
            {'id': 'D1', 'status': 'CLOSED-2026', 'owns': ['shipped.py']}]}]})
        ok, problems = W.prove(plan, self.ref)
        self.assertFalse(ok)
        self.assertTrue(any('NOT-STARTED' in p for p in problems), problems)

    def test_a_bad_ref_is_no_data_never_a_silent_pass(self):
        plan = os.path.join(self.repo, 'plan.json')
        self._plan(plan, {'status': 'CLOSED', 'lanes': [{'work_packages': [
            {'id': 'D1', 'status': 'CLOSED-2026', 'owns': ['shipped.py']}]}]})
        ok, problems = W.prove(plan, 'not-a-real-ref-anywhere')
        self.assertFalse(ok)
        self.assertTrue(any('NO-DATA' in p for p in problems), problems)


if __name__ == '__main__':
    unittest.main()
