"""Proof funding uses dispatch policy and this run's measured spend."""
from pathlib import Path
import contextlib,importlib,io,json,os,shutil,subprocess,sys,tempfile,time,unittest
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE/'loop'),str(HERE.parent)]
import test_proof_dispatch_accounting as F
sys.path.insert(0,str(HERE/'loop'))   # named for the parity gate: the loop copy, the one burn_guard imports beside itself
import proof_ledger as LOOP_LEDGER
from plugin.runtime.brother.core import openrouter_ledger as L
from scripts.loop import burn_guard as B

class Funding(unittest.TestCase):
 def setUp(self):
  F.ProofDispatch.setUp(self)
  patch=mock.patch.dict(os.environ,BROTHER_OR_STATE_ROOT=str(self.state));patch.start();self.addCleanup(patch.stop)
 write_start=F.ProofDispatch.write_start
 rows=F.ProofDispatch.rows
 def result(self,now=172801):return B.proof_funding(str(self.state),now)
 def reserve(self,cost=1,at=86399):return L.reserve(str(self.state),20,cost,'holder',now=at,timeout_seconds=300)
 def marker(self,rid):return L.mark_dispatched(str(self.state),rid,'holder',now=86401)
 def cli(self):
  env=dict(os.environ,BROTHER_OR_STATE_ROOT=str(self.state),PYTHONPATH=str(HERE.parent))
  return subprocess.run([sys.executable,'-B',str(HERE/'loop/burn_guard.py')],env=env,capture_output=True,text=True)
 def paid(self,cost=.25,at=86399):
  rid=self.reserve(at=at);self.marker(rid);L.reconcile(str(self.state),rid,cost,now=at+2,cost_source='provider_usage')
 def test_no_grant_uses_dispatch_default(self):self.assertEqual(self.result()['ceiling'],20)
 def test_configured_ceiling_is_shared(self):
  (self.state/'dispatch-limits.json').write_text(json.dumps({'daily_cap':5}));self.assertEqual(self.result()['ceiling'],5)
 def test_invalid_grant_never_lifts_cap(self):
  (self.state/'cap-grant.json').write_text(json.dumps({'daily_cap':999,'until':'bad'}));self.assertEqual(self.result()['ceiling'],20)
 def test_historic_unknown_stays_outside_new_headroom(self):
  x=self.result();self.assertEqual(x['headroom'],20);self.assertEqual(x['measured_spend_usd'],0)
 def test_money_keeps_own_run_spend_across_midnight(self):
  self.paid();self.assertEqual(self.result()['measured_spend_usd'],.25)
 def test_headroom_keeps_inflight_across_midnight(self):
  self.reserve(19);self.assertEqual(self.result()['headroom'],1)
 def test_abandoned_cost_refuses_more_funding(self):
  rid=self.reserve();self.marker(rid);L.abandon(str(self.state),rid,'unknown',now=86402)
  with self.assertRaises(ValueError):self.result()
 def test_zero_estimate_abandoned_still_refuses(self):
  rid=self.reserve(0);self.marker(rid);L.abandon(str(self.state),rid,'unknown',now=86402)
  with self.assertRaises(ValueError):self.result()
 def test_active_reservation_reduces_headroom_without_becoming_settlement(self):
  self.reserve(1);x=self.result();self.assertEqual(x['headroom'],19);self.assertEqual(x['measured_spend_usd'],0)
 def test_other_run_settlement_not_this_runs_money(self):
  self.paid();self.start.update(run_id='RC',attempt_id='b'*32,phase='RC');other=self.root/'RC';(other/'proof').mkdir(parents=True);(other/'proof/start.json').write_text(json.dumps(self.start))
  with mock.patch.dict(os.environ,BROTHER_RUN_DIR=str(other),BROTHER_PROOF_PHASE='RC'):self.assertEqual(self.result()['measured_spend_usd'],0)
 def test_bad_baseline_refuses(self):
  self.base.write_text(self.base.read_text()+' ')
  with self.assertRaises(ValueError):self.result()
 def test_partial_proof_policy_refuses(self):
  with mock.patch.dict(os.environ,BROTHER_PROOF_PHASE=''):
   with self.assertRaises(ValueError):self.result()
 def test_cli_has_money_and_funds_without_optional_grant(self):
  self.paid();r=self.cli();self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('MONEY   spent 0.25000000',r.stdout);self.assertEqual(r.stdout.strip().splitlines()[-1],'8')
 def test_cli_refuses_unknown_without_a_money_line(self):
  rid=self.reserve();self.marker(rid);L.abandon(str(self.state),rid,'unknown',now=86402);r=self.cli();self.assertEqual(r.returncode,3);self.assertNotIn('MONEY   spent',r.stdout);self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_cli_honors_low_deployment_ceiling(self):
  (self.state/'dispatch-limits.json').write_text(json.dumps({'daily_cap':.5}));r=self.cli();self.assertEqual(r.returncode,3);self.assertEqual(r.stdout.strip().splitlines()[-1],'0')

