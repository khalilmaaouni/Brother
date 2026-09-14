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
import fence_expiry  # noqa: E402
import subprocess  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

T = lambda s: datetime.datetime.fromisoformat(s)  # noqa: E731
NOW = T('2026-08-30T12:00:00+00:00')


class Classify(unittest.TestCase):
    def test_unmatched_text_now_classifies_amber_not_green(self):
        """Fixed 2026-09-10: classify()'s no-match fallback was GREEN, so a
        decision matching neither list proceeded with no record at all. A
        plain, genuinely low-stakes edit like this one is exactly the text
        that used to slip through as GREEN; it must now read AMBER, the
        recorded-but-still-actionable class, never GREEN, never RED."""
        label, reason = fa.classify('rename a local variable for clarity')
        self.assertEqual(label, fa.AMBER)
        self.assertIn('AMBER', reason)
        self.assertIn('no RED or AMBER signal matched', reason)

    def test_a_red_worthy_decision_that_misses_every_keyword_is_now_amber_not_green(self):
        """The actual gap this row exists to close: a decision that IS the
        kind RED_SIGNALS is meant to catch (an irreversible, destructive
        action on shared data) but whose phrasing happens to miss every
        keyword in both RED_SIGNALS and AMBER_SIGNALS. Before this fix it
        classified GREEN and proceeded with no record whatsoever. It must
        now be recorded as AMBER rather than vanish silently; RED_SIGNALS
        itself is deliberately left untouched by this fix, so closing this
        gap for real (making this phrasing RED) is separate, future scope."""
        text = 'wipe the customer records clean and start fresh'
        # Sanity: prove this text really matches neither list, or the test
        # would not be exercising the no-match fallback at all.
        for kw, _ in fa.RED_SIGNALS + fa.AMBER_SIGNALS:
            self.assertNotIn(kw, text.lower(), '%r unexpectedly matches %r' % (text, kw))
        label, reason = fa.classify(text)
        self.assertEqual(label, fa.AMBER)
        self.assertIn('no RED or AMBER signal matched', reason)

    def test_red_and_amber_signal_matches_are_unaffected_by_the_default_flip(self):
        """Disproving case for the fix: only the no-match fallback moved.
        Text that actually matches a RED or AMBER signal must classify
        exactly as it did before, unaffected by the default change."""
        red_label, red_reason = fa.classify('delete the remote branch')
        self.assertEqual(red_label, fa.RED)
        self.assertIn('deletes data', red_reason)
        amber_label, amber_reason = fa.classify('restructure the module layout across the repo')
        self.assertEqual(amber_label, fa.AMBER)
        self.assertIn('restructures shared layout', amber_reason)

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
        must stay OUT OF RED, or the fix would have bought safety with a false
        alarm on every report anyone writes. (2026-09-10: the no-match default
        moved from GREEN to AMBER, so the local-export side now reads AMBER,
        not GREEN; it must still never read RED.)"""
        for text in ['export to the public repository',
                     'run scripts/export_public.py --push',
                     'push this to the public repo']:
            label, reason = fa.classify(text)
            self.assertEqual(label, fa.RED, '%r should classify RED' % text)
            self.assertIn('public surface', reason)
        for text in ['export the csv report to a local file',
                     'refactor the parser']:
            label, _ = fa.classify(text)
            self.assertEqual(label, fa.AMBER,
                             '%r must not be RED: a local export is not a publication '
                             '(and now defaults AMBER, not GREEN)' % text)

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
        acceptance decision. (2026-09-10: the no-match default moved from
        GREEN to AMBER, so these now read AMBER; the property under test is
        unchanged, that they must never read RED.)"""
        for text in ['that is an acceptable risk to take',
                     'accept the risk of a retry and move on']:
            label, _ = fa.classify(text)
            self.assertEqual(label, fa.AMBER,
                             '%r must not be RED: not an acceptance decision '
                             '(and now defaults AMBER, not GREEN)' % text)

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


