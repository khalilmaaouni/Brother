"""An immutable proof baseline cannot hide new liability or rewritten history."""
import base64,hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'loop'))
try:import proof_ledger as P
except ImportError:P=None

class Baseline(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(P,'proof ledger implementation required')
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.ledger=self.root/'openrouter-ledger.jsonl';self.base=self.root/'baseline.json'
  self.old=dict(type='RESERVE',reservation_id='historic',estimated_cost=104.86,at=100)
  self.ledger.write_text(json.dumps(self.old)+'\n');P.capture(self.ledger,self.base);self.digest=P.sha256(self.base.read_bytes())
 def add(self,*rows):
  with self.ledger.open('a') as f:
   for row in rows:f.write(json.dumps(row)+'\n')
 def reserve(self,rid='new',cost=1,at=86399,run='RB',attempt='a'*32):
  return dict(type='RESERVE',reservation_id=rid,estimated_cost=cost,at=at,run_id=run,attempt_id=attempt)
 def dispatch(self,rid='new',at=86401,run='RB',attempt='a'*32):
  return dict(type='DISPATCH_START',reservation_id=rid,at=at,run_id=run,attempt_id=attempt)
 def result(self):return P.analyze(self.ledger,self.base,self.digest)
 def test_historic_liability_disclosed_but_not_new(self):
  r=self.result();self.assertEqual(r['unknown_cost_calls'],0);self.assertEqual(r['historic']['unknown_cost_calls'],1);self.assertAlmostEqual(r['historic']['reserved_liability_usd'],104.86)
 def test_snapshot_preserves_exact_bytes(self):
  b=json.loads(self.base.read_text());self.assertEqual(base64.b64decode(b['prefix_base64']),self.ledger.read_bytes());self.assertEqual(b['prefix_sha256'],P.sha256(self.ledger.read_bytes()))
 def test_snapshot_never_overwrites(self):
  before=self.base.read_bytes()
  with self.assertRaises((OSError,ValueError)):P.capture(self.ledger,self.base)
  self.assertEqual(self.base.read_bytes(),before)
 def test_missing_ledger_not_empty(self):
  self.ledger.unlink()
  with self.assertRaises((OSError,ValueError)):self.result()
 def test_missing_baseline_unknown(self):
  self.base.unlink()
  with self.assertRaises((OSError,ValueError)):self.result()
 def test_replaced_baseline_refused(self):
  self.base.write_text(self.base.read_text()+' ')
  with self.assertRaises((OSError,ValueError)):self.result()
 def test_prefix_rewrite_and_truncate_refused(self):
  before=self.ledger.read_bytes()
  for raw in (before.replace(b'104.86',b'204.86'),before[:-1]):
   self.ledger.write_bytes(raw)
   with self.assertRaises((OSError,ValueError)):self.result()
 def test_same_bytes_other_ledger_refused(self):
  other=self.root/'other.jsonl';other.write_bytes(self.ledger.read_bytes())
  with self.assertRaises((OSError,ValueError)):P.analyze(other,self.base,self.digest)
 def test_new_unknown_and_zero_estimate_count_across_midnight(self):
  self.add(self.reserve(),self.dispatch(),dict(type='ABANDONED',reservation_id='new',at=86402),self.reserve('zero',0,172801,'RC','b'*32))
  r=self.result();self.assertEqual(r['unknown_cost_calls'],2);self.assertEqual(r['abandoned_unsettled_calls'],1);self.assertEqual(r['reserved_liability_usd'],1);self.assertEqual(r['unknown_reservation_ids'],['new','zero'])
 def test_old_reservation_dispatched_after_baseline_is_new(self):
  self.add(self.dispatch('historic'));r=self.result();self.assertEqual(r['unknown_reservation_ids'],['historic'])
 def test_provider_measured_zero_and_nonzero_kept_per_run(self):
  self.add(self.reserve(),self.dispatch(),dict(type='RECONCILE',reservation_id='new',actual_cost=0,cost_source='provider_usage',at=86402),self.reserve('second',2,172801,'RC','b'*32),self.dispatch('second',172802,'RC','b'*32),dict(type='RECONCILE',reservation_id='second',actual_cost=1.25,cost_source='provider_usage',at=172803))
  r=self.result();self.assertEqual(r['unknown_cost_calls'],0);self.assertEqual(r['runs']['RB']['measured_spend_usd'],0);self.assertEqual(r['runs']['RC']['measured_spend_usd'],1.25)
 def test_unproven_cost_stays_unknown(self):
  self.add(self.reserve(),self.dispatch(),dict(type='RECONCILE',reservation_id='new',actual_cost=0.5,cost_source='catalog_estimate',at=86402));r=self.result();self.assertEqual(r['unknown_cost_calls'],1);self.assertEqual(r['runs']['RB']['measured_spend_usd'],0)
 def test_untagged_new_activity_not_hidden(self):
  self.add(dict(self.old,reservation_id='untagged'))
  with self.assertRaises(ValueError):self.result()
 def test_duplicate_reservation_refused(self):
  self.add(self.reserve(),self.reserve())
  with self.assertRaises(ValueError):self.result()
 def test_duplicate_dispatch_refused(self):
  self.add(self.reserve(),self.dispatch(),self.dispatch())
  with self.assertRaises(ValueError):self.result()
 def test_dispatch_unknown_reservation_refused(self):
  self.add(self.dispatch())
  with self.assertRaises(ValueError):self.result()
 def test_release_after_dispatch_refused(self):
  self.add(self.reserve(),self.dispatch(),dict(type='RELEASE',reservation_id='new',at=86402))
  with self.assertRaises(ValueError):self.result()
 def test_positive_predispatch_release_clears_only_itself(self):
  self.add(self.reserve(),dict(type='RELEASE',reservation_id='new',at=86402));r=self.result();self.assertEqual(r['unknown_cost_calls'],0);self.assertEqual(r['historic']['unknown_cost_calls'],1)
 def test_wrong_attempt_and_duplicate_run_identity_refused(self):
  self.add(self.reserve(),self.dispatch(attempt='b'*32))
  with self.assertRaises(ValueError):self.result()
 def test_same_attempt_cannot_name_two_runs(self):
  self.add(self.reserve(),self.reserve('second',1,86402,'RC','a'*32))
  with self.assertRaises(ValueError):self.result()
 def test_same_attempt_on_two_runs_through_dispatch_tags_refused(self):
  # untagged reservations carry no identity, so only the DISPATCH_START tags name the run; the analyze guard, not the
  # reservation scan, must refuse one attempt on two runs (mutation survivor M-PBASE2-attempt-run, 2026-09-26)
  self.ledger.write_text('');base=self.root/'dtag.json';digest=P.capture(self.ledger,base);a='a'*32
  self.add(dict(type='RESERVE',reservation_id='r1',estimated_cost=1,at=1),dict(type='RESERVE',reservation_id='r2',estimated_cost=1,at=2),
           self.dispatch('r1',3,'RB',a),self.dispatch('r2',4,'RC',a))
  with self.assertRaises(ValueError):P.analyze(self.ledger,base,digest)
 def test_one_run_cannot_name_two_attempts(self):
  self.add(self.reserve(),self.reserve('second',1,86402,'RB','b'*32))
  with self.assertRaises(ValueError):self.result()
 def test_bad_amount_and_unknown_event_refused(self):
  before=self.ledger.read_bytes()
  for row in [self.reserve(cost=float('nan')),self.reserve(cost=True),dict(type='MYSTERY',reservation_id='new',at=1)]:
   self.ledger.write_bytes(before);self.add(row)
   with self.assertRaises(ValueError):self.result()
 def test_amount_past_float_range_is_evidence_error_not_overflow(self):
  # 10**400 is valid JSON; math.isfinite on it raised OverflowError, which no ValueError handler catches (F3).
  for bad in (10**400,-10**400):
   with self.assertRaises(P.EvidenceError):P.amount(bad)
  before=self.ledger.read_bytes()
  for row in [self.reserve(cost=10**400),self.reserve(at=10**400)]:
   self.ledger.write_bytes(before);self.add(row)
   with self.assertRaises(ValueError):self.result()
 def test_corrupt_baseline_prefix_metadata_refused_even_with_new_outer_digest(self):
  b=json.loads(self.base.read_text());b['prefix_sha256']='0'*64;self.base.write_text(json.dumps(b));self.digest=P.sha256(self.base.read_bytes())
  with self.assertRaises(ValueError):self.result()
 def test_new_settlement_without_dispatch_refused(self):
  self.add(self.reserve(),dict(type='RECONCILE',reservation_id='new',actual_cost=0,cost_source='provider_usage',at=86402))
  with self.assertRaises(ValueError):self.result()
 def test_historic_unproven_reconcile_not_settled(self):
  self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='historic',estimated_cost=104.86,at=100))+ '\n' +json.dumps(dict(type='RECONCILE',reservation_id='historic',actual_cost=0,cost_source='catalog_estimate',at=101))+ '\n' )
  base=self.root/'historic.json';digest=P.capture(self.ledger,base);r=P.analyze(self.ledger,base,digest)
  self.assertEqual(r['historic']['unknown_cost_calls'],1);self.assertEqual(r['historic']['reserved_liability_usd'],104.86)
 def test_cross_baseline_attempt_alias_refused(self):
  attempt='a'*32;self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='old',estimated_cost=1,at=1,run_id='RA',attempt_id=attempt))+ '\n' )
  base=self.root/'alias.json';digest=P.capture(self.ledger,base);self.add(self.reserve('new',1,2,'RB',attempt))
  with self.assertRaises(ValueError):P.analyze(self.ledger,base,digest)
 def test_terminal_identity_conflict_refused(self):
  self.ledger.write_text('');base=self.root/'terminal.json';digest=P.capture(self.ledger,base);a='a'*32
  self.add(self.reserve('r',1,1,'RB',a),self.dispatch('r',2,'RB',a),dict(type='RECONCILE',reservation_id='r',actual_cost=0,cost_source='provider_usage',at=3,run_id='RC',attempt_id=a))
  with self.assertRaises(ValueError):P.analyze(self.ledger,base,digest)
 def test_new_reconcile_for_old_reservation_without_dispatch_refused(self):
  self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='old',estimated_cost=100,at=1))+ '\n' )
  base=self.root/'oldres.json';digest=P.capture(self.ledger,base);self.add(dict(type='RECONCILE',reservation_id='old',actual_cost=0,cost_source='provider_usage',at=2))
  with self.assertRaises(ValueError):P.analyze(self.ledger,base,digest)
 def test_new_settlement_of_old_dispatch_is_measured(self):
  a='a'*32;self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='old',estimated_cost=100,at=1,run_id='runOld',attempt_id=a))+ '\n' +json.dumps(dict(type='DISPATCH_START',reservation_id='old',at=2,run_id='runOld',attempt_id=a))+ '\n' )
  base=self.root/'olddispatch.json';digest=P.capture(self.ledger,base);self.add(dict(type='RECONCILE',reservation_id='old',actual_cost=1.25,cost_source='provider_usage',at=3))
  r=P.analyze(self.ledger,base,digest);self.assertEqual(r['runs'].get('runOld',{}).get('measured_spend_usd'),1.25)

 def test_raw_identical_bytes_accepted(self):
  raw=self.ledger.read_bytes();r=P.analyze(self.ledger,self.base,self.digest,raw=raw)
  self.assertEqual(r['ledger_sha256'],P.sha256(raw));self.assertEqual(P.verified_prefix(self.ledger,self.base,self.digest,raw=raw)[1],raw)
 def test_raw_changed_prefix_refused(self):
  raw=self.ledger.read_bytes().replace(b'104.86',b'204.86')
  with self.assertRaises(ValueError):P.analyze(self.ledger,self.base,self.digest,raw=raw)
 def test_raw_wrong_baseline_digest_refused(self):
  raw=self.ledger.read_bytes()
  with self.assertRaises(ValueError):P.analyze(self.ledger,self.base,'0'*64,raw=raw)
 def test_raw_keeps_original_logical_ledger_identity(self):
  other=self.root/'other.jsonl';other.write_bytes(self.ledger.read_bytes())
  with self.assertRaises(ValueError):P.analyze(other,self.base,self.digest,raw=self.ledger.read_bytes())
 def test_raw_must_start_with_baseline_prefix(self):
  raw=b'{"type":"RESERVE","reservation_id":"other","estimated_cost":1,"at":1}\n'
  with self.assertRaises(ValueError):P.analyze(self.ledger,self.base,self.digest,raw=raw)
 def test_raw_not_bytes_refused(self):
  with self.assertRaises(ValueError):P.analyze(self.ledger,self.base,self.digest,raw=self.ledger.read_text())
 def test_raw_partial_trailing_line_refused(self):
  raw=self.ledger.read_bytes()+b'{"type":"RESERVE",'
  with self.assertRaises(ValueError):P.verified_prefix(self.ledger,self.base,self.digest,raw=raw)
 def test_raw_truncated_last_byte_refused(self):
  raw=self.ledger.read_bytes()[:-1]
  with self.assertRaises(ValueError):P.verified_prefix(self.ledger,self.base,self.digest,raw=raw)
 def test_raw_none_live_path_still_rejects_partial_append(self):
  with self.ledger.open('ab') as f:f.write(b'{"type":"RESERVE",')
  with self.assertRaises(ValueError):P.analyze(self.ledger,self.base,self.digest)
 def test_raw_stale_live_tail_ignored(self):
  raw=self.ledger.read_bytes()
  self.add(self.reserve('live',1,86401,'RB','c'*32))
  r=P.analyze(self.ledger,self.base,self.digest,raw=raw)
  self.assertEqual(r['ledger_sha256'],P.sha256(raw));self.assertEqual(r['unknown_cost_calls'],0)


