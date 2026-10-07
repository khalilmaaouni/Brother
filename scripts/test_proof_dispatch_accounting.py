"""Proof dispatch spends only after durable identity and cap admission."""
import json,os,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from scripts.loop import proof_ledger as P
from plugin.runtime.brother.core import openrouter_ledger as L, openrouter_dispatch as D

class ProofDispatch(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.state=self.root/'state';self.state.mkdir();self.ledger=self.state/'openrouter-ledger.jsonl'
  self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='historic',holder_id='old',estimated_cost=104.86,at=10))+'\n');self.base=self.root/'baseline.json';self.digest=P.capture(self.ledger,self.base);self.run=self.root/'RB';(self.run/'proof').mkdir(parents=True)
  self.env=dict(BROTHER_PROOF_PHASE='RB',BROTHER_PROOF_BASELINE=str(self.base),BROTHER_PROOF_BASELINE_SHA256=self.digest,BROTHER_RUN_DIR=str(self.run),BROTHER_CODE_ROOT=str(REPO))
  self.start=dict(schema='loop-proof-start-v1',run_id='RB',attempt_id='a'*32,phase='RB',ledger_baseline=str(self.base),ledger_baseline_sha256=self.digest,ledger_path=str(self.ledger))
  self.write_start();ProofDispatch.launch(self,time.time()+86400);self.patch=mock.patch.dict(os.environ,self.env);self.patch.start();self.addCleanup(self.patch.stop)
 def write_start(self): (self.run/'proof/start.json').write_text(json.dumps(self.start))
 def launch(self,deadline,**extra):
  # the launch registry record admission reads its deadline from (proof_ledger.launch_dir), outside the run directory
  where=P.launch_dir(self.run);where.mkdir(parents=True,exist_ok=True)
  record=dict(schema='loop-proof-launch-v1',run_dir=str(self.run.resolve()),deadline_epoch=deadline);record.update(extra)
  (where/'launch.json').write_text(json.dumps(record))
 def rows(self):return [json.loads(l) for l in self.ledger.read_text().splitlines()]
 def result(self):return P.analyze(self.ledger,self.base,self.digest)
 def reserve(self,cost=1,now=86399):return L.reserve(str(self.state),20,cost,'holder',now=now,timeout_seconds=300)
 def marker(self,rid,now=86401):return L.mark_dispatched(str(self.state),rid,'holder',now=now)
 def call(self,runner=None,stderr='[billed] usd=0.25 attempts=1 known=yes'):
  runner=runner or mock.Mock(return_value=(SimpleNamespace(stderr=stderr),'fixture/model'))
  with mock.patch.object(D,'run_strict',runner):D.dispatch([], 'fixture/model',estimated_cost=1,holder_id='holder',state_root=str(self.state))
  return runner
 def test_a_stop_seen_inside_the_marker_lock_writes_no_marker_and_the_reservation_releases(self):
  # Codex check-in 2, finding 4: a TERM while mark_dispatched waited for the lock must refuse before any marker
  rid=self.reserve()
  with self.assertRaises(P.DrainRefused):L.mark_dispatched(str(self.state),rid,'holder',now=86401,stopped=lambda:True)
  self.assertFalse([r for r in self.rows() if r['type']=='DISPATCH_START'])
  L.release(str(self.state),rid,'holder');self.assertEqual(self.rows()[-1]['type'],'RELEASE')
 def test_a_marker_after_a_long_lock_wait_is_admitted_again_against_the_deadline(self):
  # Codex check-in 3, finding 1: admission ran at reservation, but the marker could wait on the lock and then start a
  # 300 s call with too little time left before the deadline
  self.launch(90000);rid=self.reserve(now=86399)
  self.assertEqual(self.rows()[-1].get('timeout_seconds'),300)
  with self.assertRaises(P.DrainRefused):L.mark_dispatched(str(self.state),rid,'holder',now=89800)
  self.assertFalse([r for r in self.rows() if r['type']=='DISPATCH_START'])
  L.mark_dispatched(str(self.state),self.reserve(now=86400),'holder',now=86401)   # control: in time, dispatched
  self.assertEqual(self.rows()[-1]['type'],'DISPATCH_START')
 def test_a_proof_reservation_with_no_recorded_timeout_is_not_dispatched(self):
  rid=self.reserve();rows=self.rows();rows[-1].pop('timeout_seconds')
  self.ledger.write_text(''.join(json.dumps(r)+'\n' for r in rows))
  with self.assertRaises(P.DrainRefused):L.mark_dispatched(str(self.state),rid,'holder',now=86401)
  self.assertFalse([r for r in self.rows() if r['type']=='DISPATCH_START'])
 def test_reservation_is_tagged(self):
  rid=self.reserve();r=self.rows()[-1];self.assertEqual((r['run_id'],r['attempt_id']),('RB','a'*32));self.assertEqual(r['reservation_id'],rid)
 def test_provider_observes_marker_before_contact(self):
  seen=[]
  def provider(*a,**k):seen.extend(self.rows());return SimpleNamespace(stderr='[billed] usd=0 attempts=1 known=yes'),'fixture/model'
  self.call(provider);self.assertEqual(seen[-1]['type'],'DISPATCH_START');self.assertEqual(seen[-1]['run_id'],'RB')
 def test_known_cost_has_provenance_and_identity(self):
  self.call();r=self.rows()[-1];self.assertEqual((r['cost_source'],r['run_id'],r['attempt_id']),('provider_usage','RB','a'*32));self.assertEqual(self.result()['runs']['RB']['measured_spend_usd'],.25)
 def test_catalog_estimate_is_not_a_measured_settlement(self):
  with mock.patch.object(D,'measured_cost',return_value=(.7,None)):self.call(stderr='[usage] prompt=1 completion=2 model=fixture/model')
  self.assertEqual(self.rows()[-1]['type'],'ABANDONED');self.assertEqual(self.result()['unknown_cost_calls'],1)
 def test_unknown_billed_attempts_cannot_be_zero_filled(self):
  with mock.patch.object(D,'measured_cost',return_value=(0,None)):self.call(stderr='[billed] usd=0 attempts=2 known=no')
  self.assertEqual(self.rows()[-1]['type'],'ABANDONED')
 def test_crash_keeps_marker_and_liability(self):
  with self.assertRaises(RuntimeError):self.call(mock.Mock(side_effect=RuntimeError('offline crash')))
  self.assertEqual([x['type'] for x in self.rows()][-2:],['DISPATCH_START','ABANDONED']);self.assertEqual(self.result()['unknown_cost_calls'],1)
 def test_marker_failure_buys_nothing_and_releases_slot(self):
  runner=mock.Mock()
  with mock.patch.object(L,'mark_dispatched',side_effect=L.LedgerError('marker refused'),create=True),mock.patch.object(D.semaphore,'release_slot',wraps=D.semaphore.release_slot) as release:
   with self.assertRaises(L.LedgerError):self.call(runner)
   runner.assert_not_called();release.assert_called_once()
 def test_unknown_liability_carries_across_midnight_in_cap(self):
  rid=self.reserve(19);self.marker(rid)
  with self.assertRaises(L.BudgetExceeded):L.reserve(str(self.state),20,2,'holder',now=172801,timeout_seconds=300)
  self.assertEqual(L.current_spend(str(self.state),now=172801),19)
 def test_abandoned_liability_carries_across_midnight(self):
  rid=self.reserve(19);self.marker(rid);L.abandon(str(self.state),rid,'crash',now=86402)
  self.assertEqual(L.spend_breakdown(str(self.state),now=172801)['abandoned'],19)
  with self.assertRaises(L.BudgetExceeded):L.reserve(str(self.state),20,2,'holder',now=172801,timeout_seconds=300)
 def test_historic_unknown_remains_separate(self):
  self.assertEqual(L.current_spend(str(self.state),now=20),0);self.assertEqual(self.result()['historic']['unknown_cost_calls'],1)
 def test_missing_baseline_refuses_before_slot(self):
  self.base.unlink()
  with mock.patch.object(D.semaphore,'acquire_slot') as slot:
   with self.assertRaises((ValueError,L.LedgerError,OSError)):self.call()
   slot.assert_not_called()
 def test_missing_run_identity_refuses(self):
  (self.run/'proof/start.json').unlink()
  with self.assertRaises((ValueError,L.LedgerError,OSError)):self.reserve()
 def test_reused_attempt_refuses(self):
  (self.run/'proof/reuse-refused.json').write_text('{}')
  with self.assertRaises((ValueError,L.LedgerError,OSError)):self.reserve()
 def test_wrong_baseline_binding_refuses(self):
  self.start['ledger_baseline_sha256']='0'*64;self.write_start()
  with self.assertRaises((ValueError,L.LedgerError,OSError)):self.reserve()
 def test_partial_configuration_cannot_fall_back_to_legacy(self):
  with mock.patch.dict(os.environ,BROTHER_PROOF_PHASE=''):
   with self.assertRaises((ValueError,L.LedgerError,OSError)):self.reserve()
 def test_duplicate_dispatch_refuses_without_appending(self):
  rid=self.reserve();self.marker(rid);before=self.ledger.read_bytes()
  with self.assertRaises(L.LedgerError):self.marker(rid)
  self.assertEqual(self.ledger.read_bytes(),before)
 def test_dispatch_checks_holder(self):
  rid=self.reserve()
  with self.assertRaises(L.LedgerError):L.mark_dispatched(str(self.state),rid,'other')
 def test_release_after_dispatch_refuses(self):
  rid=self.reserve();self.marker(rid)
  with self.assertRaises(L.LedgerError):L.release(str(self.state),rid,'holder')
 def test_marker_is_under_ledger_lock(self):
  rid=self.reserve();old=L._append_entry
  def append(root,row):
   import fcntl
   with open(self.state/'openrouter-ledger.lock','a') as lock:
    with self.assertRaises(BlockingIOError):fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
   return old(root,row)
  with mock.patch.object(L,'_append_entry',side_effect=append):self.marker(rid)
 def test_new_dispatch_of_old_reservation_enters_scope(self):
  L.mark_dispatched(str(self.state),'historic','old');self.assertEqual(self.result()['unknown_reservation_ids'],['historic'])
 def test_the_bridge_is_handed_its_reservation(self):
  # or_ask refuses a proof phase call that carries no reservation (objection 5): dispatch is the one route that has one
  runner=self.call();env=runner.call_args.kwargs.get('env') or {}
  rid=[r for r in self.rows() if r['type']=='RESERVE'][-1]['reservation_id']
  self.assertEqual(env.get('BROTHER_DISPATCH_RESERVATION'),rid)
 def test_one_run_cannot_switch_attempt_before_next_reserve(self):
  self.reserve();self.start['attempt_id']='b'*32;self.write_start();before=self.ledger.read_bytes()
  with self.assertRaises((ValueError,L.LedgerError,OSError)):self.reserve()
  self.assertEqual(self.ledger.read_bytes(),before)

if __name__=='__main__':unittest.main()