class RecordRuling(unittest.TestCase):
    """WBS-10.03 (docs/decisions/ruling-not-stall-2026-09-13.json). A ruling
    is admitted only when all eight fields are present, autonomy_dial
    classifies the observables A0, and check_passed_before is exactly
    False (the check discriminated: red before, green after)."""

    def _fields(self):
        return dict(
            question='rename the local helper for clarity?',
            choice='rename it',
            reason='pure rename, no behavior change',
            evidence='grep shows one call site, updated in the same diff',
            cost_if_wrong='a five-minute revert',
            deciding_check='python3 scripts/test_x.py',
            reversibility='minutes',
            human_override_path='Revert the rename',
        )

    def _observables_a0(self):
        return dict(single_file_or_named_target=True, contract_change='none',
                    crosses_boundary=False, reversible_under_hour=True)

    def test_written_when_a0_and_check_discriminated(self):
        path = os.path.join(tempfile.mkdtemp(), 'ruling.jsonl')
        entry = fa.record_ruling(observables=self._observables_a0(),
                                  check_passed_before=False, path=path,
                                  **self._fields())
        self.assertIsNotNone(entry)
        self.assertEqual(entry['status'], 'RULED-FABLE')
        self.assertEqual(entry['human_override_path'], 'Revert the rename')
        with open(path, encoding='utf-8') as fh:
            line = json.loads(fh.readline())
        self.assertEqual(line['question'], self._fields()['question'])

    def test_refused_when_a_required_field_is_missing(self):
        for missing in self._fields():
            fields = self._fields()
            fields[missing] = ''
            entry = fa.record_ruling(observables=self._observables_a0(),
                                      check_passed_before=False,
                                      path=os.path.join(tempfile.mkdtemp(), 'r.jsonl'),
                                      **fields)
            self.assertIsNone(entry, 'missing %r should refuse' % missing)

    def test_refused_when_classify_is_not_a0(self):
        """An observables dict naming nothing lands A2 per autonomy_dial's
        own contract, never A0."""
        entry = fa.record_ruling(observables={}, check_passed_before=False,
                                  path=os.path.join(tempfile.mkdtemp(), 'r.jsonl'),
                                  **self._fields())
        self.assertIsNone(entry)

    def test_refused_when_check_passed_before_true(self):
        """The check already passed before the work began, so it could not
        have caught a wrong choice: not a ruling."""
        entry = fa.record_ruling(observables=self._observables_a0(),
                                  check_passed_before=True,
                                  path=os.path.join(tempfile.mkdtemp(), 'r.jsonl'),
                                  **self._fields())
        self.assertIsNone(entry)

    def test_refused_when_check_passed_before_none(self):
        """NO-DATA: nothing could be measured, so it is never admitted."""
        entry = fa.record_ruling(observables=self._observables_a0(),
                                  check_passed_before=None,
                                  path=os.path.join(tempfile.mkdtemp(), 'r.jsonl'),
                                  **self._fields())
        self.assertIsNone(entry)

    def test_defaults_to_ruling_log_when_no_path_given(self):
        """RS-5: the session-scoped default, same seam as record_amber's
        own default-when-no-path-given."""
        saved = fa.RULING_LOG
        d = tempfile.mkdtemp()
        try:
            fa.RULING_LOG = os.path.join(d, 'ruling-records.jsonl')
            entry = fa.record_ruling(observables=self._observables_a0(),
                                      check_passed_before=False,
                                      **self._fields())
            self.assertIsNotNone(entry)
            self.assertTrue(os.path.isfile(fa.RULING_LOG))
        finally:
            fa.RULING_LOG = saved


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
        """decide()'s GREEN branch is no longer reachable through classify()
        for real text (2026-09-10: the no-match default moved to AMBER, and
        there is still no GREEN_SIGNALS list), but the branch itself stays
        live production code for the day a real positive GREEN signal is
        added, so it is driven here with classify() forced to GREEN."""
        saved = fa.classify
        try:
            fa.classify = lambda decision: (fa.GREEN, 'forced GREEN for this test')
            label, entry, _ = fa.decide('rename a local variable', amber_path=self.amber_path,
                                         red_path=self.red_path)
        finally:
            fa.classify = saved
        self.assertEqual(label, fa.GREEN)
        self.assertIsNone(entry)
        self.assertFalse(os.path.exists(self.amber_path))
        self.assertFalse(os.path.exists(self.red_path))

    def test_unmatched_decision_now_classifies_amber_via_decide_too(self):
        """The real path: 'rename a local variable' matches neither list,
        so decide() (with the real classify(), not a forced one) now
        returns AMBER for it, and still writes no record without an
        overrule, exactly like any other AMBER decision."""
        label, entry, _ = fa.decide('rename a local variable', amber_path=self.amber_path,
                                     red_path=self.red_path)
        self.assertEqual(label, fa.AMBER)
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
        """classify() itself no longer returns GREEN for anything (no
        GREEN_SIGNALS list exists, and the no-match default moved to AMBER
        2026-09-10), so this exercises --record-amber's own refusal of a
        non-AMBER label the only way left to reach it: force GREEN out of
        classify(), the same monkeypatch style as the NO-DATA test below."""
        saved = fa.classify
        try:
            fa.classify = lambda decision: (fa.GREEN, 'forced GREEN for this test')
            code = fa.main(['--record-amber', 'rename a local variable',
                             '--overrule', 'undo the rename',
                             '--amber-log', self.amber_path])
        finally:
            fa.classify = saved
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


