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
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbs as W  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