class UnknownSplit(unittest.TestCase):
 setUp=Baseline.setUp;add=Baseline.add;reserve=Baseline.reserve;dispatch=Baseline.dispatch;result=Baseline.result
 """U6 (B5-14): analyze splits a run's unknown calls three ways, per run: in flight (no terminal row yet),
 abandoned at a bound (RESERVE bound exactly true), and abandoned unbounded. The bounded figure is reported apart
 from measured spend. One unknown shape per case."""
 def abandon(self,rid,bound,cost=.05,**extra):
  reserve=self.reserve(rid,cost)
  if bound is not None:reserve['bound']=bound
  self.add(reserve,self.dispatch(rid),dict(type='ABANDONED',reservation_id=rid,at=86402,**extra))
 def own(self):return self.result()['runs']['RB']
 def test_a_call_without_a_terminal_is_in_flight(self):
  self.add(self.reserve('open'));r=self.own()
  self.assertEqual((r['inflight_reservation_ids'],r['abandoned_bounded_usd'],r['abandoned_unbounded']),(['open'],0,0))
 def test_a_bound_abandon_is_counted_at_its_bound_apart_from_spend(self):
  self.abandon('b',True);r=self.own()
  self.assertEqual((r['inflight_reservation_ids'],r['abandoned_bounded_usd'],r['abandoned_unbounded'],r['measured_spend_usd']),([],.05,0,0))
 def test_an_unbound_abandon_is_unbounded(self):
  self.abandon('u',False);r=self.own();self.assertEqual((r['abandoned_bounded_usd'],r['abandoned_unbounded']),(0,1))
 def test_an_abandon_with_no_bound_field_is_unbounded(self):
  self.abandon('l',None);r=self.own();self.assertEqual((r['abandoned_bounded_usd'],r['abandoned_unbounded']),(0,1))
 def test_only_a_real_true_is_a_bound(self):
  self.abandon('t',1);r=self.own();self.assertEqual((r['abandoned_bounded_usd'],r['abandoned_unbounded']),(0,1))
 def test_an_unproven_settlement_is_unbounded_not_in_flight(self):
  self.add(self.reserve(),self.dispatch(),dict(type='RECONCILE',reservation_id='new',actual_cost=.5,cost_source='catalog_estimate',at=86402))
  r=self.own();self.assertEqual((r['inflight_reservation_ids'],r['abandoned_unbounded']),([],1))


