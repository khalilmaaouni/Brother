"""Proves the tick contract in scripts/gen_orch_board.py.

The whole point of this board is that a DONE with no evidence never counts as
finished. Every test here is written so it would fail if tick_class collapsed
to a constant, or if counts() folded a claim into the done bucket.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closure_integrity  # noqa: E402
import gen_orch_board as G  # noqa: E402


def unit(uid, wave=1, depends_on=None, owns=None):
    return {
        'id': uid,
        'title': 'title for %s' % uid,
        'objective': 'objective for %s' % uid,
        'worker': 'sonnet',
        'checker': 'opus',
        'wave': wave,
        'depends_on': depends_on or [],
        'owns': owns or ['scripts/%s.py' % uid],
        'done_check': 'python3 scripts/test_%s.py' % uid,
    }


def st(state, evidence=''):
    return {'state': state, 'evidence': evidence}


MIN_WBS = {
    'units': [unit('u1')],
    'window': {'start': '2026-09-18T00:00:00+09:00',
               'drain_start': '2026-09-18T05:00:00+09:00',
               'hard_stop': '2026-09-18T07:00:00+09:00',
               'decided_by': 'founder order'},
    'role_map_tonight': {
        'orchestrator': 'main',
        'workers': 'sonnet',
        'checkers': 'opus',
        'final_check': 'main',
        'documentation': 'codex',
        'privacy_constraint': 'none',
    },
}


def min_status(run_id='r1', units=None):
    return {
        'run_id': run_id,
        'head': '0123456789abcdef',
        'generated_at': '2026-09-18T01:00:00+09:00',
        'units': units or {},
    }


class TickClassDone(unittest.TestCase):
    def test_done_with_evidence_is_a_tick_and_counts(self):
        self.assertEqual(G.tick_class('DONE', 'python3 x.py OK'), 'done')
        wbs = {'units': [unit('a')]}
        status = min_status(units={'a': st('DONE', 'ran it, output OK')})
        c = G.counts(wbs['units'], status)
        self.assertEqual(c['done'], 1)
        self.assertEqual(c['claim'], 0)


class TickClassClaim(unittest.TestCase):
    def test_done_with_empty_evidence_is_a_claim_not_a_done(self):
        self.assertEqual(G.tick_class('DONE', ''), 'claim')
        wbs = {'units': [unit('a')]}
        status = min_status(units={'a': st('DONE', '')})
        c = G.counts(wbs['units'], status)
        # The load-bearing assertion: a claim must NOT inflate the done count.
        self.assertEqual(c['done'], 0)
        self.assertEqual(c['claim'], 1)


class MixedPopulationCounts(unittest.TestCase):
    """Expected numbers are a literal written by hand, never derived by
    calling the module under test, so this can actually catch a miscount."""

    def test_mixed_population_matches_a_hand_written_literal(self):
        units = [
            unit('done1'), unit('done2'), unit('claim1'),
            unit('running1'), unit('parked1'), unit('planned1'),
        ]
        status = min_status(units={
            'done1': st('DONE', 'evidence one'),
            'done2': st('DONE', 'evidence two'),
            'claim1': st('DONE', ''),
            'running1': st('RUNNING', ''),
            'parked1': st('PARKED', ''),
            'planned1': st('QUEUED', ''),
        })
        c = G.counts(units, status)
        expected = {'total': 6, 'done': 2, 'claim': 1, 'running': 1, 'stopped': 1}
        self.assertEqual(c['total'], expected['total'])
        self.assertEqual(c['done'], expected['done'])
        self.assertEqual(c['claim'], expected['claim'])
        self.assertEqual(c['running'], expected['running'])
        self.assertEqual(c['stopped'], expected['stopped'])


class RunningAndStoppedStates(unittest.TestCase):
    def test_every_running_state_classifies_as_running(self):
        for state in ('RUNNING', 'VERIFYING', 'REVIEW', 'INTEGRATING', 'CANONICAL-VERIFY'):
            self.assertEqual(G.tick_class(state, ''), 'running', state)

    def test_every_stopped_state_classifies_as_stopped(self):
        for state in ('PARKED', 'EXHAUSTED', 'AWAITING-HUMAN', 'CANCELLED'):
            self.assertEqual(G.tick_class(state, ''), 'stopped', state)


class UnknownStateIsPlanned(unittest.TestCase):
    def test_unknown_state_maps_to_planned_never_raises(self):
        # The renderer must still render when a status file carries a state
        # nobody has met yet, so this must not raise.
        self.assertEqual(G.tick_class('SOME-FUTURE-STATE', ''), 'planned')
        self.assertEqual(G.tick_class('SOME-FUTURE-STATE', 'even with evidence'), 'planned')


class BuildSurfacesClaimsInThePage(unittest.TestCase):
    def test_a_claim_makes_the_word_claim_appear_in_the_page(self):
        wbs = {
            'units': [unit('a', wave=1)],
            'window': MIN_WBS['window'],
            'role_map_tonight': MIN_WBS['role_map_tonight'],
        }
        status = min_status(units={'a': st('DONE', '')})
        page = G.build(wbs, status)
        self.assertTrue('CLAIM' in page or 'claim' in page)

    def test_no_claim_present_still_renders(self):
        wbs = {
            'units': [unit('a', wave=1)],
            'window': MIN_WBS['window'],
            'role_map_tonight': MIN_WBS['role_map_tonight'],
        }
        status = min_status(units={'a': st('DONE', 'ran it, output OK')})
        page = G.build(wbs, status)
        self.assertIn('<html', page)


class MainCheckExitCode(unittest.TestCase):
    """main(["--check"]) must fail (non-zero) whenever a claim exists, and
    succeed (0) whenever none does. Drives it through the real module
    constants, patched to temp files, since load() reads them at call time."""

    def _write_sources(self, tmpdir, units_status):
        wbs_path = os.path.join(tmpdir, 'wbs.json')
        status_path = os.path.join(tmpdir, 'status.json')
        wbs = {
            'units': [unit('a', wave=1)],
            'window': MIN_WBS['window'],
            'role_map_tonight': MIN_WBS['role_map_tonight'],
        }
        status = min_status(units=units_status)
        with open(wbs_path, 'w', encoding='utf-8') as fh:
            json.dump(wbs, fh)
        with open(status_path, 'w', encoding='utf-8') as fh:
            json.dump(status, fh)
        return wbs_path, status_path

    def test_check_returns_nonzero_when_a_claim_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wbs_path, status_path = self._write_sources(tmpdir, {'a': st('DONE', '')})
            orig_wbs, orig_status = G.WBS, G.STATUS
            G.WBS, G.STATUS = wbs_path, status_path
            try:
                # Prove the patch actually took before trusting the result.
                self.assertEqual(G.WBS, wbs_path)
                self.assertEqual(G.STATUS, status_path)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = G.main(['--check'])
                self.assertNotEqual(rc, 0)
            finally:
                G.WBS, G.STATUS = orig_wbs, orig_status

    def test_check_returns_zero_when_no_claim_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wbs_path, status_path = self._write_sources(tmpdir, {'a': st('DONE', 'ran it, ok')})
            orig_wbs, orig_status = G.WBS, G.STATUS
            G.WBS, G.STATUS = wbs_path, status_path
            try:
                self.assertEqual(G.WBS, wbs_path)
                self.assertEqual(G.STATUS, status_path)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = G.main(['--check'])
                self.assertEqual(rc, 0)
            finally:
                G.WBS, G.STATUS = orig_wbs, orig_status


class MainCheckRunsClosureIntegrity(unittest.TestCase):
    """--check must also refuse a hollow CANCELLED/MERGED closure, not just
    an evidence-free DONE claim. ORCH-20's real shape (CANCELLED, no
    merged_into, no carried_by_check, no fold_proof) is the fixture."""

    def _write_sources(self, tmpdir, units, units_status):
        wbs_path = os.path.join(tmpdir, 'wbs.json')
        status_path = os.path.join(tmpdir, 'status.json')
        wbs = {
            'units': units,
            'window': MIN_WBS['window'],
            'role_map_tonight': MIN_WBS['role_map_tonight'],
        }
        status = min_status(units=units_status)
        with open(wbs_path, 'w', encoding='utf-8') as fh:
            json.dump(wbs, fh)
        with open(status_path, 'w', encoding='utf-8') as fh:
            json.dump(status, fh)
        return wbs_path, status_path

    def test_check_returns_nonzero_on_a_hollow_cancelled_fold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hollow = unit('a')
            hollow['disposition'] = 'MERGED'
            wbs_path, status_path = self._write_sources(
                tmpdir, [hollow], {'a': st('CANCELLED', 'folded into nothing provable')})
            orig_wbs, orig_status = G.WBS, G.STATUS
            G.WBS, G.STATUS = wbs_path, status_path
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = G.main(['--check'])
                self.assertNotEqual(rc, 0)
                self.assertIn('closure finding', buf.getvalue())
                self.assertIn('a:', buf.getvalue())
            finally:
                G.WBS, G.STATUS = orig_wbs, orig_status

    def test_check_returns_zero_when_the_fold_is_fully_proven(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, 'scripts'))
            open(os.path.join(tmpdir, 'scripts', 'test_a.py'), 'w').close()
            merged = unit('a')
            merged['disposition'] = 'MERGED'
            target = unit('b')
            wbs_path, status_path = self._write_sources(
                tmpdir, [merged, target], {
                    'a': st('CANCELLED', 'folded into b, proven'),
                    'b': st('DONE', 'ran it, OK'),
                })
            with open(status_path, encoding='utf-8') as fh:
                status_doc = json.load(fh)
            status_doc['units']['a']['merged_into'] = 'b'
            status_doc['units']['a']['carried_by_check'] = 'python3 scripts/test_a.py -v'
            status_doc['units']['a']['fold_proof'] = 'test_guard: FAILED (failures=1), restored'
            with open(status_path, 'w', encoding='utf-8') as fh:
                json.dump(status_doc, fh)
            orig_wbs, orig_status = G.WBS, G.STATUS
            orig_root = closure_integrity.ROOT
            G.WBS, G.STATUS = wbs_path, status_path
            closure_integrity.ROOT = tmpdir
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = G.main(['--check'])
                self.assertEqual(rc, 0)
                self.assertNotIn('closure finding', buf.getvalue())
            finally:
                G.WBS, G.STATUS = orig_wbs, orig_status
                closure_integrity.ROOT = orig_root


class BacklogExportForTheStopHook(unittest.TestCase):
    """The Stop hook reads one 'status' per row. A planned unit is ready only
    when its dependencies are ticked; a DONE without evidence is a claim, never
    done, so it cannot quietly unblock its dependants."""

    def test_rows_follow_the_tick_contract_and_dependencies(self):
        units = [{'id': 'A', 'depends_on': []}, {'id': 'B', 'depends_on': ['A']},
                 {'id': 'C', 'depends_on': ['D']}, {'id': 'D', 'depends_on': []},
                 {'id': 'E', 'depends_on': []}]
        status = {'units': {'A': {'state': 'DONE', 'evidence': 'ran'},
                            'D': {'state': 'DONE', 'evidence': ''},
                            'E': {'state': 'RUNNING'}}}
        rows = {r['id']: r for r in G.backlog(units, status)['rows']}
        self.assertEqual({k: v['status'] for k, v in rows.items()},
                         {'A': 'done', 'B': 'ready', 'C': 'waiting',
                          'D': 'claim', 'E': 'running'})
        self.assertEqual(rows['C']['note'], 'depends on D')


class SpendLineNeverReadsAsFree(unittest.TestCase):
    """TOKEN-05: an unmeasured unit says NO-DATA; zero is a real measurement
    and must not be confused with one that was never taken."""

    def test_measured_missing_and_zero_are_three_different_answers(self):
        self.assertEqual(G.spend_line({'tokens': 183718, 'worker': 'sonnet'}),
                         '183,718 tokens, sonnet')
        self.assertIn('NO-DATA', G.spend_line({'worker': 'sonnet'}))
        self.assertIn('NO-DATA', G.spend_line({'tokens': None, 'worker': 'x'}))
        self.assertIn('NO-DATA', G.spend_line({'tokens': 'lots'}))
        self.assertEqual(G.spend_line({'tokens': 0, 'worker': 'x'}), '0 tokens, x')
        self.assertIn('lane not recorded', G.spend_line({}))


if __name__ == '__main__':
    unittest.main()
