"""End accounting is derived from retained bytes, never from a later live tail."""
from pathlib import Path
import copy,fcntl,hashlib,json,os,subprocess,sys,threading,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parent/'loop'))
import loop_receipt as R
import proof_accept as A
import test_proof_receipt_accounting as F
import test_proof_accept as V
import proof_ledger as P
TOOL=Path(__file__).resolve().parent/'loop/loop_receipt.py'
def sha(raw):return hashlib.sha256(raw).hexdigest()

class EndWriter(unittest.TestCase):
 def setUp(self):
  self.f=F.Accounting();self.f.setUp();self.addCleanup(self.f.doCleanups)
 def unknown(self,receipt):self.assertTrue(str(receipt['openrouter_spend_usd']).startswith('NO-DATA:'),receipt['openrouter_spend_usd'])
 def test_exact_end_bytes_and_digest_retained(self):
  f=self.f;f.paid();raw=f.ledger.read_bytes();ev=f.finish();self.assertEqual((f.run/'proof/ledger-end.jsonl').read_bytes(),raw);self.assertEqual(ev['ledger_snapshot']['sha256'],hashlib.sha256(raw).hexdigest())
 def test_snapshot_records_logical_ledger_and_baseline(self):
  f=self.f;f.paid();s=f.finish()['ledger_snapshot']
  self.assertEqual(s,dict(schema='loop-ledger-end-v1',path=str(f.run.resolve()/'proof/ledger-end.jsonl'),sha256=s['sha256'],ledger_path=str(f.ledger),baseline_path=str(f.base),baseline_sha256=f.digest,run_id='RB',attempt_id='a'*32))
 def test_later_live_append_does_not_change_receipt(self):
  f=self.f;f.paid();f.finish();f.paid('later','RC','b'*32,9);self.assertEqual(f.receipt()['openrouter_spend_usd'],.25)
 def test_snapshot_missing_refuses_receipt(self):
  f=self.f;f.paid();f.finish();(f.run/'proof/ledger-end.jsonl').unlink(missing_ok=True);self.unknown(f.receipt())
 def test_snapshot_tamper_refuses_receipt(self):
  f=self.f;f.paid();f.finish();(f.run/'proof/ledger-end.jsonl').write_text('wrong');self.unknown(f.receipt())
 def test_edited_summary_cannot_change_receipt_cost(self):
  f=self.f;f.paid();f.finish();p=f.run/'proof/evidence.json';ev=json.loads(p.read_text());ev['accounting']['measured_spend_usd']=0;p.write_text(json.dumps(ev));self.unknown(f.receipt())
 def test_existing_snapshot_never_overwritten(self):
  f=self.f;p=f.run/'proof/ledger-end.jsonl';p.write_bytes(b'prior');ev=f.finish();self.assertEqual(p.read_bytes(),b'prior');self.assertIsInstance(ev['accounting'],str)
 def test_failed_capture_receipt_is_no_data(self):
  f=self.f;f.paid();(f.run/'proof/ledger-end.jsonl').write_bytes(b'prior');ev=f.finish();self.assertIsInstance(ev['ledger_snapshot'],str);self.unknown(f.receipt())
 def test_cached_finish_rejects_snapshot_tamper(self):
  f=self.f;f.paid();f.finish();(f.run/'proof/ledger-end.jsonl').write_text('wrong')
  with self.assertRaises((ValueError,OSError)):f.finish()
 def test_cached_end_reused_under_another_attempt_refused(self):
  f=self.f;f.paid();f.finish();f.start['attempt_id']='c'*32;f.save()
  with self.assertRaises(ValueError):f.finish()
  self.unknown(f.receipt())
 def test_pre_snapshot_evidence_refused_cleanly(self):
  # Evidence claiming accounting without a snapshot: the receipt writer refuses by name, never crashes.
  import contextlib,io
  f=self.f;f.paid();f.finish();p=f.run/'proof/evidence.json';ev=json.loads(p.read_text());del ev['ledger_snapshot'];p.write_text(json.dumps(ev));out=io.StringIO()
  with contextlib.redirect_stdout(out):code=R.main(['write','--run-dir',str(f.run),'--pid','1','--start',f.start['work_start'],'--end',f.end,'--state','FINISHED','--reason','fixture','--deadline','later','--budget','1','--log-path','/no/log'])
  self.assertEqual(code,1);self.assertTrue(out.getvalue().startswith('RECEIPT FAILED:'),out.getvalue())
 def test_repeated_finish_keeps_rb_end_after_rc_rows(self):
  f=self.f;f.paid();first=f.finish();f.paid('later','RC','b'*32,9);self.assertEqual(f.finish(),first)
 def test_zero_unknown_still_refuses_receipt(self):
  f=self.f;f.add(f.reserve(cost=0));f.finish();self.unknown(f.receipt())
 def test_snapshot_bytes_ignore_missing_live_tail(self):
  f=self.f;f.paid();f.finish();f.ledger.unlink();self.assertEqual(f.receipt()['openrouter_spend_usd'],.25)
 def test_snapshot_symlink_refuses_receipt(self):
  f=self.f;f.paid();f.finish();p=f.run/'proof/ledger-end.jsonl';other=f.root/'outside.jsonl';other.write_bytes(p.read_bytes());p.unlink();p.symlink_to(other);self.unknown(f.receipt())
 def test_snapshot_hash_guard_alone(self):
  # Benign byte drift (a blank line) with the accounting digest updated but not the snapshot's.
  f=self.f;f.paid();f.finish();p=f.run/'proof/ledger-end.jsonl';p.write_bytes(p.read_bytes()+b'\n')
  e=f.run/'proof/evidence.json';ev=json.loads(e.read_text());ev['accounting']['ledger_sha256']=sha(p.read_bytes());e.write_text(json.dumps(ev));self.unknown(f.receipt())
 def test_capture_waits_for_ledger_writer_lock(self):
  # A writer holding the ledger lock has written half a row. The capture must wait and
  # retain the completed bytes, with the digest and the derivation from that one read.
  f=self.f;f.paid();row=json.dumps(f.reserve('late'))+'\n';half=len(row)//2;box={}
  lock=f.ledger.with_suffix('.lock').open('a');fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
  try:
   with f.ledger.open('a') as out:out.write(row[:half])
   t=threading.Thread(target=lambda:box.update(ev=f.finish()));t.start();t.join(1.5)
   with f.ledger.open('a') as out:out.write(row[half:])
  finally:
   fcntl.flock(lock.fileno(),fcntl.LOCK_UN);lock.close()
  t.join(60);raw=f.ledger.read_bytes();ev=box['ev']
  self.assertEqual((f.run/'proof/ledger-end.jsonl').read_bytes(),raw)
  self.assertEqual(ev['ledger_snapshot']['sha256'],sha(raw));self.assertEqual(ev['accounting']['ledger_sha256'],sha(raw))
 def test_derivation_uses_the_captured_read(self):
  # A row appended after the capture but before derivation must not reach the recorded accounting.
  f=self.f;f.paid();real=R.proof_snapshot
  def capture(run_dir):
   out=real(run_dir);f.paid('after','RB','a'*32,5);return out
  with mock.patch.object(R,'proof_snapshot',side_effect=capture):ev=f.finish()
  self.assertEqual(ev['accounting']['ledger_sha256'],ev['ledger_snapshot']['sha256']);self.assertEqual(ev['accounting']['measured_spend_usd'],.25)
 def test_cli_proof_end_subprocess_retains_bytes(self):
  f=self.f;f.paid();raw=f.ledger.read_bytes()
  r=subprocess.run([sys.executable,'-B',str(TOOL),'proof-end','--run-dir',str(f.run),'--end',f.end],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertEqual((f.run/'proof/ledger-end.jsonl').read_bytes(),raw)
  ev=json.loads((f.run/'proof/evidence.json').read_text());self.assertEqual(ev['ledger_snapshot']['sha256'],sha(raw));self.assertEqual(ev['accounting']['measured_spend_usd'],.25)

class EndAcceptance(unittest.TestCase):
 def setUp(self):
  self.f=V.Proof();self.f.setUp();self.addCleanup(self.f.doCleanups)
  f=self.f;ledger=f.root/'end-test-ledger.jsonl';baseline=f.root/'end-test-baseline.json';ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='historic',estimated_cost=2,at=1))+'\n');digest=P.capture(ledger,baseline)
  policy=dict(ledger_path=str(ledger),ledger_baseline=str(baseline),ledger_baseline_sha256=digest)
  self.policies=[dict(policy),dict(policy)];self.original_save=f.save
  def save():
   self.original_save()
   for d,pol in zip(f.dirs,self.policies):
    p=d/'proof/start.json';start=json.loads(p.read_text());start.update(pol);p.write_text(json.dumps(start))
    V.launch_fixture(d)   # the launch record outside the run follows the rewritten start record (F44/F45)
  f.save=save;f.save()
  for d,(rec,ev) in zip(f.dirs,f.records):
   rows=[dict(type='RESERVE',reservation_id=d.name,at=10,estimated_cost=3,run_id=d.name,attempt_id=rec['proof_attempt_id']),dict(type='DISPATCH_START',reservation_id=d.name,at=11,run_id=d.name,attempt_id=rec['proof_attempt_id']),dict(type='RECONCILE',reservation_id=d.name,at=12,actual_cost=3,cost_source='provider_usage',run_id=d.name,attempt_id=rec['proof_attempt_id'])]
   with ledger.open('a') as out:
    for row in rows:out.write(json.dumps(row)+'\n')
   raw=ledger.read_bytes();snapshot=d/'proof/ledger-end.jsonl';snapshot.write_bytes(raw)
   ev['accounting']=R.proof_accounting(str(d));ev['ledger_snapshot']=dict(schema='loop-ledger-end-v1',path=str(snapshot.resolve()),sha256=hashlib.sha256(raw).hexdigest(),ledger_path=str(ledger),baseline_path=str(baseline),baseline_sha256=digest,run_id=d.name,attempt_id=rec['proof_attempt_id']);rec['proof_baseline_sha256']=digest
  f.save()
 def result(self):self.f.save();return A.accept(*self.f.dirs,pair=self.f.pair)
 def refuse(self):self.assertNotEqual(self.result()['verdict'],'PASS')
 def row(self,name,run=0):return next(r for r in self.result()['runs'][run]['checks'] if r['check']==name)['verdict']
 def pair(self,name):return next(r for r in self.result()['pair_checks'] if r['check']==name)['verdict']
 def snap(self,**changes):self.f.records[0][1]['ledger_snapshot'].update(changes);self.assertEqual(self.row('accounting_snapshot'),'FAIL')
 def test_existing_positive_fixture_has_derived_accounting(self):
  f=self.f;self.assertEqual(self.result()['verdict'],'PASS');self.assertIn('accounting_snapshot',[r['check'] for r in self.result()['runs'][0]['checks']])
 def test_repository_fixture_contains_complete_accounting(self):
  f=V.Proof();f.setUp()
  try:self.assertIn('ledger_snapshot',f.records[0][1]);self.assertEqual(A.accept(*f.dirs,pair=f.pair)['verdict'],'PASS')
  finally:f.doCleanups()
 def test_artifact_fixture_preserves_accounting_identity(self):
  import test_proof_artifact_identity as artifact
  f=artifact.ArtifactIdentity();f.setUp()
  try:self.assertIn('ledger_snapshot',f.records[0][1]);self.assertEqual(A.accept(*f.dirs,pair=f.pair)['verdict'],'PASS')
  finally:f.doCleanups()
 def test_changed_prefix_with_matching_snapshot_hash_refused(self):
  f=self.f;p=f.dirs[0]/'proof/ledger-end.jsonl';p.write_bytes(p.read_bytes().replace(b'"estimated_cost": 2',b'"estimated_cost": 4'));f.records[0][1]['ledger_snapshot']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.refuse()
 def test_hidden_zero_unknown_in_snapshot_refused(self):
  f=self.f;p=f.dirs[0]/'proof/ledger-end.jsonl'
  with p.open('a') as out:out.write(json.dumps(dict(type='RESERVE',reservation_id='hidden',at=15,estimated_cost=0,run_id='RB',attempt_id='a'*32))+'\n')
  f.records[0][1]['ledger_snapshot']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.refuse()
 def test_liability_summary_cannot_hide_snapshot_unknown(self):
  # Snapshot, digest and accounting all agree on one unknown call; only the liability claims zero.
  f=self.f;p=f.dirs[0]/'proof/ledger-end.jsonl';ev=f.records[0][1]
  with p.open('a') as out:out.write(json.dumps(dict(type='RESERVE',reservation_id='hidden',at=15,estimated_cost=0,run_id='RB',attempt_id='a'*32))+'\n')
  ev['ledger_snapshot']['sha256']=sha(p.read_bytes());ev['accounting']=R.proof_accounting(str(f.dirs[0]),raw=p.read_bytes())
  self.assertEqual(ev['accounting']['unknown_cost_calls'],1);self.assertEqual(self.row('accounting_snapshot'),'FAIL')
 def test_different_valid_rc_baseline_refused(self):
  f=self.f;ev=f.records[1][1];snap=ev['ledger_snapshot'];ledger=Path(snap['ledger_path']);baseline=f.root/'alternate-baseline.json';digest=P.capture(ledger,baseline);self.policies[1].update(ledger_baseline=str(baseline),ledger_baseline_sha256=digest);f.save();snap.update(baseline_path=str(baseline),baseline_sha256=digest);ev['accounting']=R.proof_accounting(str(f.dirs[1]));f.records[1][0].update(proof_baseline_sha256=digest,openrouter_spend_usd=0);self.refuse()
  self.assertEqual(self.row('accounting_snapshot',1),'PASS');self.assertEqual(self.pair('same_accounting_baseline'),'FAIL')
 def test_rc_snapshot_that_lost_rb_rows_refused(self):
  # The ledger is append-only across the pair, so RB's retained end bytes are a prefix of RC's. An RC snapshot with
  # RB's rows removed, re-hashed and re-derived, validates on its own; only the pair can see RB's spend was forgotten.
  f=self.f;ev=f.records[1][1];snap=ev['ledger_snapshot'];p=f.dirs[1]/'proof/ledger-end.jsonl'
  kept=[l for l in p.read_bytes().splitlines(keepends=True) if b'"reservation_id": "RB"' not in l];raw=b''.join(kept);p.write_bytes(raw)
  snap['sha256']=hashlib.sha256(raw).hexdigest();ev['accounting']=R.proof_accounting(str(f.dirs[1]),raw=raw)
  self.assertEqual(self.row('accounting_snapshot',1),'PASS');self.assertEqual(self.pair('rc_extends_rb'),'FAIL');self.refuse()
 def test_rc_extends_rb_on_the_honest_pair(self):self.assertEqual(self.pair('rc_extends_rb'),'PASS')
 def test_rc_extends_rb_missing_snapshot_is_no_data(self):
  (self.f.dirs[1]/'proof/ledger-end.jsonl').unlink();self.assertEqual(self.pair('rc_extends_rb'),'NO-DATA')
 def test_receipt_cost_cannot_be_substituted(self):self.f.records[0][0]['openrouter_spend_usd']=0;self.refuse()
 def test_other_run_accounting_substituted_refused(self):
  f=self.f;f.records[0][1]['accounting']=copy.deepcopy(f.records[1][1]['accounting']);self.assertEqual(self.row('accounting_snapshot'),'FAIL')
 def test_accounting_summary_cannot_be_substituted(self):
  self.f.records[0][1].setdefault('accounting',{})['measured_spend_usd']=0;self.refuse()
 def test_snapshot_absence_refused(self):
  (self.f.dirs[0]/'proof/ledger-end.jsonl').unlink(missing_ok=True);self.refuse()
 def test_snapshot_record_absent_is_no_data(self):
  self.f.records[0][1]['ledger_snapshot']='NO-DATA: capture failed'
  row=next(r for r in self.result()['runs'][0]['checks'] if r['check']=='accounting_snapshot')
  self.assertEqual(row['verdict'],'NO-DATA');self.assertIn('capture failed',row['reason'])
 def test_snapshot_byte_drift_refused(self):
  (self.f.dirs[0]/'proof/ledger-end.jsonl').write_text('wrong');self.refuse()
 def test_wrong_logical_identity_refused(self):
  self.f.records[0][1].setdefault('ledger_snapshot',{})['ledger_path']=str(self.f.root/'other');self.refuse()
 def test_wrong_baseline_digest_refused(self):
  self.f.records[0][0]['proof_baseline_sha256']='0'*64;self.refuse()
 def test_wrong_snapshot_attempt_refused(self):
  self.f.records[0][1].setdefault('ledger_snapshot',{})['attempt_id']='f'*32;self.refuse()
 def test_snapshot_outside_run_refused(self):
  self.f.records[0][1].setdefault('ledger_snapshot',{})['path']=str(self.f.root/'other.jsonl');self.refuse()
 def test_snapshot_hash_drift_refused(self):
  self.f.records[0][1].setdefault('ledger_snapshot',{})['sha256']='0'*64;self.refuse()
 def test_snapshot_identity_fields_each_bound(self):
  for key,value in [('schema','loop-ledger-end-v0'),('baseline_path',str(self.f.root/'other-baseline.json')),('baseline_sha256','0'*64),('run_id','RC')]:
   with self.subTest(key=key):
    old=self.f.records[0][1]['ledger_snapshot'][key];self.snap(**{key:value});self.f.records[0][1]['ledger_snapshot'][key]=old
 def test_live_tail_after_rc_does_not_change_rb(self):
  f=self.f;ledger=Path(f.records[0][1]['ledger_snapshot']['ledger_path'])
  with ledger.open('a') as out:out.write(json.dumps(dict(type='RESERVE',reservation_id='after',at=20,estimated_cost=0,run_id='RB',attempt_id='a'*32))+'\n')
  self.assertEqual(self.result()['verdict'],'PASS')

if __name__=='__main__':unittest.main()
