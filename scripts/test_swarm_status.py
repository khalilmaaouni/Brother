#!/usr/bin/env python3
"""Calibration for scripts/swarm_status.py.

The property under test is not that a table is produced. It is that the table
never says something it does not know. A status board is read by people who
will not go and check it, so a plausible wrong number here is worse than a
blank cell: three of these tests exist only to prove a cell stays blank.

Mirrors scripts/test_graph_loop.py: its node()/doc() fixture helpers, its
sys.path insert, plain unittest with unittest.mock, no framework.
"""
import os
import socket
import sys
import unittest
import unittest.mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import graph_loop as gl  # noqa: E402
import swarm_status as ss  # noqa: E402

#: The two tests that read this repository's own board skip where that board
#: is not shipped (the public export), as scripts/test_board_status.py does.
needs_board = unittest.skipUnless(
    os.path.isfile(gl.ROADMAP),
    "the live board docs/plan/READINESS-ROADMAP-2026-08-29.json is not shipped here")

NOW = 1_700_000_000.0


def node(nid, deps=None, owns=None, status='SCHEDULED', hours=1,
         in_ship=True, event=None, owner=None):
    # owns defaults to [] (declared read-only) rather than None, so a fixture
    # never accidentally exercises the undeclared-scope path. A test that
    # WANTS that path passes owns=None explicitly, and must therefore say so.
    row = {'id': nid, 'title': nid, 'status': status,
           'depends_on': deps or [], 'owns': [] if owns is None else owns,
           'effort_hours': hours, 'in_ship_v1': in_ship}
    if owns is None:
        row['owns'] = None
    if event:
        row['event'] = event
    if owner:
        row['owner'] = owner
    return row


def declared(nid, **kw):
    """A node with a real, declared write set of its own."""
    kw.setdefault('owns', ['%s.py' % nid.lower()])
    return node(nid, **kw)


def doc(rows):
    return {'rows': rows, 'features': []}


def claim(unit_id, started_ago, ttl=600.0, state='claimed', pid=None,
          hostname=None):
    return {'unit_id': unit_id, 'owner': 'lane-1', 'state': state,
            'claimed_at': NOW - started_ago,
            'expires_at': NOW - started_ago + ttl,
            'pid': os.getpid() if pid is None else pid,
            'hostname': socket.gethostname() if hostname is None else hostname,
            'attempt': 1}


def capped(slots, notes):
    """Force graph_loop's DERIVED capacity, which is what the real refusal
    path uses. Passing slots= to plan() would set the number but not the
    scheduler's own reason for it, and the reason is what rule three is
    about."""
    return unittest.mock.patch.object(gl, 'machine_capacity',
                                      return_value=(slots, list(notes)))


class ARunningBoard(unittest.TestCase):
    def test_a_live_claim_gives_a_real_elapsed_time(self):
        d = doc([declared('A', status='IN-FLIGHT'),
                 declared('B', status='IN-FLIGHT')])
        b = ss.board(d, {'A': claim('A', 252),
                         'B': claim('B', 3838, ttl=7200.0)},
                     now=NOW, slots=4)
        by_id = {r['id']: r for r in b['rows']}
        self.assertEqual(by_id['A']['state'], 'RUNNING')
        self.assertEqual(by_id['A']['detail'], '04:12')
        self.assertEqual(by_id['B']['detail'], '1:03:58')

    def test_a_ready_unit_is_shown_as_dispatchable(self):
        b = ss.board(doc([declared('A')]), {}, now=NOW, slots=4)
        self.assertEqual([r['state'] for r in b['rows']], ['READY'])

    def test_the_rendered_board_names_every_state_it_holds(self):
        d = doc([declared('A', status='IN-FLIGHT'), declared('B'),
                 declared('C', deps=['B'])])
        text = ss.render(ss.board(d, {'A': claim('A', 61)}, now=NOW, slots=4))
        self.assertIn('RUNNING', text)
        self.assertIn('01:01', text)
        self.assertIn('READY', text)
        self.assertIn('BLOCKED', text)


