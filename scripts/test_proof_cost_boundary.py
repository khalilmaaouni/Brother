"""Proof costs cannot silently convert unknown observations to zero."""
import os,sys,subprocess,unittest,shutil
from pathlib import Path
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'loop'))
import loop_receipt as R
import test_loop_until_lifecycle as L

class CostBoundary(unittest.TestCase):
 def tearDown(self):
  for h in L._HOMES:shutil.rmtree(h,ignore_errors=True)
  L._HOMES.clear()
 def claude(self,rows):
  # The receipt tallies the Claude ledger itself, after the final pass (U9); the driver's figure is never read.
  import json,tempfile
  from types import SimpleNamespace
  d=tempfile.mkdtemp(prefix='cost-boundary-');self.addCleanup(shutil.rmtree,d,True);ledger=os.path.join(d,'calls.jsonl')
  with open(ledger,'w') as fh:fh.write(''.join(json.dumps(r)+'\n' for r in rows))
  a=SimpleNamespace(run_dir=d,pid='1',start='2026-01-01T00:00:00+00:00',end='2026-01-01T01:00:00+00:00',state='FINISHED',reason='fixture',
                    deadline='23:59',budget='1',spent_before='0',spent_after='0',claude_spent='0',claude_note='',log_path='',cwd=d)
  with mock.patch.dict(os.environ,BROTHER_CLAUDE_CALLS_LEDGER=ledger):
   os.environ.pop('BROTHER_PROOF_PHASE',None);return R.build_receipt(a)['claude_spend_usd']
 def test_unknown_overrides_zero_and_partial_known_total(self):
  uncosted=dict(call='u',at='2026-01-01T00:20:00+00:00',expires_at='2026-01-01T00:30:00+00:00')
  for known in ([],[dict(call='k',at='2026-01-01T00:10:00+00:00'),dict(call='k',event='done',cost_usd=1.25,at='2026-01-01T00:11:00+00:00')]):
   self.assertIn('NO-DATA',str(self.claude(known+[uncosted])))
 def test_known_zero_remains_known(self):
  self.assertEqual(self.claude([dict(call='z',at='2026-01-01T00:10:00+00:00'),dict(call='z',event='done',cost_usd=0,at='2026-01-01T00:11:00+00:00')]),0.0)
 def test_shell_missing_usd_does_not_invent_zero(self):
  line=next(s for s in (HERE/'loop/loop_until.sh').read_text().splitlines() if s.startswith('claude_spent()'))
  command='claude_tally() { echo "NOTE NO-DATA: unavailable"; }; '+line+'; claude_spent "NOTE NO-DATA: unavailable"'
  p=subprocess.run(['bash','-c',command],capture_output=True,text=True)
  self.assertEqual(p.stdout.strip(),'')
 def unreadable(self,proof):
  # ONE condition: the Claude calls ledger is a directory, so it cannot be tallied.
  h=L.make_home(lanes='1',pass_body='echo called > "$HOME/paid-pass"; exit 42')
  path=Path(h)/'bad-ledger';path.mkdir()
  extra={};script=None
  if proof:
   rd=os.path.join(h,'runs','run-RB-cost');os.makedirs(os.path.dirname(rd))
   extra=dict(BROTHER_PROOF_PHASE='RB',BROTHER_PROOF_RUN_DIR=rd,BROTHER_CODE_ROOT=L._code_root(h))
   # the proof bookkeeping (frozen manifest, observed sandbox) is accepted as given: it is not the condition here
   script=L._order_driver(h,prelude='if sys.argv[1:2] and sys.argv[1].startswith("proof-"):\n'
                                    ' import datetime; print(datetime.datetime.now().astimezone().isoformat(timespec="seconds")); sys.exit(0)\n')
  with mock.patch.dict(os.environ,BROTHER_CLAUDE_CALLS_LEDGER=str(path)):
   r=L.run(h,['tomorrow 23:59','1'],extra=extra,script=script)
  return h,r
 def test_a_proof_stops_on_an_unreadable_ledger_before_it_pays(self):
  h,r=self.unreadable(proof=True)
  self.assertEqual(r.returncode,3,r.stdout+r.stderr)
  self.assertIn('a proof accepts only known cost',r.stdout)
  self.assertFalse((Path(h)/'paid-pass').exists())
 def test_a_plain_run_reports_an_unreadable_ledger_and_continues(self):
  h,r=self.unreadable(proof=False)
  self.assertNotIn('a proof accepts only known cost',r.stdout)
  self.assertTrue((Path(h)/'paid-pass').exists(),r.stdout+r.stderr)
  receipts=list(Path(h).rglob('receipt/receipt.json'));self.assertEqual(len(receipts),1)
  import json
  self.assertIn('NO-DATA',json.loads(receipts[0].read_text())['claude_spend_usd'])
 def test_partial_usage_stops_before_more_spend(self):
  self.assertTrue(L.case_claude_calls_without_a_cost_say_no_data())
  self.assertTrue(L.case_a_done_row_with_a_null_cost_says_no_data())

if __name__=='__main__':unittest.main()