class BoundedAbandon(Funding):
 """M1-5 (B5-14, owner question Q1): proof_ledger.BOUNDED_ABANDON_COUNTS is ONE frozen switch. The owner ruled
 option A on 2026-09-27, so it ships True: a failed call whose reservation is BOUND counts at that bound, in headroom;
 an unbounded one still funds nothing. False (the rule before the ruling) keeps every failed paid call an unknown
 that funds nothing. Driven through the guard's own main(), with the constant patched on the very module the guard
 imports."""
 def abandon(self,bound=True,cost=.05):
  rid=L.reserve(str(self.state),20,cost,'holder',now=86399,bound=bound,timeout_seconds=300)
  self.marker(rid);L.abandon(str(self.state),rid,'post_dispatch.TimeoutExpired',now=86402);return rid
 def guard(self,counts):
  buf=io.StringIO()
  with mock.patch.object(LOOP_LEDGER,'BOUNDED_ABANDON_COUNTS',counts),mock.patch.object(sys,'argv',['burn_guard.py']),contextlib.redirect_stdout(buf):
   code=B.main()
  return code,buf.getvalue()
 def test_the_switch_ships_with_the_owners_ruling(self):
  from scripts.loop import proof_ledger as pkg
  self.assertIs(LOOP_LEDGER.BOUNDED_ABANDON_COUNTS,True);self.assertIs(pkg.BOUNDED_ABANDON_COUNTS,True)
 def test_switched_on_a_bounded_abandon_counts_at_its_bound(self):
  self.abandon();code,out=self.guard(True)
  self.assertEqual(code,0,out);self.assertIn('headroom 19.95000000',out);self.assertEqual(out.strip().splitlines()[-1],'8')
 def test_switched_off_a_bounded_abandon_funds_nothing(self):
  self.abandon();code,out=self.guard(False)
  self.assertEqual(code,3,out);self.assertIn('NO-DATA: proof funding unreadable (new terminal cost is unknown)',out);self.assertEqual(out.strip().splitlines()[-1],'0')
 def test_switched_on_an_unbounded_abandon_still_funds_nothing(self):
  self.abandon(bound=False);code,out=self.guard(True)
  self.assertEqual(code,3,out);self.assertIn('unknown',out);self.assertEqual(out.strip().splitlines()[-1],'0')
 def test_switched_on_an_untagged_abandon_still_funds_nothing(self):
  self.abandon(bound=None);code,out=self.guard(True)
  self.assertEqual(code,3,out);self.assertEqual(out.strip().splitlines()[-1],'0')