class AnUnknownStartTime(unittest.TestCase):
    """RULE ONE. Nothing durable recorded a start, so no number is printed."""

    def test_an_in_flight_unit_with_no_claim_prints_NO_DATA_not_a_guess(self):
        b = ss.board(doc([declared('A', status='IN-FLIGHT')]), {},
                     now=NOW, slots=4)
        detail = b['rows'][0]['detail']
        self.assertTrue(detail.startswith('NO-DATA'), detail)
        # No digit anywhere: a clock, however formatted, would put one here.
        self.assertFalse(any(c.isdigit() for c in detail), detail)

    def test_an_unreadable_claim_store_names_the_problem_in_every_time_cell(self):
        b = ss.board(doc([declared('A', status='IN-FLIGHT')]), None,
                     claims_problem='the claim store could not be read: boom',
                     now=NOW, slots=4)
        self.assertIn('NO-DATA', b['rows'][0]['detail'])
        self.assertIn('boom', b['rows'][0]['detail'])

    def test_a_claim_whose_lease_died_is_ABANDONED_not_a_running_clock(self):
        """An expired lease under an IN-FLIGHT row is the one case where a
        plausible elapsed time would be actively misleading: the row says
        running and the durable record says nobody is there."""
        b = ss.board(doc([declared('A', status='IN-FLIGHT')]),
                     {'A': claim('A', 4000, ttl=600.0)}, now=NOW, slots=4)
        self.assertEqual(b['rows'][0]['state'], 'ABANDONED')
        self.assertIn('lease expired', b['rows'][0]['detail'])

    def test_a_claim_missing_claimed_at_prints_NO_DATA(self):
        broken = claim('A', 60)
        del broken['claimed_at']
        b = ss.board(doc([declared('A', status='IN-FLIGHT')]), {'A': broken},
                     now=NOW, slots=4)
        self.assertIn('NO-DATA', b['rows'][0]['detail'])

    def test_a_claim_that_was_released_is_not_timed(self):
        b = ss.board(doc([declared('A', status='IN-FLIGHT')]),
                     {'A': claim('A', 60, state='done')}, now=NOW, slots=4)
        self.assertIn('NO-DATA', b['rows'][0]['detail'])


class AFullyBlockedBoard(unittest.TestCase):
    def test_every_unit_waits_and_the_wait_names_the_real_dependency(self):
        d = doc([declared('A', status='IN-FLIGHT'),
                 declared('B', deps=['A']),
                 declared('C', deps=['A', 'B'])])
        b = ss.board(d, {}, now=NOW, slots=4)
        by_id = {r['id']: r for r in b['rows']}
        self.assertEqual(by_id['B']['state'], 'BLOCKED')
        self.assertEqual(by_id['B']['detail'], 'waits for A')
        self.assertEqual(by_id['C']['detail'], 'waits for A, B')
        self.assertEqual(b['counts']['READY'], 0)

    def test_the_waits_for_cell_is_not_row_order(self):
        """C is declared FIRST and waits on B, which is declared last. A
        board deriving the wait from ordering would say the opposite."""
        d = doc([declared('C', deps=['B']), declared('B', status='IN-FLIGHT')])
        b = ss.board(d, {}, now=NOW, slots=4)
        by_id = {r['id']: r for r in b['rows']}
        self.assertEqual(by_id['C']['detail'], 'waits for B')


class ANonDependencyDeferral(unittest.TestCase):
    """RULE FOUR. Not everything that is not running is waiting on a unit."""

    def test_an_event_wait_says_the_event_not_a_dependency(self):
        d = doc([declared('A', event='the v1.0 tag')])
        b = ss.board(d, {}, now=NOW, slots=4)
        row = b['rows'][0]
        self.assertEqual(row['state'], 'DEFERRED')
        self.assertEqual(row['reason_kind'], 'EVENT')
        self.assertIn('the v1.0 tag', row['detail'])
        self.assertNotIn('waits for', row['detail'])

    def test_a_founder_gated_unit_says_so(self):
        b = ss.board(doc([declared('A', owner='FOUNDER')]), {},
                     now=NOW, slots=4)
        self.assertEqual(b['rows'][0]['reason_kind'], 'FOUNDER')

    def test_an_undeclared_scope_says_so(self):
        b = ss.board(doc([node('A', owns=None)]), {}, now=NOW, slots=4)
        self.assertEqual(b['rows'][0]['reason_kind'], 'UNDECLARED')

    def test_running_out_of_slots_is_not_a_dependency_either(self):
        d = doc([declared('A'), declared('B')])
        b = ss.board(d, {}, now=NOW, slots=1)
        kinds = [r.get('reason_kind') for r in b['rows'] if r['state'] == 'DEFERRED']
        self.assertEqual(kinds, ['NO-SLOT'])


class AZeroCapacityMachine(unittest.TestCase):
    """RULE THREE. An empty board and a refused board are different facts."""

    NOTE = 'REFUSE: 6.6 GiB free is under the 8 GiB floor'

    def test_a_refused_board_is_marked_refused(self):
        with capped(0, ['8 core(s), reserving 2, so 6 slot(s)', self.NOTE]):
            b = ss.board(doc([declared('A'), declared('B')]), {}, now=NOW)
        self.assertTrue(b['refused'])
        self.assertEqual(b['capacity'], 0)

    def test_a_refused_board_does_not_render_as_a_calm_idle_system(self):
        with capped(0, [self.NOTE]):
            text = ss.render(ss.board(doc([declared('A')]), {}, now=NOW))
        self.assertIn('REFUSED, not idle', text)
        self.assertIn('under the 8 GiB floor', text)

    def test_a_refused_board_still_shows_the_work_it_refused_to_start(self):
        """The units do not vanish because the machine said no. A board that
        rendered nothing here would be indistinguishable from no backlog."""
        with capped(0, [self.NOTE]):
            b = ss.board(doc([declared('A'), declared('B')]), {}, now=NOW)
        self.assertEqual(b['counts']['DEFERRED'], 2)
        self.assertEqual(b['counts']['READY'], 0)

    def test_an_idle_board_is_NOT_marked_refused(self):
        with capped(4, ['4 slot(s)']):
            b = ss.board(doc([declared('A', status='DONE')]), {}, now=NOW)
        self.assertFalse(b['refused'])
        self.assertNotIn('REFUSED', ss.render(b))


