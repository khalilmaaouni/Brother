"""M1-1 (B5-13, B5-18, objection 8): a proof call is admitted at REGISTRATION, under the ledger lock, only while the
run is not ending, the registering module sits inside the frozen code root, and the call's own timeout ends a margin
before the run's deadline. Anything else is refused before any ledger row exists and before the bridge is run.

Entry point: openrouter_dispatch.dispatch with run_strict replaced, a real ledger in a scratch state root and the real
proof start fixture (test_proof_dispatch_accounting.ProofDispatch, which also writes the launch record carrying the
deadline). Each refusal case changes ONE condition from the state test_a_call_ending_before_the_deadline_is_admitted
proves admits.

Design note, measured: M1-1 names timeouts of 300 and 30 s. The dispatcher refuses any timeout under 300 s
(openrouter_strict.MIN_TIMEOUT_SECONDS) before admission is reached, so both sides of the deadline boundary are driven
here with the 300 s floor and the deadline moved instead; test_a_sub_floor_timeout_never_reaches_admission records it.
"""
import json,os,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent)]
import test_proof_dispatch_accounting as F   # a module, so its ProofDispatch tests are not collected here again
from scripts.loop import proof_ledger as P
from plugin.runtime.brother.core import openrouter_ledger as L, openrouter_dispatch as D
from plugin.runtime.brother.core.openrouter_strict import TimeoutTooLow

MARGIN=60      # proof_ledger's drain margin: a call must end this many seconds before the deadline
TIMEOUT=300


class Admission(unittest.TestCase):
 setUp=F.ProofDispatch.setUp
 write_start=F.ProofDispatch.write_start
 launch=F.ProofDispatch.launch
 rows=F.ProofDispatch.rows
 def runner(self):return mock.Mock(return_value=(SimpleNamespace(stderr='[billed] usd=0.25 attempts=1 known=yes'),'fixture/model'))
 def dispatch(self,runner,timeout=TIMEOUT):
  with mock.patch.object(D,'run_strict',runner):
   return D.dispatch([],'fixture/model',estimated_cost=1,holder_id='holder',state_root=str(self.state),timeout_seconds=timeout)
 def refused(self,exc,*words):
  before=self.ledger.read_bytes();runner=self.runner()
  with self.assertRaises(exc) as caught:self.dispatch(runner)
  runner.assert_not_called();self.assertEqual(self.ledger.read_bytes(),before,'a refused call left a ledger row')
  for word in words:self.assertIn(word,str(caught.exception))
  return caught.exception

 def test_a_call_ending_before_the_deadline_is_admitted(self):
  self.launch(time.time()+TIMEOUT+MARGIN+120);runner=self.runner();self.dispatch(runner)
  runner.assert_called_once();self.assertEqual([r['type'] for r in self.rows()][1:],['RESERVE','DISPATCH_START','RECONCILE'])
 def test_a_call_that_would_outlive_the_deadline_is_refused_before_any_row(self):
  self.launch(time.time()+TIMEOUT+MARGIN-30);self.refused(P.DrainRefused,'deadline')
 def test_an_ending_run_refuses_every_registration(self):
  (self.run/'proof/ending.json').write_text('{}');self.refused(P.DrainRefused,'ending')
 def test_a_module_outside_the_code_root_is_refused_naming_the_root(self):
  other=tempfile.mkdtemp(dir=str(self.root))
  with mock.patch.dict(os.environ,BROTHER_CODE_ROOT=other):self.refused(P.DrainRefused,os.path.realpath(other))
 def test_no_code_root_is_evidence_error(self):
  with mock.patch.dict(os.environ,BROTHER_CODE_ROOT=''):self.refused(P.EvidenceError,'BROTHER_CODE_ROOT')
 def test_an_unreadable_launch_record_is_evidence_error(self):
  (P.launch_dir(self.run)/'launch.json').write_text('not json');self.refused(P.EvidenceError)
 def test_a_launch_record_without_a_deadline_is_evidence_error(self):
  self.launch(None);self.refused(P.EvidenceError,'deadline')
 def test_a_launch_record_for_another_run_is_evidence_error(self):
  self.launch(time.time()+86400,run_dir=str(self.root/'elsewhere'));self.refused(P.EvidenceError)
 def test_a_registration_without_a_timeout_is_refused(self):
  before=self.ledger.read_bytes()
  with self.assertRaises(P.EvidenceError):L.reserve(str(self.state),20,1,'holder')
  self.assertEqual(self.ledger.read_bytes(),before)
 def test_the_run_after_its_end_snapshot_registers_nothing(self):
  (self.run/'proof/ledger-end.jsonl').write_text('')
  with mock.patch.object(D.semaphore,'acquire_slot') as slot:
   before=self.ledger.read_bytes()
   with self.assertRaises(P.EvidenceError):self.dispatch(self.runner())
   slot.assert_not_called();self.assertEqual(self.ledger.read_bytes(),before)
 def test_an_ending_after_the_reservation_releases_it_before_provider_contact(self):
  real=L.reserve
  def then_end(*a,**k):
   rid=real(*a,**k);(self.run/'proof/ending.json').write_text('{}');return rid
  runner=self.runner()
  with mock.patch.object(L,'reserve',side_effect=then_end):
   with self.assertRaises(P.DrainRefused):self.dispatch(runner)
  runner.assert_not_called();self.assertEqual([r['type'] for r in self.rows()][1:],['RESERVE','RELEASE'])
  self.assertEqual(P.analyze(self.ledger,self.base,self.digest)['unknown_cost_calls'],0)
 def test_a_sub_floor_timeout_never_reaches_admission(self):
  with self.assertRaises(TimeoutTooLow):self.dispatch(self.runner(),timeout=30)