class LegacyBurnGuard(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.mkdtemp()
  self.addCleanup(lambda: shutil.rmtree(self.tmp,ignore_errors=True))
 def ledger(self,text):(Path(self.tmp)/'openrouter-ledger.jsonl').write_text(text)
 def grant(self,obj):(Path(self.tmp)/'cap-grant.json').write_text(json.dumps(obj))
 def cli(self):
  env=dict(os.environ,BROTHER_OR_STATE_ROOT=self.tmp,PYTHONPATH=str(HERE.parent))
  for k in ('BROTHER_PROOF_PHASE','BROTHER_PROOF_BASELINE','BROTHER_PROOF_BASELINE_SHA256'):
   env.pop(k,None)
  return subprocess.run([sys.executable,'-B',str(HERE/'loop/burn_guard.py')],env=env,capture_output=True,text=True)
 def test_active_reservation_counts_against_headroom(self):
  self.ledger('{"type":"RESERVE","reservation_id":"r1","estimated_cost":19}\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('headroom 1.00',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_nan_actual_cost_refuses(self):
  self.ledger('{"type":"RECONCILE","actual_cost":NaN}\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_non_numeric_abandoned_estimate_refuses(self):
  self.ledger('{"type":"RESERVE","reservation_id":"r1","estimated_cost":"banana"}\n{"type":"ABANDONED","reservation_id":"r1"}\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_nan_grant_refuses(self):
  self.ledger('')
  (Path(self.tmp)/'cap-grant.json').write_text('{"daily_cap":NaN}\n')
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 # AN UNKNOWN BILL IS LIABILITY, NOT CORRUPTION (2026-09-27): this case used to pin the latch that stopped a 100 USD run at
 # 0.23 after one timed out call. An abandoned hold now counts once, at its estimate; only a malformed one refuses.
 def test_abandoned_terminal_is_liability_and_room_still_funds(self):
  self.ledger('{"type":"RESERVE","reservation_id":"r1","estimated_cost":9.5}\n{"type":"ABANDONED","reservation_id":"r1"}\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertIn('+ 9.50 unresolved',r.stdout)
  self.assertIn('FUNDING OK',r.stdout)
  self.assertNotEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_abandoned_liability_that_uses_the_room_is_spent_not_no_data(self):
  self.ledger('{"type":"RESERVE","reservation_id":"r1","estimated_cost":9.5}\n{"type":"ABANDONED","reservation_id":"r1"}\n')
  self.grant({"daily_cap":10})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('FUNDING SPENT',r.stdout)
  self.assertNotIn('NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_an_abandon_of_an_unknown_reservation_refuses(self):
  self.ledger('{"type":"ABANDONED","reservation_id":"ghost"}\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('FUNDING NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_non_finite_actual_cost_refuses(self):
  self.ledger('{"type":"RECONCILE","actual_cost":NaN}\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_naive_expired_grant_falls_back_to_default_cap(self):
  self.ledger('{"type":"RECONCILE","actual_cost":19.5}\n')
  self.grant({"daily_cap":999,"until":"2020-01-01T00:00:00"})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('headroom 0.50',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_non_object_ledger_line_refuses_without_crash(self):
  self.ledger('null\n')
  self.grant({"daily_cap":20})
  r=self.cli()
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('NO-DATA',r.stdout)
  self.assertEqual(r.stdout.strip().splitlines()[-1],'0')
 def test_state_root_resolved_at_call_time(self):
  old=tempfile.mkdtemp(dir=os.getcwd())
  new=tempfile.mkdtemp(dir=os.getcwd())
  self.addCleanup(lambda: shutil.rmtree(old,ignore_errors=True))
  self.addCleanup(lambda: shutil.rmtree(new,ignore_errors=True))
  (Path(old)/'openrouter-ledger.jsonl').write_text('')
  (Path(old)/'cap-grant.json').write_text(json.dumps({"daily_cap":20}))
  with mock.patch.dict(os.environ,{'BROTHER_OR_STATE_ROOT':old,'HOME':old},clear=False):
   for k in ('BROTHER_PROOF_PHASE','BROTHER_PROOF_BASELINE','BROTHER_PROOF_BASELINE_SHA256'):
    os.environ.pop(k,None)
   importlib.reload(B)
   os.environ['BROTHER_OR_STATE_ROOT']=new
   import io,contextlib
   buf=io.StringIO()
   with contextlib.redirect_stdout(buf):
    ret=B.main()
   out=buf.getvalue()
  self.assertEqual(ret,3,out)
  self.assertIn('NO-DATA',out)
  self.assertEqual(out.strip().splitlines()[-1],'0')

if __name__=='__main__':unittest.main()