class DelegationFor(unittest.TestCase):
    """P3b (design-P3.md section 3, steering 9.7): authority is separate
    from readiness. delegation_for is the one reader; grant_merge (through
    the CLI only, per 9.7) is the one writer."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, 'delegations.jsonl')

    def _write_raw(self, entry):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry) + '\n')

    def test_no_file_means_none(self):
        self.assertIsNone(
            fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path))

    def test_a_matching_live_grant_returns_the_dict(self):
        ok, entry = fa.grant_merge(
            'org/repo', 'main', 'merge', 'A2', '2026-09-08T00:00:00Z', 'squash',
            'Khalil Maaouni', 'go ahead, squash merge tonight only', now=NOW,
            path=self.path)
        self.assertTrue(ok, entry)
        found = fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path)
        self.assertIsNotNone(found)
        self.assertEqual(found['repository'], 'org/repo')
        self.assertEqual(found['risk_ceiling'], 'A2')
        self.assertEqual(found['granted_by'], 'Khalil Maaouni')

    def test_wrong_repository_base_or_action_returns_none(self):
        fa.grant_merge('org/repo', 'main', 'merge', 'A2', '2026-09-08T00:00:00Z',
                        'squash', 'Khalil Maaouni', 'go ahead', now=NOW, path=self.path)
        self.assertIsNone(fa.delegation_for('org/other', 'main', 'merge', now=NOW, path=self.path))
        self.assertIsNone(fa.delegation_for('org/repo', 'develop', 'merge', now=NOW, path=self.path))
        self.assertIsNone(fa.delegation_for('org/repo', 'main', 'release', now=NOW, path=self.path))

    def test_expired_returns_none(self):
        # Written directly: grant_merge itself refuses a past `until` (a
        # human cannot grant a lapsed window), so an expired grant on disk
        # only ever arrives by outliving the moment it was queried, exactly
        # like fence_expiry's own claims.
        self._write_raw({
            'repository': 'org/repo', 'base': 'main', 'action': 'merge',
            'risk_ceiling': 'A2', 'until': '2026-08-01T00:00:00Z',
            'merge_method': 'squash', 'granted_by': 'Khalil Maaouni',
            'words': 'go ahead', 'granted_at': '2026-07-30T00:00:00Z'})
        self.assertIsNone(fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path))

    def test_missing_until_returns_none(self):
        self._write_raw({
            'repository': 'org/repo', 'base': 'main', 'action': 'merge',
            'risk_ceiling': 'A2', 'until': None,
            'merge_method': 'squash', 'granted_by': 'Khalil Maaouni',
            'words': 'go ahead', 'granted_at': '2026-07-30T00:00:00Z'})
        self.assertIsNone(fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path))

    def test_a_second_grant_for_a_different_base_does_not_leak(self):
        fa.grant_merge('org/repo', 'main', 'merge', 'A2', '2026-09-08T00:00:00Z',
                        'squash', 'Khalil Maaouni', 'main only', now=NOW, path=self.path)
        fa.grant_merge('org/repo', 'develop', 'merge', 'A1', '2026-09-08T00:00:00Z',
                        'merge', 'Khalil Maaouni', 'develop only', now=NOW, path=self.path)
        found = fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path)
        self.assertEqual(found['base'], 'main')
        self.assertEqual(found['words'], 'main only')

    def test_last_matching_line_wins(self):
        fa.grant_merge('org/repo', 'main', 'merge', 'A1', '2026-09-08T00:00:00Z',
                        'squash', 'Khalil Maaouni', 'first grant', now=NOW, path=self.path)
        fa.grant_merge('org/repo', 'main', 'merge', 'A2', '2026-09-09T00:00:00Z',
                        'squash', 'Khalil Maaouni', 'second grant supersedes', now=NOW,
                        path=self.path)
        found = fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path)
        self.assertEqual(found['words'], 'second grant supersedes')
        self.assertEqual(found['risk_ceiling'], 'A2')


class GrantMergeCli(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, 'delegations.jsonl')

    def _argv(self, until):
        return ['grant-merge', '--repository', 'org/repo', '--base', 'main',
                '--action', 'merge', '--risk-ceiling', 'A2', '--until', until,
                '--merge-method', 'squash', '--granted-by', 'Khalil Maaouni',
                '--words', 'squash merge tonight only, one shot',
                '--delegations-log', self.path]

    def test_refuses_a_past_until(self):
        code = fa.main(self._argv('2020-01-01T00:00:00Z'))
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.path))

    def test_writes_a_line_for_a_future_until(self):
        code = fa.main(self._argv('2099-01-01T00:00:00Z'))
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.path))
        with open(self.path, encoding='utf-8') as fh:
            line = json.loads(fh.readline())
        self.assertEqual(line['repository'], 'org/repo')
        self.assertEqual(line['merge_method'], 'squash')
        self.assertEqual(line['granted_by'], 'Khalil Maaouni')


class RevokeMerge(unittest.TestCase):
    """F4: grant_merge refuses any `until` at or before now, so the only
    writer of delegations.jsonl could never itself end a live grant
    early. revoke_merge/`revoke-merge` is the second, deliberate writer."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, 'delegations.jsonl')

    def test_a_live_grant_is_revoked_and_delegation_for_reads_none(self):
        ok, entry = fa.grant_merge('org/repo', 'main', 'merge', 'A2', '2099-01-01T00:00:00Z',
                                    'squash', 'Khalil Maaouni', 'land it tonight', now=NOW,
                                    path=self.path)
        self.assertTrue(ok, entry)
        self.assertIsNotNone(fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path))

        ok, revoked = fa.revoke_merge('org/repo', 'main', 'merge', 'Khalil Maaouni',
                                       'cancel that grant', now=NOW, path=self.path)
        self.assertTrue(ok, revoked)
        self.assertIsNone(fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path))

    def test_revoke_before_any_grant_still_writes_and_finds_nothing_to_undo(self):
        ok, revoked = fa.revoke_merge('org/repo', 'main', 'merge', 'Khalil Maaouni',
                                       'nothing to cancel yet', now=NOW, path=self.path)
        self.assertTrue(ok, revoked)
        self.assertIsNone(fa.delegation_for('org/repo', 'main', 'merge', now=NOW, path=self.path))

    def test_revoke_missing_a_required_field_is_refused(self):
        ok, reason = fa.revoke_merge('', 'main', 'merge', 'Khalil Maaouni', 'cancel', path=self.path)
        self.assertFalse(ok)
        self.assertIn('repository', reason)