class DrainAgreesWithAdmission(unittest.TestCase):
 """proof_ledger.draining() is what the pool and the driver ask before starting work (S4): it must say draining
 exactly when admission would refuse the shortest call, and never outside a proof phase."""
 setUp=F.ProofDispatch.setUp
 write_start=F.ProofDispatch.write_start
 launch=F.ProofDispatch.launch
 def test_outside_a_proof_nothing_drains(self):
  self.assertIs(P.draining(env={},now=time.time()),False)
 def test_a_deadline_that_admits_the_shortest_call_is_not_draining(self):
  now=time.time();self.launch(now+TIMEOUT+MARGIN+30)
  self.assertIs(P.draining(now=now,min_timeout=TIMEOUT),False)
  P.admit_locked(self.run,TIMEOUT,D.ledger.__file__,now=now)   # admitted, no exception
 def test_a_deadline_that_refuses_the_shortest_call_is_draining(self):
  now=time.time();self.launch(now+TIMEOUT+MARGIN-30)
  self.assertIs(P.draining(now=now,min_timeout=TIMEOUT),True)
  with self.assertRaises(P.DrainRefused):P.admit_locked(self.run,TIMEOUT,D.ledger.__file__,now=now)


class DeployedFlatToolsAreInsideTheFrozenRoot(unittest.TestCase):
 """Codex check-in 4, finding 1: the deploy stages the code root as <bin>/candidate while the flat tools the driver
 runs sit directly in <bin>, and admission refused every flat tool (model_call's claude_ledger) as outside the root."""
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);b=Path(self.tmp.name)/'bin'
  (b/'candidate'/'scripts'/'loop').mkdir(parents=True);(b/'other').mkdir();self.bin=b
  for f in (b/'claude_ledger.py',b/'candidate'/'scripts'/'loop'/'claude_ledger.py',b/'other'/'x.py'):f.write_text('')
  self.run=Path(self.tmp.name)/'RB'
 def admit(self,module,root):
  with mock.patch.object(P,'_deadline',return_value=10**9):
   return P.admit_locked(str(self.run),300,str(module),env={'BROTHER_CODE_ROOT':str(root)},now=0)
 def test_a_flat_tool_beside_the_staged_candidate_is_admitted(self):
  self.admit(self.bin/'claude_ledger.py',self.bin/'candidate')
 def test_code_inside_the_candidate_is_admitted(self):
  self.admit(self.bin/'candidate'/'scripts'/'loop'/'claude_ledger.py',self.bin/'candidate')
 def test_a_module_in_another_bin_subdirectory_is_refused(self):
  with self.assertRaises(P.DrainRefused):self.admit(self.bin/'other'/'x.py',self.bin/'candidate')
 def test_a_sibling_of_a_code_root_that_is_not_the_staged_candidate_is_refused(self):
  with self.assertRaises(P.DrainRefused):self.admit(self.bin/'claude_ledger.py',self.bin/'other')

if __name__=='__main__':unittest.main()