class RunState(unittest.TestCase):
 """The run state helpers other lanes read: run_identity (the tags a Claude row carries) and draining (the pool and
 the driver start nothing while it is true). Each case changes ONE thing from a state that is not draining."""
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  self.run=root/'RB';(self.run/'proof').mkdir(parents=True)
  (self.run/'proof/start.json').write_text(json.dumps(dict(schema='loop-proof-start-v1',run_id='RB',attempt_id='a'*32,phase='RB')))
  self.env=dict(BROTHER_PROOF_PHASE='RB',BROTHER_PROOF_BASELINE=str(root/'b.json'),BROTHER_PROOF_BASELINE_SHA256='0'*64,BROTHER_RUN_DIR=str(self.run))
  self.now=1_000_000.0;self.deadline(self.now+3600)
 def deadline(self,value):
  where=P.launch_dir(self.run);where.mkdir(parents=True,exist_ok=True)
  (where/'launch.json').write_text(json.dumps(dict(schema='loop-proof-launch-v1',run_dir=str(self.run.resolve()),deadline_epoch=value)))
 def draining(self,**kw):return P.draining(env=self.env,now=self.now,**kw)
 def test_run_identity_reads_the_start_record(self):self.assertEqual(P.run_identity(self.env),('RB','a'*32))
 def test_run_identity_outside_a_proof_is_none(self):self.assertIsNone(P.run_identity({}))
 def test_run_identity_without_a_start_record_raises(self):
  (self.run/'proof/start.json').unlink()
  with self.assertRaises((OSError,ValueError)):P.run_identity(self.env)
 def test_run_identity_for_a_start_naming_another_run_raises(self):
  (self.run/'proof/start.json').write_text(json.dumps(dict(schema='loop-proof-start-v1',run_id='RC',attempt_id='a'*32,phase='RB')))
  with self.assertRaises(ValueError):P.run_identity(self.env)
 def test_run_identity_for_a_start_of_another_phase_raises(self):
  (self.run/'proof/start.json').write_text(json.dumps(dict(schema='loop-proof-start-v1',run_id='RB',attempt_id='a'*32,phase='RC')))
  with self.assertRaises(ValueError):P.run_identity(self.env)
 def test_run_identity_after_a_refused_reuse_raises(self):
  (self.run/'proof/reuse-refused.json').write_text('{}')
  with self.assertRaises(ValueError):P.run_identity(self.env)
 def test_a_far_deadline_is_not_draining(self):self.assertIs(self.draining(),False)
 def test_outside_a_proof_nothing_drains(self):self.assertIs(P.draining(env={},now=self.now),False)
 def test_a_deadline_closer_than_the_shortest_call_is_draining(self):
  self.deadline(self.now+300+60-1);self.assertIs(self.draining(),True)
 def test_the_shortest_call_is_a_parameter(self):
  self.deadline(self.now+100+60+1);self.assertIs(self.draining(min_timeout=100),False);self.assertIs(self.draining(),True)
 def test_an_ending_run_is_draining(self):
  (self.run/'proof/ending.json').write_text('{}');self.assertIs(self.draining(),True)
 def test_an_unreadable_launch_record_is_draining(self):
  (P.launch_dir(self.run)/'launch.json').write_text('not json');self.assertIs(self.draining(),True)

if __name__=='__main__':unittest.main()
