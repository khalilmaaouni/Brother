"""Calibration for scripts/fable_authority.py, in both directions.

The property this file exists to assert is not that Fable can act while the
founder is away. It is that the RIGHT class of decision gets recorded and the
RIGHT class gets refused: an AMBER decision always carries its overrule
sentence or is not written at all, and a RED decision is never acted on, only
queued.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import fable_authority as fa  # noqa: E402

T = lambda s: datetime.datetime.fromisoformat(s)  # noqa: E731
NOW = T('2026-08-30T12:00:00+00:00')


class Classify(unittest.TestCase):
    def test_a_plain_reversible_edit_is_green(self):
        label, reason = fa.classify('rename a local variable for clarity')
        self.assertEqual(label, fa.GREEN)
        self.assertIn('GREEN', reason)

    def test_a_wide_but_reversible_change_is_amber(self):
        label, reason = fa.classify('restructure the module layout across the repo')
        self.assertEqual(label, fa.AMBER)

    def test_deleting_something_is_red(self):
        """The exact example the roadmap's done_check names."""
        label, reason = fa.classify('delete the remote branch')
        self.assertEqual(label, fa.RED)
        self.assertIn('deletes data', reason)

    def test_every_named_red_member_is_refused(self):
        """irreversible, credentials, spend ceiling, public publish, deletion,
        purchase, changing the laws: the RED list from the roadmap, driven for
        each member."""
        examples = [
            'take an irreversible action on the shared branch',
            'read the api key from the vault and paste it here',
            'raise the spend ceiling for tonight',
            'publish this repository publicly',
            'purge the old records',
            'buy a new subscription for the team',
            'amend the rule in claude.md',
        ]
        for text in examples:
            label, _ = fa.classify(text)
            self.assertEqual(label, fa.RED, '%r should classify RED' % text)

    def test_the_estates_own_publication_verb_is_red(self):
        """Found 2026-09-01 by a recon that ran this classifier rather than reading
        it: 'export to the public repository' returned GREEN, because the red list
        carried 'publish' and not 'export'. Export is the ONLY route from the private
        hub to the public repository on this estate, so that sentence describes the
        single most consequential act the classifier is meant to catch. Driven both
        ways here: the publication verbs must be RED, and an ordinary local export
        must stay GREEN, or the fix would have bought safety with a false alarm on
        every report anyone writes."""
        for text in ['export to the public repository',
                     'run scripts/export_public.py --push',
                     'push this to the public repo']:
            label, reason = fa.classify(text)
            self.assertEqual(label, fa.RED, '%r should classify RED' % text)
            self.assertIn('public surface', reason)
        for text in ['export the csv report to a local file',
                     'refactor the parser']:
            label, _ = fa.classify(text)
            self.assertEqual(label, fa.GREEN,
                             '%r must stay GREEN: a local export is not a publication' % text)

    def test_acceptance_is_never_delegable(self):
        """Row E49: the north star chain's HUMAN DECISION node is unconditional,
        so an agent under delegation classifying its own next move must read RED
        for every phrasing that actually accepts something, one per keyword."""
        examples = [
            'accept the delivery on the founder\'s behalf',
            'accept this delivery for him',
            'the agent accepts the delivery under the delegation',
            'record the acceptance of the toy delivery',
            'the delivery was accepted by the session',
            'sign off on the release for the founder',
            'the agent signs off on the change',
        ]
        for text in examples:
            label, reason = fa.classify(text)
            self.assertEqual(label, fa.RED, '%r should classify RED' % text)
            self.assertIn('human decision node', reason)

    def test_acceptance_signals_do_not_catch_an_ordinary_sentence(self):
        """Negative control: the matcher is substring (kw in text), so a bare
        'accept' was deliberately not added to RED_SIGNALS, because it is a
        substring of both these ordinary sentences and neither one is an
        acceptance decision."""
        for text in ['that is an acceptable risk to take',
                     'accept the risk of a retry and move on']:
            label, _ = fa.classify(text)
            self.assertEqual(label, fa.GREEN,
                             '%r must stay GREEN: not an acceptance decision' % text)

    def test_an_unrecognized_label_is_no_data_never_a_silent_green(self):
        """classify() itself only ever returns one of the three, but the CLI
        dispatch must not silently fall through to GREEN if it ever did not.
        Driven at the CLI layer with classify monkeypatched to misbehave."""
        saved = fa.classify
        try:
            fa.classify = lambda decision: ('PURPLE', 'not a real class')
            code = fa.main(['--classify', 'anything'])
            self.assertEqual(code, 2)
        finally:
            fa.classify = saved