class TheThreeZeros(unittest.TestCase):
    """RULE TWO. The character 0 carries three opposite meanings."""

    def test_zero_because_none_were_found(self):
        d = doc([declared('A', owns=['a.py']), declared('B', owns=['b.py'])])
        conf = ss.board(d, {}, now=NOW, slots=4)['conflicts']
        self.assertEqual(conf['count'], 0)
        self.assertEqual(conf['kind'], 'NONE')
        self.assertIn('2 unit(s) were checked', conf['note'])

    def test_zero_because_nothing_reached_the_check(self):
        with capped(0, ['REFUSE']):
            conf = ss.board(doc([declared('A'), declared('B')]), {},
                            now=NOW)['conflicts']
        self.assertEqual(conf['count'], 0)
        self.assertEqual(conf['kind'], 'NOTHING-TESTED')
        self.assertIn('NOT MEASURED', conf['note'])

    def test_zero_because_the_conflict_data_could_not_be_read(self):
        """A unit that declared no write set cannot be conflict-checked at
        all, so no overlap was found AND none could have been."""
        d = doc([node('A', owns=None), declared('B', owns=['b.py'])])
        conf = ss.board(d, {}, now=NOW, slots=4)['conflicts']
        self.assertIsNone(conf['count'])
        self.assertEqual(conf['kind'], 'NO-DATA')

    def test_the_three_zeros_do_not_render_identically(self):
        found = ss.conflict_summary([({'id': 'X'}, 'write set overlaps A')], 1)
        none = ss.conflict_summary([], 2)
        nothing = ss.conflict_summary([({'id': 'X'}, 'no free slot: capacity is 0')], 0)
        unreadable = ss.conflict_summary([({'id': 'X'}, 'NO DECLARED SCOPE: owns is absent')], 0)
        kinds = {none['kind'], nothing['kind'], unreadable['kind']}
        self.assertEqual(len(kinds), 3)
        self.assertEqual(found['count'], 1)
        self.assertIsNone(unreadable['count'])

    def test_a_real_overlap_is_counted_not_hidden(self):
        d = doc([declared('A', owns=['shared/x.py']),
                 declared('B', owns=['shared/x.py'])])
        conf = ss.board(d, {}, now=NOW, slots=4)['conflicts']
        self.assertEqual(conf['count'], 1)
        self.assertEqual(conf['kind'], 'FOUND')

    def test_a_deferral_phrased_in_words_this_does_not_know_is_NO_DATA(self):
        """If graph_loop grows a sixth deferral reason, this must say it
        cannot tell rather than quietly counting the unit as conflict-free."""
        conf = ss.conflict_summary([({'id': 'X'}, 'some new reason')], 1)
        self.assertIsNone(conf['count'])
        self.assertEqual(conf['kind'], 'NO-DATA')


class TheClaimStoreBoundary(unittest.TestCase):
    def test_an_absent_store_is_no_claims_not_a_failure(self):
        claims, problem = ss.read_claims(os.path.join(REPO_ROOT, 'no-such.json'))
        self.assertEqual(claims, {})
        self.assertEqual(problem, '')

    def test_a_malformed_store_refuses_rather_than_reading_as_empty(self):
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            fh.write('{not json')
            path = fh.name
        try:
            claims, problem = ss.read_claims(path)
        finally:
            os.unlink(path)
        self.assertIsNone(claims)
        self.assertIn('could not be read', problem)

    def test_a_store_that_is_not_a_mapping_refuses(self):
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            fh.write('[1, 2, 3]')
            path = fh.name
        try:
            claims, problem = ss.read_claims(path)
        finally:
            os.unlink(path)
        self.assertIsNone(claims)
        self.assertIn('not a mapping', problem)


class TheCommandLine(unittest.TestCase):
    def test_an_unreadable_graph_exits_2_with_NO_DATA(self):
        code = ss.main(['--roadmap', os.path.join(REPO_ROOT, 'no-such.json')])
        self.assertEqual(code, 2)

    @needs_board
    def test_the_real_repository_renders_at_exit_0(self):
        self.assertEqual(ss.main([]), 0)

    @needs_board
    def test_json_output_is_json(self):
        import contextlib
        import io
        import json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ss.main(['--json'])
        self.assertEqual(code, 0)
        self.assertIn('conflicts', json.loads(buf.getvalue()))


class ElapsedFormatting(unittest.TestCase):
    def test_under_an_hour_is_minutes_and_seconds(self):
        self.assertEqual(ss.elapsed_text(252), '04:12')

    def test_an_hour_or_more_carries_the_hours(self):
        self.assertEqual(ss.elapsed_text(3838), '1:03:58')

    def test_a_start_in_the_future_is_NO_DATA_not_a_negative_clock(self):
        self.assertIn('NO-DATA', ss.elapsed_text(-5))


if __name__ == '__main__':
    unittest.main()
