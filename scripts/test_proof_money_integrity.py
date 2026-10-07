"""Proof money fails closed on corrupt or impossible figures (money audit 2026-09-27, findings 3, 6 and 8).

  6  plain json.loads keeps the LAST of repeated members, so {"actual_cost": 9, "actual_cost": 0} read as zero spend.
     Every proof record parser (ledger rows, baseline, start record, launch record) refuses a repeated member.
  8  sums of finite amounts overflow to inf and inf minus inf is NaN, which made the cap comparison false and admitted
     a reservation under a zero cap. Every aggregate is finite or the reading refuses.
  3  a successful decision (Jev) call settled ABANDONED because the bridge printed no billed line, and its reservation
     claimed a bound although the decision request sends no max_tokens. Driven through dispatch with the REAL or_ask
     main in process (post_json and the key reader replaced): the call settles at its billed cost, unbound.

Fixture: the real proof start of test_proof_dispatch_accounting in a scratch folder. Nothing reaches a model, a key or
the network. One condition per case.

Run from the repository root: python3 -B scripts/test_proof_money_integrity.py
"""
import contextlib, io, json, os, signal, sys, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
import test_proof_dispatch_accounting as F   # a module, so its ProofDispatch tests are not collected here again
from scripts.loop import proof_ledger as P, or_ask as A
from plugin.runtime.brother.core import openrouter_ledger as L, openrouter_dispatch as D, openrouter_strict as S

REFUSED = (P.EvidenceError, L.LedgerError, L.BudgetExceeded)


class Base(unittest.TestCase):
    setUp = F.ProofDispatch.setUp
    write_start = F.ProofDispatch.write_start
    launch = F.ProofDispatch.launch
    rows = F.ProofDispatch.rows

    def append(self, raw):
        with self.ledger.open('a') as out:
            out.write(raw)

    def analyze(self):
        return P.analyze(self.ledger, self.base, self.digest)

    def settle_raw(self, rid, tail):
        row = dict(type='RECONCILE', reservation_id=rid, at=time.time(), run_id='RB', attempt_id='a' * 32,
                   cost_source='provider_usage', actual_cost=9)
        self.append(json.dumps(row)[:-1] + tail + '}\n')

    def dispatched(self, cost=9):
        rid = L.reserve(str(self.state), 20, cost, 'holder', timeout_seconds=300)
        L.mark_dispatched(str(self.state), rid, 'holder')
        return rid


class ARepeatedMemberIsRefused(Base):
    def test_a_settlement_repeating_its_cost_is_refused_by_the_analysis(self):
        """The audit case: 9 then 0 read as a measured zero."""
        self.settle_raw(self.dispatched(), ', "actual_cost": 0')
        with self.assertRaises(P.EvidenceError):
            self.analyze()

    def test_the_next_reservation_after_it_is_refused_with_nothing_written(self):
        self.settle_raw(self.dispatched(), ', "actual_cost": 0')
        before = self.ledger.read_bytes()
        with self.assertRaises(REFUSED):
            L.reserve(str(self.state), 20, 1, 'holder', timeout_seconds=300)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_control_a_settlement_without_a_repeat_is_measured(self):
        self.settle_raw(self.dispatched(), '')
        self.assertEqual(self.analyze()['runs']['RB']['measured_spend_usd'], 9)

    def test_a_launch_record_repeating_its_deadline_refuses_admission(self):
        where = P.launch_dir(self.run) / 'launch.json'
        record = dict(schema='loop-proof-launch-v1', run_dir=str(self.run.resolve()), deadline_epoch=time.time() - 60)
        where.write_text(json.dumps(record)[:-1] + ', "deadline_epoch": %r}' % (time.time() + 86400))
        with self.assertRaises(P.EvidenceError):
            P.admit_locked(str(self.run), 30, str(REPO / 'scripts' / 'loop' / 'proof_ledger.py'))

    def test_a_start_record_repeating_a_member_is_refused(self):
        raw = json.dumps(self.start)
        (self.run / 'proof' / 'start.json').write_text(raw[:-1] + ', "run_id": "RB"}')
        with self.assertRaises(P.EvidenceError):
            P.run_identity()

    def test_a_baseline_repeating_a_member_is_refused(self):
        raw = self.base.read_bytes()
        forged = b'{"schema": "other", ' + raw[1:]
        self.base.write_bytes(forged)
        with self.assertRaises(P.EvidenceError):
            P.analyze(self.ledger, self.base, P.sha256(forged))


class AnAggregateThatIsNotFiniteIsRefused(Base):
    def overflowing_abandons(self):
        """The audit case: two individually finite abandoned estimates of 1e308."""
        now = time.time()
        for rid in ('a', 'b'):
            self.append(json.dumps(dict(type='RESERVE', reservation_id=rid, estimated_cost=1e308, at=now, run_id='RB',
                                        attempt_id='a' * 32)) + '\n')
            self.append(json.dumps(dict(type='ABANDONED', reservation_id=rid, at=now, reason='unknown')) + '\n')

    def test_an_overflowing_unknown_liability_is_refused_by_the_analysis(self):
        self.overflowing_abandons()
        with self.assertRaises(P.EvidenceError):
            self.analyze()

    def test_the_audit_case_admits_nothing_under_a_zero_cap(self):
        self.overflowing_abandons()
        before = self.ledger.read_bytes()
        with self.assertRaises(REFUSED):
            L.reserve(str(self.state), 0, 1, 'audit', timeout_seconds=300)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_an_overflowing_measured_spend_is_refused_by_the_analysis(self):
        rids = [self.dispatched(cost=1), self.dispatched(cost=1)]
        for rid in rids:
            L.reconcile(str(self.state), rid, 1e308, cost_source='provider_usage')
        with self.assertRaises(P.EvidenceError):
            self.analyze()

    def test_an_overflowing_settled_total_is_refused_by_the_cap_view(self):
        """Settled spend outside the proof scope (baseline rows of today) is summed by cap_view alone."""
        now = time.time()
        rows = []
        for rid in ('s1', 's2'):
            rows += [dict(type='RESERVE', reservation_id=rid, holder_id='old', estimated_cost=0, at=now),
                     dict(type='RECONCILE', reservation_id=rid, actual_cost=1e308, at=now)]
        self.ledger.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        base = self.root / 'baseline-settled.json'
        digest = P.capture(self.ledger, base)
        self.start.update(ledger_baseline=str(base), ledger_baseline_sha256=digest)
        self.write_start()
        with mock.patch.dict(os.environ, BROTHER_PROOF_BASELINE=str(base), BROTHER_PROOF_BASELINE_SHA256=digest):
            with self.assertRaises(P.EvidenceError):
                P.cap_view(str(self.state), now)


