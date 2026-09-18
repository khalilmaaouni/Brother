#!/usr/bin/env python3
"""Tests for scripts/effort_authorisation.py (ORCH-30).

Each test drives one branch of check_authorisation() or is_above_cap() in
the direction that must fail as well as the direction that must pass, per
worker contract rule 5: a check that cannot fail is not evidence.
"""

import unittest
from datetime import datetime, timezone

import effort_authorisation as ea


VALID_RECORD = {
    'words': 'Raise this one dispatch to xhigh, I already reviewed the spec.',
    'granted_by': 'khalil',
    'session': 'session-2026-09-18-orch',
    'until': '2030-01-01T00:00:00+00:00',
}
NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)


class IsAboveCapTests(unittest.TestCase):

    def test_below_cap_is_false(self):
        self.assertFalse(ea.is_above_cap('medium'))

    def test_at_cap_is_false(self):
        self.assertFalse(ea.is_above_cap('high'))

    def test_above_cap_is_true(self):
        self.assertTrue(ea.is_above_cap('xhigh'))
        self.assertTrue(ea.is_above_cap('max'))

    def test_unknown_effort_raises(self):
        with self.assertRaises(ValueError):
            ea.is_above_cap('ultra')

    def test_unknown_cap_raises(self):
        with self.assertRaises(ValueError):
            ea.is_above_cap('high', cap='ultra')

    def test_custom_cap_shifts_the_line(self):
        # Same effort, different outcome depending on the cap passed in:
        # proves the comparison actually reads `cap`, not a hardcoded
        # STANDING_CAP baked into the branch.
        self.assertFalse(ea.is_above_cap('high', cap='xhigh'))
        self.assertTrue(ea.is_above_cap('medium', cap='low'))


class CheckAuthorisationAtOrBelowCapTests(unittest.TestCase):

    def test_at_cap_allowed_with_no_record(self):
        allowed, reason = ea.check_authorisation('high', None, 'agent-1', now=NOW)
        self.assertTrue(allowed)
        self.assertIn('no authorisation needed', reason)

    def test_below_cap_allowed_even_with_a_bad_record(self):
        # The record is not even inspected at or below the cap: garbage
        # in a record that is never gated must not accidentally refuse.
        allowed, reason = ea.check_authorisation('medium', {}, 'agent-1', now=NOW)
        self.assertTrue(allowed)


class CheckAuthorisationAboveCapTests(unittest.TestCase):

    def test_valid_record_allows_it(self):
        allowed, reason = ea.check_authorisation('xhigh', VALID_RECORD, 'agent-1', now=NOW)
        self.assertTrue(allowed)
        self.assertIn('khalil', reason)

    def test_no_record_refused(self):
        allowed, reason = ea.check_authorisation('xhigh', None, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('missing authorisation', reason)

    def test_empty_record_refused(self):
        allowed, reason = ea.check_authorisation('xhigh', {}, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('missing authorisation', reason)

    def test_missing_granted_by_refused(self):
        record = dict(VALID_RECORD)
        del record['granted_by']
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('named authoriser', reason)

    def test_self_authored_refused_even_when_otherwise_valid(self):
        record = dict(VALID_RECORD, granted_by='agent-1')
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('self-authored', reason)

    def test_self_authored_refused_even_when_also_missing_words(self):
        # Proves self-authorship is checked ahead of the words/session/
        # until fields, not merely as a side effect of one of them being
        # blank: the rule holds "regardless of any other field".
        record = {'granted_by': 'agent-1', 'session': 's', 'until': '2030-01-01T00:00:00Z'}
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('self-authored', reason)

    def test_missing_words_refused(self):
        record = dict(VALID_RECORD)
        del record['words']
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('verbatim words', reason)

    def test_missing_session_refused(self):
        record = dict(VALID_RECORD)
        del record['session']
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('session', reason)

    def test_missing_until_refused(self):
        record = dict(VALID_RECORD)
        del record['until']
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('until', reason)

    def test_unparsable_until_refused(self):
        record = dict(VALID_RECORD, until='soon')
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('could not be parsed', reason)

    def test_expired_until_refused(self):
        record = dict(VALID_RECORD, until='2020-01-01T00:00:00+00:00')
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('expired', reason)

    def test_until_equal_to_now_is_expired_not_live(self):
        # Boundary: "at or before now" refuses exactly-now too, it does
        # not treat the instant of expiry as still-live.
        record = dict(VALID_RECORD, until=NOW.isoformat())
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertFalse(allowed)
        self.assertIn('expired', reason)

    def test_trailing_z_is_accepted(self):
        record = dict(VALID_RECORD, until='2030-01-01T00:00:00Z')
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertTrue(allowed)

    def test_naive_until_is_treated_as_utc(self):
        record = dict(VALID_RECORD, until='2030-01-01T00:00:00')
        allowed, reason = ea.check_authorisation('xhigh', record, 'agent-1', now=NOW)
        self.assertTrue(allowed)

    def test_unknown_effort_raises(self):
        with self.assertRaises(ValueError):
            ea.check_authorisation('ultra', VALID_RECORD, 'agent-1', now=NOW)

    def test_unknown_effort_raises_even_with_no_record(self):
        with self.assertRaises(ValueError):
            ea.check_authorisation('ultra', None, 'agent-1', now=NOW)


if __name__ == '__main__':
    unittest.main()
