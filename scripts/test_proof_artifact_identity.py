"""Acceptance requires the actual sandbox artifact and one fresh proof attempt."""
from pathlib import Path
import hashlib,json,sys,unittest
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'loop'))
import proof_accept as A
import loop_receipt as R
from test_proof_accept import Proof, launch_fixture

class ArtifactIdentity(Proof):
 def setUp(self):
  super().setUp()
  for d,(rec,ev) in zip(self.dirs,self.records):
   attempt='a'*32 if d.name=='RB' else 'b'*32
   rec['proof_attempt_id']=attempt;ev.update(attempt_id=attempt,work_start=rec['start'],end=rec['end'])
   start=dict(schema='loop-proof-start-v1',run_id=d.name,phase=d.name,attempt_id=attempt,start=rec['start'],work_start=rec['start'],**self.policy)
   (d/'proof/start.json').write_text(json.dumps(start))
   artifact=d/'proof/sandbox.json';raw=dict(schema='loop-sandbox-observation-v1',run_id=d.name,attempt_id=attempt,exit_code=0,observation=dict(control=True,escape_attempts=3,blocked_attempts=3))
   artifact.write_text(json.dumps(raw));ev['sandbox'].update(evidence_path=str(artifact),evidence_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
   start['sandbox']=ev['sandbox'];(d/'proof/start.json').write_text(json.dumps(start));launch_fixture(d)   # launch record follows the rewritten start (F44/F45)
  self.save()
 def save(self):
  for d,(rec,ev) in zip(self.dirs,self.records):
   (d/'receipt/receipt.json').write_text(json.dumps(rec));(d/'proof/evidence.json').write_text(json.dumps(ev))
 def row(self,name):
  return next(x['verdict'] for x in A.check_run(self.dirs[0],'RB')['checks'] if x['check']==name)
 def test_artifact_missing_is_not_pass(self):
  self.assertEqual(self.row('sandbox'),'PASS');(self.dirs[0]/'proof/sandbox.json').unlink();self.assertEqual(self.row('sandbox'),'NO-DATA')
 def test_artifact_tamper_is_not_pass(self):
  self.assertEqual(self.row('sandbox'),'PASS');p=self.dirs[0]/'proof/sandbox.json';p.write_text(p.read_text()+' ');self.assertEqual(self.row('sandbox'),'FAIL')
 def test_artifact_contents_and_run_identity_are_checked(self):
  p=self.dirs[0]/'proof/sandbox.json';raw=json.loads(p.read_text())
  for key,value in [('run_id','other'),('exit_code',1),('schema','unknown'),('observation',dict(control=False,escape_attempts=3,blocked_attempts=3)),('observation',dict(control=True,escape_attempts=2,blocked_attempts=2))]:
   edited=dict(raw,**{key:value});p.write_text(json.dumps(edited));self.records[0][1]['sandbox']['evidence_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.save();self.assertEqual(self.row('sandbox'),'FAIL',key)
 def test_referenced_artifact_must_belong_to_this_run(self):
  other=self.root/'outside.json';other.write_bytes((self.dirs[0]/'proof/sandbox.json').read_bytes());self.records[0][1]['sandbox']['evidence_path']=str(other);self.save();self.assertEqual(self.row('sandbox'),'FAIL')
 def test_stale_start_attempt_cannot_serve_old_receipt(self):
  self.assertEqual(self.row('identity'),'PASS');p=self.dirs[0]/'proof/start.json';raw=json.loads(p.read_text());raw['attempt_id']='c'*32;p.write_text(json.dumps(raw));self.assertEqual(self.row('identity'),'FAIL')
 def test_missing_start_record_is_unknown(self):
  (self.dirs[0]/'proof/start.json').unlink();self.assertEqual(self.row('identity'),'NO-DATA')
 def test_start_work_clock_and_end_bind_receipt(self):
  for key,value in [('work_start','2026-09-26T00:01:00+00:00'),('end','2026-09-26T08:01:00+00:00')]:
   old=self.records[0][1][key];self.records[0][1][key]=value;self.save();self.assertEqual(self.row('identity'),'FAIL',key);self.records[0][1][key]=old
 def test_reuse_refuses_without_overwriting_start_and_invalidates_old_attempt(self):
  p=self.dirs[0]/'proof/start.json';before=p.read_bytes()
  with mock.patch.object(R,'freeze_boundary',return_value={'verdict':'PASS'}),mock.patch.object(R,'sandbox_observation',return_value={'enforced':True}):
   with self.assertRaises((OSError,ValueError)):R.proof_start(str(self.dirs[0]),'RB','scope','manifest')
  self.assertEqual(p.read_bytes(),before);self.assertEqual(self.row('identity'),'FAIL')
 def test_writer_carries_fresh_attempt_into_receipt_and_evidence(self):
  from types import SimpleNamespace
  d=self.root/'fresh';d.mkdir()
  with mock.patch.object(R,'freeze_boundary',return_value={'verdict':'NO-DATA'}):
   started=R.proof_start(str(d),'','scope','')
   self.assertRegex(started['attempt_id'],r'^[0-9a-f]{32}$')
   work=R.proof_work_start(str(d),None);end=R.utc_now();ev=R.proof_finish(str(d),end)
   a=SimpleNamespace(run_dir=str(d),pid='1',start=work,end=end,state='FINISHED',reason='fixture',deadline='23:59',budget='1',spent_before='0',spent_after='0',claude_spent='0',claude_note='',log_path='',cwd=str(d))
   receipt=R.build_receipt(a)
  self.assertEqual(receipt['proof_attempt_id'],started['attempt_id']);self.assertEqual(ev['attempt_id'],started['attempt_id'])
 def test_receipt_names_the_same_attempt(self):
  self.records[0][0]['proof_attempt_id']='c'*32;self.save();self.assertEqual(self.row('identity'),'FAIL')

# Run only the new regression methods, not inherited proof cases.
if __name__=='__main__':
 suite=unittest.TestSuite(ArtifactIdentity(n) for n in ArtifactIdentity.__dict__ if n.startswith('test_'))
 result=unittest.TextTestRunner().run(suite);sys.exit(not result.wasSuccessful())