class Absence(unittest.TestCase):
    def test_a_quiet_founder_with_nothing_blocked_is_not_absence(self):
        absent, _ = fa.check_absence(T('2026-08-29T00:00:00+00:00'), 24, False, now=NOW)
        self.assertFalse(absent)

    def test_a_blocked_decision_with_a_present_founder_is_not_absence(self):
        absent, _ = fa.check_absence(T('2026-08-30T11:00:00+00:00'), 24, True, now=NOW)
        self.assertFalse(absent)

    def test_both_together_is_absence(self):
        absent, reason = fa.check_absence(T('2026-08-29T00:00:00+00:00'), 24, True, now=NOW)
        self.assertTrue(absent)
        self.assertIn('blocking', reason)

    def test_neither_is_not_absence(self):
        absent, _ = fa.check_absence(T('2026-08-30T11:00:00+00:00'), 24, False, now=NOW)
        self.assertFalse(absent)


class RecordAmber(unittest.TestCase):
    def test_refused_without_an_overrule_sentence(self):
        entry = fa.record_amber('restructure the layout', 'AMBER: wide', '',
                                 overrule=None, path=os.path.join(tempfile.mkdtemp(), 'a.jsonl'))
        self.assertIsNone(entry)

    def test_refused_with_a_blank_overrule_sentence(self):
        entry = fa.record_amber('restructure the layout', 'AMBER: wide', '',
                                 overrule='   ', path=os.path.join(tempfile.mkdtemp(), 'a.jsonl'))
        self.assertIsNone(entry)

    def test_written_with_an_overrule_sentence_carries_it(self):
        path = os.path.join(tempfile.mkdtemp(), 'a.jsonl')
        entry = fa.record_amber('restructure the layout', 'AMBER: wide', 'a day of rework',
                                 overrule='Revert the restructure', path=path)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['status'], 'PROVISIONAL-FABLE')
        self.assertEqual(entry['overrule_sentence'], 'Revert the restructure')
        with open(path, encoding='utf-8') as fh:
            line = json.loads(fh.readline())
        self.assertEqual(line['overrule_sentence'], 'Revert the restructure')


class QueueRed(unittest.TestCase):
    def test_a_red_decision_is_queued_never_acted_on(self):
        path = os.path.join(tempfile.mkdtemp(), 'r.jsonl')
        entry = fa.queue_red('delete the remote branch', 'RED: deletes data', path=path)
        self.assertEqual(entry['status'], 'AWAITING FOUNDER')
        with open(path, encoding='utf-8') as fh:
            line = json.loads(fh.readline())
        self.assertEqual(line['decision'], 'delete the remote branch')


