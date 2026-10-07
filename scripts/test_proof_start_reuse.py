"""A refused launch cannot reuse older proof before deadline validation."""
from pathlib import Path
import contextlib,io,os,sys,unittest
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'loop'))
import loop_receipt as R
from test_proof_artifact_identity import ArtifactIdentity

class ReuseAdmission(ArtifactIdentity):
 def check_refusal(self,extra):
  self.assertEqual(self.row('identity'),'PASS');p=self.dirs[0]/'proof/start.json';before=p.read_bytes()
  with mock.patch.dict(os.environ,BROTHER_PROOF_PHASE='RB'),contextlib.redirect_stderr(io.StringIO()):
   code=R.proof_cli(['proof-start','--run-dir',str(self.dirs[0])]+extra)
  self.assertEqual(code,2);self.assertEqual(p.read_bytes(),before);self.assertEqual(self.row('identity'),'FAIL')
 def test_short_deadline_still_invalidates_reuse(self):self.check_refusal(['--deadline-epoch','1'])
 def test_missing_deadline_still_invalidates_reuse(self):self.check_refusal([])

if __name__=='__main__':
 suite=unittest.TestSuite(ReuseAdmission(n) for n in ReuseAdmission.__dict__ if n.startswith('test_'))
 result=unittest.TextTestRunner().run(suite);sys.exit(not result.wasSuccessful())
