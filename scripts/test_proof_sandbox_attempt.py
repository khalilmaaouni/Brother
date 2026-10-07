"""A sandbox observation must belong to the accepted proof attempt."""
from pathlib import Path
import hashlib,json,sys,unittest,subprocess
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parent/'loop')]
from test_proof_artifact_identity import ArtifactIdentity
class Attempt(ArtifactIdentity):
 def setUp(self):
  super().setUp();self.update('a'*32)
 def update(self,attempt):
  p=self.dirs[0]/'proof/sandbox.json';row=json.loads(p.read_text())
  if attempt is None:row.pop('attempt_id',None)
  else:row['attempt_id']=attempt
  p.write_text(json.dumps(row));self.records[0][1]['sandbox']['evidence_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.save()
 def test_matching_attempt_passes(self):self.assertEqual(self.row('sandbox'),'PASS')
 def test_other_attempt_fails(self):
  self.update('c'*32);self.assertEqual(self.row('sandbox'),'FAIL')
 def test_missing_attempt_is_not_pass(self):
  self.update(None);self.assertNotEqual(self.row('sandbox'),'PASS')
 def test_malformed_attempt_fails(self):
  self.update('bad');self.assertEqual(self.row('sandbox'),'FAIL')
 def test_start_attempt_is_also_bound(self):
  p=self.dirs[0]/'proof/start.json';row=json.loads(p.read_text());row['attempt_id']='c'*32;p.write_text(json.dumps(row));self.assertEqual(self.row('sandbox'),'FAIL')
if __name__=='__main__':
 result=unittest.TextTestRunner().run(unittest.TestSuite(Attempt(n) for n in Attempt.__dict__ if n.startswith('test_')))
 codes=[subprocess.run([sys.executable,'-B',str(Path(__file__).with_name(f))]).returncode for f in ['test_proof_accept.py','test_proof_artifact_identity.py']]
 sys.exit(not result.wasSuccessful() or any(codes))