class Decide(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.amber_path = os.path.join(d, 'amber.jsonl')
        self.red_path = os.path.join(d, 'red.jsonl')

    def test_green_proceeds_with_no_record(self):
        label, entry, _ = fa.decide('rename a local variable', amber_path=self.amber_path,
                                     red_path=self.red_path)
        self.assertEqual(label, fa.GREEN)
        self.assertIsNone(entry)
        self.assertFalse(os.path.exists(self.amber_path))
        self.assertFalse(os.path.exists(self.red_path))

    def test_amber_without_overrule_is_classified_but_not_recorded(self):
        label, entry, _ = fa.decide('restructure the layout', amber_path=self.amber_path,
                                     red_path=self.red_path)
        self.assertEqual(label, fa.AMBER)
        self.assertIsNone(entry)
        self.assertFalse(os.path.exists(self.amber_path))

    def test_amber_with_overrule_produces_a_provisional_fable_record(self):
        """Load-bearing: the done_check this row exists for."""
        label, entry, _ = fa.decide('restructure the layout',
                                     overrule='Revert the restructure',
                                     amber_path=self.amber_path, red_path=self.red_path)
        self.assertEqual(label, fa.AMBER)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['status'], 'PROVISIONAL-FABLE')
        self.assertEqual(entry['overrule_sentence'], 'Revert the restructure')

    def test_red_is_refused_and_queued_regardless_of_overrule(self):
        """Load-bearing: the other half of the done_check. An overrule
        sentence never buys a RED decision an exemption."""
        label, entry, _ = fa.decide('delete the remote branch',
                                     overrule='this should not matter',
                                     amber_path=self.amber_path, red_path=self.red_path)
        self.assertEqual(label, fa.RED)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['status'], 'AWAITING FOUNDER')
        self.assertFalse(os.path.exists(self.amber_path), 'RED must never write the amber log')


class CliExitCodes(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.amber_path = os.path.join(d, 'amber.jsonl')
        self.red_path = os.path.join(d, 'red.jsonl')

    def test_classify_green_exits_zero(self):
        code = fa.main(['--classify', 'rename a local variable',
                         '--amber-log', self.amber_path, '--red-queue', self.red_path])
        self.assertEqual(code, 0)

    def test_classify_amber_exits_zero_and_does_not_record(self):
        code = fa.main(['--classify', 'restructure the layout',
                         '--amber-log', self.amber_path, '--red-queue', self.red_path])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.amber_path))

    def test_classify_red_exits_one_and_queues(self):
        code = fa.main(['--classify', 'delete the remote branch',
                         '--amber-log', self.amber_path, '--red-queue', self.red_path])
        self.assertEqual(code, 1)
        self.assertTrue(os.path.exists(self.red_path))

    def test_record_amber_without_overrule_exits_one(self):
        code = fa.main(['--record-amber', 'restructure the layout',
                         '--amber-log', self.amber_path])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.amber_path))

    def test_record_amber_with_overrule_exits_zero(self):
        code = fa.main(['--record-amber', 'restructure the layout',
                         '--overrule', 'Revert the restructure',
                         '--amber-log', self.amber_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.amber_path))

    def test_record_amber_on_a_green_decision_is_refused(self):
        code = fa.main(['--record-amber', 'rename a local variable',
                         '--overrule', 'undo the rename',
                         '--amber-log', self.amber_path])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.amber_path))

    def test_record_amber_on_a_red_decision_is_refused(self):
        code = fa.main(['--record-amber', 'delete the remote branch',
                         '--overrule', 'this should not matter',
                         '--amber-log', self.amber_path])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.amber_path))

    def test_check_absence_present_founder_exits_zero(self):
        code = fa.main(['--check-absence', '--last-message', '2026-08-30T11:55:00Z',
                         '--window-hours', '24'])
        self.assertEqual(code, 0)

    def test_check_absence_absent_exits_one(self):
        code = fa.main(['--check-absence', '--last-message', '2020-01-01T00:00:00Z',
                         '--window-hours', '24', '--blocked'])
        self.assertEqual(code, 1)

    def test_check_absence_unparseable_timestamp_is_no_data(self):
        code = fa.main(['--check-absence', '--last-message', 'not a date', '--blocked'])
        self.assertEqual(code, 2)

    def test_check_absence_missing_last_message_is_no_data(self):
        code = fa.main(['--check-absence'])
        self.assertEqual(code, 2)

    def test_no_flags_at_all_is_no_data(self):
        self.assertEqual(fa.main([]), 2)

    def test_selftest_exits_zero(self):
        self.assertEqual(fa.main(['--selftest']), 0)


if __name__ == '__main__':
    unittest.main()
