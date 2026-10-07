"""Hostile admission and pair evidence are refused before proof credit."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent / 'loop'))
import loop_receipt as R
import proof_accept as A
import test_proof_accounting_admission as admission
import test_proof_accept as acceptance

class AdmissionIntegrity(unittest.TestCase):
 def setUp(self):
  self.fx=admission.Admission();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
 def test_zero_estimate_created_between_observations_refused(self):
  f=self.fx;f.start();original=R.proof_policy
  def raced(phase):
   value=original(phase)
   with f.ledger.open('a') as out:out.write(json.dumps(dict(type='RESERVE',reservation_id='race',at=86401,estimated_cost=0,run_id='prior',attempt_id='b'*32))+'\n')
   return value
  with mock.patch.object(R,'proof_policy',side_effect=raced):f.assert_work_refuses()
 def test_valid_admission_control(self):
  self.fx.start();self.assertEqual(self.fx.work(),self.fx.now)
 def test_timing_admission_control(self):
  import test_proof_time_boundary as timing
  f=timing.TimeBoundary();f.setUp();self.addCleanup(f.doCleanups);self.addCleanup(f.tearDown);f.test_work_clock_starts_after_setup_with_a_new_boundary()
 def test_failed_sandbox_refuses_work(self):
  f=self.fx;f.sandbox.side_effect=None;f.sandbox.return_value={'enforced':False};f.start();f.assert_work_refuses()
 def test_missing_sandbox_artifact_refuses_work(self):
  f=self.fx;f.start();(f.run/'proof/sandbox.json').unlink(missing_ok=True);f.assert_work_refuses()
 def test_changed_sandbox_artifact_refuses_work(self):
  f=self.fx;f.start();(f.run/'proof/sandbox.json').write_text('{}');f.assert_work_refuses()
 def test_dangling_reuse_marker_refuses_work(self):
  f=self.fx;f.start();(f.run/'proof/reuse-refused.json').symlink_to('missing');f.assert_work_refuses()

class AcceptanceIntegrity(unittest.TestCase):
 def setUp(self):
  self.fx=acceptance.Proof();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
 def result(self):
  # the launcher's pair record is required (D-12); its results follow the receipts as they now are, so only the
  # case's own hostile edit can move the verdict
  self.fx.record_results();return A.accept(*self.fx.dirs,pair=self.fx.pair)
 def test_valid_pair_control(self):self.assertEqual(self.result()['verdict'],'PASS')
 def test_duplicate_sandbox_key_refused(self):
  f=self.fx;p=f.dirs[0]/'proof/sandbox.json';raw=p.read_text().replace('"exit_code": 0','"exit_code": 7, "exit_code": 0');p.write_text(raw);f.records[0][1]['sandbox']['evidence_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();f.save();self.assertNotEqual(self.result()['verdict'],'PASS')
 def test_duplicate_receipt_key_refused(self):
  p=self.fx.dirs[0]/'receipt/receipt.json';p.write_text(p.read_text().replace('"landings": 3','"landings": 0, "landings": 3'));self.assertNotEqual(self.result()['verdict'],'PASS')
 def test_dangling_reuse_marker_refused(self):
  (self.fx.dirs[0]/'proof/reuse-refused.json').symlink_to('missing');self.assertNotEqual(self.result()['verdict'],'PASS')
 def test_pair_attempt_alias_refused(self):
  f=self.fx;attempt=f.records[0][0]['proof_attempt_id'];f.records[1][0]['proof_attempt_id']=attempt;f.records[1][1]['attempt_id']=attempt;p=f.dirs[1]/'proof/sandbox.json';record=json.loads(p.read_text());record['attempt_id']=attempt;p.write_text(json.dumps(record));f.records[1][1]['sandbox']['evidence_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();f.save();self.assertNotEqual(self.result()['verdict'],'PASS')

if __name__=='__main__':unittest.main()
