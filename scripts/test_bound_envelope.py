"""M1-3 (B5-14, objection 11): in a proof phase a reservation is BOUND only when the frozen price catalog prices every
attempt the bridge can make for this exact argv. The bound is the attempt envelope: each model in or_ask's chain at
--max and at twice --max (the one length retry), prompt tokens taken as len(prompt bytes) + 1024. Anything the
catalog cannot price (missing, stale, an unpriced fallback, a prompt the dispatcher cannot see) is bound: false and
keeps today's rule. History never lowers a proof reservation.

Entry point: openrouter_dispatch.dispatch with run_strict replaced, a real ledger and the real proof start fixture.
The expected envelope is computed by hand here, independently of or_ask.attempt_plan, so a plan that dropped the retry
or the fallback would show.
"""
import json,os,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent)]
import test_proof_dispatch_accounting as F   # a module, so its ProofDispatch tests are not collected here again
from scripts.loop import proof_ledger as P
from plugin.runtime.brother.core import openrouter_ledger as L, openrouter_dispatch as D
from plugin.runtime.brother.core.openrouter_strict import MaxTokensTooLow

DEEPSEEK='deepseek/deepseek-v4.1-flash'
FALLBACK='nvidia/nemotron-3-ultra-550b-a55b:free'
PRICES={DEEPSEEK:(3e-07,6e-07),FALLBACK:(1e-07,2e-07)}   # fixture prices per token, not real ones
PROMPT='what is two plus two, answered in one word'
MAX=40000


def argv(prompt=PROMPT,max_tokens=MAX):
 tail=['--',prompt] if prompt is not None else []
 return [sys.executable,'/fixture/bin/or_ask.py','--model','deepseek','--effort','xhigh','--max',str(max_tokens),'--timeout','300']+tail


def by_hand(prompt=PROMPT,max_tokens=MAX,models=(DEEPSEEK,)):
 """The bridge's priced worst case over the models it may try: the requested model alone unless the caller accepts
 substitutes (2026-09-27: dispatch passes --only-model, so a substitute is never tried nor reserved for)."""
 tokens=len(prompt.encode('utf-8'))+1024
 return sum(pp*tokens+cp*m for pp,cp in (PRICES[x] for x in models) for m in (max_tokens,2*max_tokens))


class Envelope(unittest.TestCase):
 setUp=F.ProofDispatch.setUp
 write_start=F.ProofDispatch.write_start
 launch=F.ProofDispatch.launch
 rows=F.ProofDispatch.rows
 def catalog(self,models=PRICES):
  data=[dict(id=m,pricing=dict(prompt=repr(p),completion=repr(c))) for m,(p,c) in models.items()]
  path=self.state/'openrouter-models.json';path.write_text(json.dumps(dict(data=data)));return path
 def dispatch(self,bridge=None,**extra):
  runner=mock.Mock(return_value=(SimpleNamespace(stderr='[billed] usd=0.01 attempts=1 known=yes'),DEEPSEEK))
  with mock.patch.object(D,'run_strict',runner):
   D.dispatch(argv() if bridge is None else bridge,DEEPSEEK,holder_id='holder',state_root=str(self.state),max_tokens=MAX,**extra)
  return runner
 def reservation(self):return [r for r in self.rows() if r['type']=='RESERVE'][-1]

 def test_a_priced_plan_reserves_its_whole_envelope_bound(self):
  self.catalog();self.dispatch();r=self.reservation()
  self.assertIs(r.get('bound'),True,r);self.assertAlmostEqual(r['estimated_cost'],by_hand(),places=12)
 def test_a_caller_that_accepts_substitutes_reserves_for_them_too(self):
  self.catalog();self.dispatch(allow_fallback=True);r=self.reservation()
  self.assertIs(r.get('bound'),True,r);self.assertAlmostEqual(r['estimated_cost'],by_hand(models=tuple(PRICES)),places=12)
 def test_history_never_lowers_a_proof_reservation(self):
  self.catalog()
  rid=L.reserve(str(self.state),20,1,'holder',model='deepseek',now=86399,timeout_seconds=300)
  L.mark_dispatched(str(self.state),rid,'holder',now=86400);L.reconcile(str(self.state),rid,.0001,now=86401,cost_source='provider_usage')
  self.dispatch(model_alias='deepseek');r=self.reservation()
  self.assertIs(r.get('bound'),True,r);self.assertAlmostEqual(r['estimated_cost'],by_hand(),places=12)
 def test_a_missing_catalog_is_not_bound_and_keeps_the_hand_rule(self):
  self.dispatch();r=self.reservation()
  self.assertIs(r.get('bound'),False,r);self.assertEqual(r['estimated_cost'],D.LEGACY_HAND_ESTIMATE)
 def test_a_stale_catalog_is_not_bound(self):
  old=time.time()-8*86400;os.utime(str(self.catalog()),(old,old));self.dispatch()
  self.assertIs(self.reservation().get('bound'),False)
 def test_an_unpriced_fallback_is_not_bound(self):
  self.catalog({DEEPSEEK:PRICES[DEEPSEEK]});self.dispatch(allow_fallback=True)
  self.assertIs(self.reservation().get('bound'),False)
 def test_an_unpriced_substitute_nobody_will_try_does_not_unbind(self):
  self.catalog({DEEPSEEK:PRICES[DEEPSEEK]});self.dispatch()
  self.assertIs(self.reservation().get('bound'),True)
 def test_a_prompt_the_dispatcher_cannot_see_is_not_bound(self):
  self.catalog();self.dispatch(argv(prompt=None))
  self.assertIs(self.reservation().get('bound'),False)
 def test_a_bridge_floor_raise_is_refused_before_any_row(self):
  self.catalog();before=self.ledger.read_bytes()
  runner=mock.Mock()
  with mock.patch.object(D,'run_strict',runner):
   with self.assertRaises(MaxTokensTooLow):
    D.dispatch(argv(max_tokens=1000),DEEPSEEK,holder_id='holder',state_root=str(self.state),max_tokens=1000,min_max_tokens=1000)
  runner.assert_not_called();self.assertEqual(self.ledger.read_bytes(),before)
 def test_a_legacy_reservation_carries_no_bound(self):
  self.catalog()
  with mock.patch.dict(os.environ,{k:'' for k in self.env}):self.dispatch()
  self.assertNotIn('bound',self.reservation())


if __name__=='__main__':unittest.main()
