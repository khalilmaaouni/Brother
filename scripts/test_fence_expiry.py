"""Calibration for scripts/fence_expiry.py, in both directions.

The property this file exists to assert is not that reaping works. It is that
reaping is SAFE: it clears a claim and never touches content. Automating a
destructive act on the strength of a comment is how this estate lost about 500
lines on 2026-08-29, and the tool that did it also only meant to tidy up.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import fence_expiry as fx  # noqa: E402

T = lambda s: datetime.datetime.fromisoformat(s)  # noqa: E731
NOW = T('2026-08-29T12:00:00+00:00')


def task(tid='t1', status='open', expiry='2026-08-30T00:00:00Z', owns=None, agent='a'):
    return {'id': tid, 'status': status, 'expiry': expiry, 'agent': agent,
            'ownedPaths': owns if owns is not None else ['some/file.py']}


class Classify(unittest.TestCase):
    def test_a_future_expiry_is_live(self):
        self.assertEqual(fx.classify(task(), NOW)[0], 'LIVE')

    def test_a_past_expiry_is_expired(self):
        self.assertEqual(fx.classify(task(expiry='2026-08-28T00:00:00Z'), NOW)[0], 'EXPIRED')

    def test_no_expiry_at_all_is_refused(self):
        """The exact shape of both fences that blocked two sessions today."""
        self.assertEqual(fx.classify(task(expiry=None), NOW)[0], 'NO-EXPIRY')

    def test_an_unparseable_expiry_is_refused_not_assumed_far_away(self):
        """A value nobody can read must never be treated as a distant date."""
        self.assertEqual(fx.classify(task(expiry='next tuesday'), NOW)[0], 'NO-EXPIRY')

    def test_a_closed_claim_is_not_judged(self):
        self.assertEqual(fx.classify(task(status='closed'), NOW)[0], 'CLOSED')

    def test_a_naive_datetime_is_read_as_utc_rather_than_crashing(self):
        self.assertEqual(fx.classify(task(expiry='2026-08-30T00:00:00'), NOW)[0], 'LIVE')

    def test_a_bare_date_is_accepted(self):
        self.assertEqual(fx.classify(task(expiry='2026-08-30'), NOW)[0], 'LIVE')


class Reaping(unittest.TestCase):
    def test_an_expired_claim_is_closed_and_says_why(self):
        doc = {'tasks': [task(expiry='2026-08-28T00:00:00Z')]}
        self.assertEqual(fx.reap(doc, NOW), ['t1'])
        self.assertEqual(doc['tasks'][0]['status'], 'closed')
        self.assertIn('REAPED', doc['tasks'][0]['reapReason'])

    def test_a_live_claim_is_left_alone(self):
        """The direction that matters as much as the other. A reaper that takes
        live claims is worse than no reaper."""
        doc = {'tasks': [task()]}
        self.assertEqual(fx.reap(doc, NOW), [])
        self.assertEqual(doc['tasks'][0]['status'], 'open')

    def test_a_claim_with_no_expiry_is_NOT_reaped(self):
        """It is REFUSED, which is a different verdict. Reaping it would destroy
        a claim somebody may still be holding; refusing it makes a human give it
        an expiry or close it deliberately."""
        doc = {'tasks': [task(expiry=None)]}
        self.assertEqual(fx.reap(doc, NOW), [])
        self.assertEqual(doc['tasks'][0]['status'], 'open')

    def test_the_reap_reason_names_the_owner_and_what_was_held(self):
        doc = {'tasks': [task(expiry='2026-08-28T00:00:00Z', agent='ghost',
                              owns=['a/b.py', 'c/d.py'])]}
        fx.reap(doc, NOW)
        reason = doc['tasks'][0]['reapReason']
        self.assertIn('ghost', reason)
        self.assertIn('a/b.py', reason)

    def test_ONLY_THE_REGISTRY_IS_TOUCHED_never_the_owned_files(self):
        """THE LOAD-BEARING TEST OF THIS FILE.

        Reaping is automated, so the property that makes it safe has to be
        asserted rather than promised. About 500 lines were destroyed in this
        estate on the same day this was written, by a tool that also only meant
        to clear up state. Create real files, name them in an expired claim,
        reap it, and require every byte still there."""
        d = tempfile.mkdtemp()
        paths = []
        for name in ('one.py', 'two.py'):
            p = os.path.join(d, name)
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write('# precious, uncommitted, irreplaceable\n')
            paths.append(p)
        doc = {'tasks': [task(expiry='2026-08-28T00:00:00Z', owns=paths)]}
        fx.reap(doc, NOW)
        for p in paths:
            self.assertTrue(os.path.exists(p), '%s was removed by a reap' % p)
            with open(p, encoding='utf-8') as fh:
                self.assertIn('precious', fh.read())


class ExitCodes(unittest.TestCase):
    def run_over(self, doc, *args):
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(doc, fh)
        fh.close()
        saved = fx.REGISTRY
        try:
            fx.REGISTRY = fh.name
            return fx.main(['--now', '2026-08-29T12:00:00+00:00'] + list(args))
        finally:
            fx.REGISTRY = saved
            os.unlink(fh.name)

    def test_all_claims_carrying_a_future_expiry_exits_zero(self):
        self.assertEqual(self.run_over({'tasks': [task()]}), 0)

    def test_a_claim_with_no_expiry_EXITS_ONE(self):
        """Without this the tool is a report, not a control."""
        self.assertEqual(self.run_over({'tasks': [task(expiry=None)]}), 1)

    def test_an_expired_claim_alone_does_not_fail_the_gate(self):
        """An expiry that lapsed is the system working. Only a claim that can
        NEVER retire is a finding."""
        self.assertEqual(self.run_over({'tasks': [task(expiry='2026-08-28T00:00:00Z')]}), 0)

    def test_an_unreadable_registry_is_NO_DATA_not_a_pass(self):
        saved = fx.REGISTRY
        try:
            fx.REGISTRY = os.path.join(tempfile.gettempdir(), 'no-such-registry-xyz.json')
            self.assertEqual(fx.main(['--now', '2026-08-29T12:00:00+00:00']), 2)
        finally:
            fx.REGISTRY = saved

    def test_the_real_registry_has_no_claim_that_can_never_retire(self):
        """Guards the live estate. Red here means a fence was opened that will
        hold its paths until a human notices, which is what happened twice."""
        self.assertEqual(fx.main(['--now', datetime.datetime.now(
            datetime.timezone.utc).isoformat()]), 0)


if __name__ == '__main__':
    unittest.main()
