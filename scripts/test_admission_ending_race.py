"""M1-2 (objection 8): admission and the run's ending are serialized by the ledger lock, so every paid call is either
registered before the ending marker (and settle sees it) or refused. Never a RESERVE that lands after the end.

Entry points: openrouter_dispatch.dispatch (thread A) and proof_ledger.mark_ending (thread B) on a real ledger in a
scratch state root with the real proof start fixture. Each case isolates one ordering.
"""
import fcntl,json,os,sys,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent)]
import test_proof_dispatch_accounting as F   # a module, so its ProofDispatch tests are not collected here again
from scripts.loop import proof_ledger as P
from plugin.runtime.brother.core import openrouter_ledger as L, openrouter_dispatch as D

BILLED='[billed] usd=0.25 attempts=1 known=yes'


class Race(unittest.TestCase):
 setUp=F.ProofDispatch.setUp
 write_start=F.ProofDispatch.write_start
 launch=F.ProofDispatch.launch
 rows=F.ProofDispatch.rows
 def claude(self):return self.root/'claude-calls.jsonl'
 def end(self):return P.mark_ending(self.run,self.state,self.claude(),'DEADLINE')
 def inflight(self):
  return P.analyze(self.ledger,self.base,self.digest)['runs'].get('RB',{}).get('inflight_reservation_ids',[])
 def new_types(self):return [r['type'] for r in self.rows()][1:]
 def a(self,box):
  try:D.dispatch([],'fixture/model',estimated_cost=1,holder_id='holder',state_root=str(self.state))
  except BaseException as exc:box['exc']=exc
 def start_a(self,box):
  t=threading.Thread(target=self.a,args=(box,));t.start();self.addCleanup(t.join,10);return t

 def test_a_call_held_before_registration_is_refused_once_the_run_ends(self):
  at_barrier,resume,box=threading.Event(),threading.Event(),{}
  real=D.semaphore.acquire_slot
  def held(*a,**k):
   slot=real(*a,**k);at_barrier.set();resume.wait(10);return slot
  runner=mock.Mock(return_value=(SimpleNamespace(stderr=BILLED),'fixture/model'))
  with mock.patch.object(D.semaphore,'acquire_slot',held),mock.patch.object(D,'run_strict',runner):
   t=self.start_a(box);self.assertTrue(at_barrier.wait(10))
   self.end();resume.set();t.join(10)
  self.assertIsInstance(box.get('exc'),P.DrainRefused,box);runner.assert_not_called()
  self.assertEqual(self.new_types(),[]);self.assertEqual(self.inflight(),[])

 def test_a_call_registered_before_the_end_is_listed_inflight_and_still_settles(self):
  in_call,finish,box=threading.Event(),threading.Event(),{}
  def provider(*a,**k):
   in_call.set();finish.wait(10);return SimpleNamespace(stderr=BILLED),'fixture/model'
  with mock.patch.object(D,'run_strict',side_effect=provider):
   t=self.start_a(box);self.assertTrue(in_call.wait(10))
   self.end();rid=[r for r in self.rows() if r['type']=='RESERVE'][-1]['reservation_id']
   self.assertEqual(self.inflight(),[rid],'a call registered before the end must be visible to settle')
   finish.set();t.join(10)
  self.assertNotIn('exc',box);self.assertEqual(self.new_types(),['RESERVE','DISPATCH_START','RECONCILE']);self.assertEqual(self.inflight(),[])

 def test_the_ending_marker_waits_for_a_registration_holding_the_ledger_lock(self):
  inside,box=threading.Event(),{}
  real=L._append_entry
  def slow(root,row):
   if row.get('type')=='RESERVE':inside.set();time.sleep(.5)
   return real(root,row)
  with mock.patch.object(L,'_append_entry',side_effect=slow),mock.patch.object(D,'run_strict',return_value=(SimpleNamespace(stderr=BILLED),'fixture/model')):
   t=self.start_a(box);self.assertTrue(inside.wait(10))
   self.end()
   self.assertIn('RESERVE',self.new_types(),'the ending marker was written while a registration held the ledger lock')
   t.join(10)

 def test_the_ending_marker_waits_for_the_claude_ledger_lock(self):
  # the Claude ledger's lock is <ledger>.lock beside it, the same convention as the OpenRouter ledger's
  with open(str(self.claude().with_suffix('.lock')),'a') as held:
   fcntl.flock(held.fileno(),fcntl.LOCK_EX)
   t=threading.Thread(target=self.end);t.start();t.join(.5)
   self.assertTrue(t.is_alive(),'mark_ending did not wait for the Claude ledger lock')
   self.assertFalse(os.path.lexists(str(self.run/'proof/ending.json')))
   fcntl.flock(held.fileno(),fcntl.LOCK_UN)
  t.join(10);self.assertTrue(os.path.lexists(str(self.run/'proof/ending.json')))

 def test_the_ending_marker_is_written_once_and_names_the_state(self):
  self.end();path=self.run/'proof/ending.json';first=path.read_bytes()
  self.assertEqual(json.loads(first)['state'],'DEADLINE')
  with self.assertRaises(FileExistsError):P.mark_ending(self.run,self.state,self.claude(),'UNFUNDED')
  self.assertEqual(path.read_bytes(),first)


if __name__=='__main__':unittest.main()