def execute_bridge(args, env, outcomes, seen):
    """The real or_ask main, in process: post_json and the key reader replaced, nothing leaves the machine."""
    def post(req, seconds, holder=None, attempt=0):   # the bridge passes its generation holder and attempt number (2026-09-27)
        seen.append(json.loads(req.data))
        out = outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out
    out, err = io.StringIO(), io.StringIO()
    old = signal.getsignal(signal.SIGTERM)
    try:
        with mock.patch.object(sys, 'argv', args[1:]), mock.patch.dict(os.environ, env), \
                mock.patch.object(A, 'read_key', return_value='offline-fixture'), mock.patch.object(A, 'post_json', post), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = A.main()
    finally:
        signal.signal(signal.SIGTERM, old)
    return SimpleNamespace(returncode=rc, stdout=out.getvalue(), stderr=err.getvalue())


class ALostRetryAfterAChargedAttemptIsUnknown(Base):
    """SBE audit 2026-09-27, the second shape of finding 1, through dispatch and the real bridge: a charged attempt,
    then its retry and the fallback lose their replies. It used to settle RECONCILE at the partial 0.25."""

    def test_the_call_settles_abandoned_never_at_the_partial_charge(self):
        argv = [sys.executable, str(REPO / 'scripts' / 'loop' / 'or_ask.py'), '--model', 'deepseek', '--max', '40000',
                '--', 'fixture']
        charged = {'model': 'deepseek/deepseek-v4.1-flash', 'usage': {'cost': 0.25},
                   'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}]}
        seen, outcomes = [], [charged, TimeoutError('lost response after provider contact'),
                              TimeoutError('lost fallback response')]

        def strict(args, requested, timeout, **kw):
            return S.run_strict(args, requested, timeout, allow_fallback=kw.get('allow_fallback', False),
                                runner=lambda a, t: execute_bridge(a, kw['env'], outcomes, seen))
        with mock.patch.object(D, 'run_strict', strict):
            result, _ = D.dispatch(argv, 'deepseek/deepseek-v4.1-flash', estimated_cost=1, holder_id='holder',
                                   state_root=str(self.state), max_tokens=40000)
        # two attempts, not three: dispatch passes --only-model since 2026-09-27, so the substitute is never asked
        self.assertEqual((result.returncode, len(seen)), (44, 2), result.stderr)
        self.assertEqual(self.rows()[-1]['type'], 'ABANDONED')
        self.assertEqual(self.analyze()['unknown_cost_calls'], 1)


class ADecisionCallSettlesAtItsBilledCost(Base):
    """Finding 3 end to end: the audit's decisions case, through dispatch and the real bridge."""

    def test_a_decision_call_is_unbound_and_settles_at_its_billed_cost(self):
        model = A.MODEL_ALIASES['jev']
        data = [dict(id=m, pricing=dict(prompt='0.000001', completion='0.000001')) for m in [model] + A.FALLBACK_MODELS]
        (self.state / 'openrouter-models.json').write_text(json.dumps(dict(data=data)))
        prompt = json.dumps(dict(state={'x': 1}, questions={'q': {'type': 'noul', 'instructions': 'decide'}}))
        argv = [sys.executable, str(REPO / 'scripts' / 'loop' / 'or_ask.py'), '--model', 'jev', '--effort', 'xhigh',
                '--max', '32000', '--timeout', '300', '--decisions', '--', prompt]
        seen, outcomes = [], [dict(model=model, answers={'q': {'noul': True}},
                                   usage=dict(input_tokens=1, output_tokens=1, cost=.25))]

        def strict(args, requested, timeout, **kw):
            return S.run_strict(args, requested, timeout, allow_fallback=kw.get('allow_fallback', False),
                                runner=lambda a, t: execute_bridge(a, kw['env'], outcomes, seen))
        with mock.patch.object(D, 'run_strict', strict):
            result, _ = D.dispatch(argv, model, holder_id='audit', state_root=str(self.state), max_tokens=32000)
        self.assertEqual(result.returncode, 0, result.stderr)
        new = [r for r in self.rows() if r['reservation_id'] != 'historic']
        self.assertEqual([r['type'] for r in new], ['RESERVE', 'DISPATCH_START', 'RECONCILE'])
        self.assertIs(new[0].get('bound'), False, 'a decision request has no token ceiling, so it is never bound')
        self.assertEqual(new[-1]['actual_cost'], .25)
        self.assertEqual(self.analyze()['unknown_cost_calls'], 0)
        self.assertNotIn('max_tokens', seen[0])


if __name__ == '__main__':
    unittest.main(verbosity=1)
