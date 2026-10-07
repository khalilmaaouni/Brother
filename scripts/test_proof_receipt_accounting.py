"""Proof receipts attribute provider cost to the bound run and baseline."""
from pathlib import Path
import json,sys,tempfile,unittest
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parent/'loop'))
import loop_receipt as R
import proof_ledger as P

class Accounting(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.ledger=self.root/'openrouter-ledger.jsonl';self.base=self.root/'baseline.json';self.run=self.root/'RB';(self.run/'proof').mkdir(parents=True)
  self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='old',estimated_cost=104.86,at=1))+'\n');self.digest=P.capture(self.ledger,self.base)
  self.start=dict(schema='loop-proof-start-v1',run_id='RB',attempt_id='a'*32,phase='RB',start='2026-01-01T00:00:00+00:00',work_start='2026-01-01T00:00:00+00:00',ledger_path=str(self.ledger),ledger_baseline=str(self.base),ledger_baseline_sha256=self.digest)
  self.save();(self.run/'proof/events.jsonl').write_text('');self.end='2026-01-01T08:00:00+00:00'
 def save(self):(self.run/'proof/start.json').write_text(json.dumps(self.start))
 def add(self,*rows):
  with self.ledger.open('a') as f:
   for row in rows:f.write(json.dumps(row)+'\n')
 def reserve(self,rid='new',run='RB',attempt='a'*32,cost=1):return dict(type='RESERVE',reservation_id=rid,estimated_cost=cost,at=86399,run_id=run,attempt_id=attempt)
 def paid(self,rid='new',run='RB',attempt='a'*32,cost=.25,provenance='provider_usage'):
  self.add(self.reserve(rid,run,attempt),dict(type='DISPATCH_START',reservation_id=rid,at=86401,run_id=run,attempt_id=attempt),dict(type='RECONCILE',reservation_id=rid,at=86402,actual_cost=cost,cost_source=provenance,run_id=run,attempt_id=attempt))
 def result(self):return R.proof_accounting(str(self.run))
 def finish(self):
  with mock.patch.object(R,'freeze_boundary',return_value={'verdict':'NO-DATA'}):return R.proof_finish(str(self.run),self.end)
 def receipt(self):
  a=SimpleNamespace(run_dir=str(self.run),pid='1',start=self.start['work_start'],end=self.end,state='FINISHED',reason='fixture',deadline='later',budget='20',spent_before='0',spent_after='999',claude_spent='0',claude_note='',log_path='',cwd=str(self.root))
  with mock.patch.object(R,'runtime_revision',return_value=('fixture',None)):return R.build_receipt(a)
 def test_per_run_measurement_excludes_other_run(self):
  self.paid();self.paid('other','RC','b'*32,10);self.assertEqual(self.result()['measured_spend_usd'],.25)
 def test_historic_disclosed_separately(self):
  x=self.result();self.assertEqual(x['historic']['reserved_liability_usd'],104.86);self.assertEqual(x['unknown_cost_calls'],0)
 def test_zero_estimate_still_unknown(self):
  self.add(self.reserve(cost=0));x=self.result();self.assertEqual(x['unknown_cost_calls'],1);self.assertEqual(x['run_unknown_cost_calls'],1)
 def test_other_run_unknown_remains_in_pair_liability(self):
  self.add(self.reserve('other','RC','b'*32));x=self.result();self.assertEqual(x['unknown_cost_calls'],1);self.assertEqual(x['run_unknown_cost_calls'],0)
 def test_midnight_does_not_drop_amount(self):
  self.add(self.reserve(),dict(type='ABANDONED',reservation_id='new',at=172801));x=self.result();self.assertEqual(x['reserved_liability_usd'],1);self.assertEqual(x['abandoned_unsettled_calls'],1)
 def test_known_zero_is_measured(self):
  self.paid(cost=0);x=self.result();self.assertEqual(x['measured_spend_usd'],0);self.assertEqual(x['run_unknown_cost_calls'],0)
 def test_catalog_price_is_unknown(self):
  self.paid(provenance='catalog_estimate');self.assertEqual(self.result()['run_unknown_cost_calls'],1)
 def test_prefix_rewrite_refuses(self):
  self.ledger.write_bytes(self.ledger.read_bytes().replace(b'104.86',b'204.86'))
  with self.assertRaises(ValueError):self.result()
 def test_missing_baseline_refuses(self):
  self.base.unlink()
  with self.assertRaises(OSError):self.result()
 def test_other_ledger_identity_refuses(self):
  p=self.root/'other.jsonl';p.write_bytes(self.ledger.read_bytes());self.start['ledger_path']=str(p);self.save()
  with self.assertRaises(ValueError):self.result()
 def test_attempt_mismatch_refuses(self):
  self.paid(attempt='b'*32)
  with self.assertRaises(ValueError):self.result()
 def test_run_identity_mismatch_refuses(self):
  self.start['run_id']='RC';self.save()
  with self.assertRaises(ValueError):self.result()
 def test_reuse_marker_refuses(self):
  (self.run/'proof/reuse-refused.json').write_text('{}')
  with self.assertRaises(ValueError):self.result()
 def test_relative_ledger_refuses(self):
  self.start['ledger_path']='openrouter-ledger.jsonl';self.save()
  with self.assertRaises(ValueError):self.result()
 def test_finish_records_accounting_and_shared_liability(self):
  self.paid();x=self.finish();self.assertEqual(x['accounting']['measured_spend_usd'],.25);self.assertEqual(x['liability']['unknown_cost_calls'],0);self.assertEqual(x['accounting']['baseline_sha256'],self.digest)
 def test_receipt_uses_end_observation_not_global_difference(self):
  self.paid();self.finish();self.paid('later','RB','a'*32,7);self.assertEqual(self.receipt()['openrouter_spend_usd'],.25)
 def test_receipt_unknown_is_not_zero(self):
  self.add(self.reserve(cost=0));self.finish();self.assertTrue(str(self.receipt()['openrouter_spend_usd']).startswith('NO-DATA:'))
 def test_receipt_without_end_observation_is_unknown(self):
  self.paid();self.assertTrue(str(self.receipt()['openrouter_spend_usd']).startswith('NO-DATA:'))
 def test_receipt_wrong_end_identity_is_unknown(self):
  self.paid();self.finish();p=self.run/'proof/evidence.json';x=json.loads(p.read_text());x['end']='2026-01-01T09:00:00+00:00';p.write_text(json.dumps(x));self.assertTrue(str(self.receipt()['openrouter_spend_usd']).startswith('NO-DATA:'))
 def test_broken_accounting_finish_says_nodata(self):
  self.base.unlink();x=self.finish();self.assertTrue(str(x['accounting']).startswith('NO-DATA:'));self.assertTrue(str(x['liability']).startswith('NO-DATA:'))

if __name__=='__main__':unittest.main()
