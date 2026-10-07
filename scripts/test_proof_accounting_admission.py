"""Proof admission pins the baseline before sandbox/setup and rechecks it."""
from pathlib import Path
from datetime import datetime
import contextlib,hashlib,io,json,os,sys,tempfile,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parent/'loop'))
import loop_receipt as R
import proof_ledger as P

def sandbox_fixture(run_dir, attempt_id):
 p=Path(run_dir)/'proof/sandbox.json'
 p.write_text(json.dumps(dict(schema='loop-sandbox-observation-v1',run_id=Path(run_dir).name,attempt_id=attempt_id,exit_code=0,observation=dict(control=True,escape_attempts=2,blocked_attempts=2))))
 return dict(enforced=True,escape_attempts=2,blocked_attempts=2,evidence_path=str(p),evidence_sha256=hashlib.sha256(p.read_bytes()).hexdigest())

class Admission(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.state=self.root/'state';self.state.mkdir();self.ledger=self.state/'openrouter-ledger.jsonl';self.ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='old',at=1,estimated_cost=104.86))+'\n');self.base=self.root/'baseline.json';self.digest=P.capture(self.ledger,self.base);self.run=self.root/'RB';self.run.mkdir()
  self.env=dict(BROTHER_PROOF_PHASE='RB',BROTHER_OR_STATE_ROOT=str(self.state),BROTHER_PROOF_BASELINE=str(self.base),BROTHER_PROOF_BASELINE_SHA256=self.digest)
  patch=mock.patch.dict(os.environ,self.env);patch.start();self.addCleanup(patch.stop)
  self.now='2026-09-26T00:00:00+00:00';self.deadline=str(datetime.fromisoformat(self.now).timestamp()+9*3600)
  self.freeze=mock.patch.object(R,'freeze_boundary',return_value={'verdict':'PASS','checked_files':1});self.freeze.start();self.addCleanup(self.freeze.stop)
  clock=mock.patch.object(R,'utc_now',return_value=self.now);clock.start();self.addCleanup(clock.stop)
  sandbox=mock.patch.object(R,'sandbox_observation',side_effect=sandbox_fixture);self.sandbox=sandbox.start();self.addCleanup(sandbox.stop)
 def start(self):return R.proof_start(str(self.run),'RB','scope','fixture',deadline_epoch=self.deadline)   # the deadline work start is checked against
 def cli(self):
  with contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(io.StringIO()):return R.proof_cli(['proof-start','--run-dir',str(self.run),'--deadline-epoch',self.deadline])
 def work(self):return R.proof_work_start(str(self.run),self.deadline)
 def change_start(self,key,value):
  p=self.run/'proof/start.json';r=json.loads(p.read_text());r[key]=value;p.write_text(json.dumps(r))
 def assert_work_refuses(self):
  with self.assertRaises((ValueError,OSError,KeyError,TypeError)):self.work()
  self.assertNotIn('work_start',json.loads((self.run/'proof/start.json').read_text()))
 def test_valid_start_pins_ledger_baseline_and_digest(self):
  r=self.start();self.assertEqual(r['ledger_path'],str(self.ledger));self.assertEqual(r['ledger_baseline'],str(self.base));self.assertEqual(r['ledger_baseline_sha256'],self.digest);self.assertEqual(r['accounting_admission'],'PASS')
 def test_valid_cli_then_work_admits(self):self.assertEqual(self.cli(),0);self.assertEqual(self.work(),self.now)
 def test_historic_liability_does_not_block_new_proof(self):self.assertEqual(self.cli(),0);self.assertEqual(self.work(),self.now)
 def test_missing_baseline_refuses_cli(self):self.base.unlink();self.assertEqual(self.cli(),2);self.sandbox.assert_not_called()
 def test_missing_state_root_refuses_cli(self):
  with mock.patch.dict(os.environ,BROTHER_OR_STATE_ROOT=''):self.assertEqual(self.cli(),2)
 def test_relative_state_root_refuses_cli(self):
  with mock.patch.dict(os.environ,BROTHER_OR_STATE_ROOT='relative'):self.assertEqual(self.cli(),2)
 def test_missing_digest_refuses_cli(self):
  with mock.patch.dict(os.environ,BROTHER_PROOF_BASELINE_SHA256=''):self.assertEqual(self.cli(),2)
 def test_relative_baseline_refuses_cli(self):
  with mock.patch.dict(os.environ,BROTHER_PROOF_BASELINE='baseline.json'):self.assertEqual(self.cli(),2)
 def test_replaced_baseline_refuses_cli(self):self.base.write_text(self.base.read_text()+' ');self.assertEqual(self.cli(),2)
 def test_rewritten_prefix_refuses_before_sandbox(self):self.ledger.write_bytes(self.ledger.read_bytes().replace(b'104.86',b'204.86'));self.assertEqual(self.cli(),2);self.sandbox.assert_not_called()
 def test_unknown_new_zero_call_refuses_cli(self):
  with self.ledger.open('a') as f:f.write(json.dumps(dict(type='RESERVE',reservation_id='zero',at=86401,estimated_cost=0,run_id='prior',attempt_id='b'*32))+'\n')
  self.assertEqual(self.cli(),2)
 def test_baseline_removed_during_setup_refuses_work(self):self.start();self.base.unlink();self.assert_work_refuses()
 def test_baseline_changed_during_setup_refuses_work(self):self.start();self.base.write_text(self.base.read_text()+' ');self.assert_work_refuses()
 def test_prefix_changed_during_setup_refuses_work(self):self.start();self.ledger.write_bytes(self.ledger.read_bytes().replace(b'104.86',b'204.86'));self.assert_work_refuses()
 def test_policy_digest_drift_refuses_work(self):
  self.start()
  with mock.patch.dict(os.environ,BROTHER_PROOF_BASELINE_SHA256='0'*64):self.assert_work_refuses()
 def test_policy_phase_drift_refuses_work(self):
  self.start()
  with mock.patch.dict(os.environ,BROTHER_PROOF_PHASE='RC'):self.assert_work_refuses()
 def test_unknown_created_during_setup_refuses_work(self):
  self.start()
  with self.ledger.open('a') as f:f.write(json.dumps(dict(type='RESERVE',reservation_id='zero',at=86401,estimated_cost=0,run_id='prior',attempt_id='b'*32))+'\n')
  self.assert_work_refuses()
 def test_bound_ledger_mismatch_refuses_work(self):self.start();self.change_start('ledger_path',str(self.root/'other.jsonl'));self.assert_work_refuses()
 def test_bound_baseline_mismatch_refuses_work(self):self.start();self.change_start('ledger_baseline_sha256','0'*64);self.assert_work_refuses()
 def test_bad_attempt_refuses_work(self):self.start();self.change_start('attempt_id','bad');self.assert_work_refuses()
 def test_sandbox_receives_actual_attempt(self):
  r=self.start();self.sandbox.assert_called_once_with(str(self.run),r['attempt_id'])
 def test_reused_run_identity_in_ledger_refuses_new_start(self):
  with self.ledger.open('a') as f:
   f.write(json.dumps(dict(type='RESERVE',reservation_id='r',at=10,estimated_cost=0,run_id='RB',attempt_id='c'*32))+'\n');f.write(json.dumps(dict(type='RELEASE',reservation_id='r',at=11))+'\n')
  self.assertEqual(self.cli(),2)

if __name__=='__main__':unittest.main()