class RevokeMergeCli(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, 'delegations.jsonl')

    def test_revoke_merge_cli_writes_and_ends_the_live_grant(self):
        code = fa.main(['grant-merge', '--repository', 'org/repo', '--base', 'main',
                         '--action', 'merge', '--risk-ceiling', 'A2', '--until',
                         '2099-01-01T00:00:00Z', '--merge-method', 'squash',
                         '--granted-by', 'Khalil Maaouni', '--words', 'land it tonight',
                         '--delegations-log', self.path])
        self.assertEqual(code, 0)
        self.assertIsNotNone(fa.delegation_for('org/repo', 'main', 'merge', path=self.path))

        code = fa.main(['revoke-merge', '--repository', 'org/repo', '--base', 'main',
                         '--action', 'merge', '--granted-by', 'Khalil Maaouni',
                         '--words', 'cancel that grant', '--delegations-log', self.path])
        self.assertEqual(code, 0)
        self.assertIsNone(fa.delegation_for('org/repo', 'main', 'merge', path=self.path))


class RecordDirIsWorktreeAware(unittest.TestCase):
    """F6: fable_authority's own logs must resolve through the SAME
    checkout fence_expiry's registry already resolves through, or a
    grant written by a human in one worktree is invisible to
    land_queue.resolve_authority running in another."""

    def _git(self, args, cwd=None):
        proc = subprocess.run(['git'] + args, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_record_dir_of_a_linked_worktree_resolves_to_the_primary_checkouts_sbe(self):
        d = tempfile.mkdtemp()
        primary = os.path.join(d, 'primary')
        self._git(['init', '-q', primary])
        self._git(['config', 'user.email', 't@t'], primary)
        self._git(['config', 'user.name', 't'], primary)
        with open(os.path.join(primary, 'f.txt'), 'w', encoding='utf-8') as fh:
            fh.write('x\n')
        self._git(['add', 'f.txt'], primary)
        self._git(['commit', '-q', '-m', 'base'], primary)
        os.makedirs(os.path.join(primary, '.sbe'), exist_ok=True)
        with open(os.path.join(primary, '.sbe', 'tasks.json'), 'w', encoding='utf-8') as fh:
            fh.write('{}')

        linked = os.path.join(d, 'linked')
        self._git(['worktree', 'add', '-q', linked, '-b', 'linked-branch'], primary)
        self.assertFalse(os.path.isdir(os.path.join(linked, '.sbe')),
                          'the linked worktree must genuinely have no .sbe of its own')

        got = fa._record_dir(linked)
        # os.path.realpath: macOS puts TMPDIR under /var, itself a symlink
        # to /private/var; fence_expiry._registry_root resolves through it
        # (os.path.realpath) to find the primary checkout's .git, so the
        # expected side must be resolved the same way or this compares a
        # symlinked path against its own resolved target.
        self.assertEqual(got, os.path.join(os.path.realpath(primary), '.sbe', 'fable-authority'),
                          'a linked worktree must resolve to the PRIMARY checkout, not its own '
                          'missing .sbe, exactly the fence_expiry._registry_root contract')
        self.assertEqual(got, os.path.join(fence_expiry._registry_root(linked), '.sbe', 'fable-authority'))

    def test_a_root_with_its_own_sbe_resolves_to_itself(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, '.sbe'), exist_ok=True)
        with open(os.path.join(d, '.sbe', 'tasks.json'), 'w', encoding='utf-8') as fh:
            fh.write('{}')
        self.assertEqual(fa._record_dir(d), os.path.join(d, '.sbe', 'fable-authority'))


if __name__ == '__main__':
    unittest.main()
