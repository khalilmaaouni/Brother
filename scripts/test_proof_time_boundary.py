"""Proof windows exclude setup and observe the end before reporting."""
from pathlib import Path
from datetime import datetime,timezone
import contextlib,io,json,os,shutil,sys,tempfile,unittest
from unittest import mock
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'loop'))
import loop_receipt as R
import test_loop_until_lifecycle as L
from test_proof_accounting_admission import sandbox_fixture

class TimeBoundary(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'run';self.root.mkdir()
  self.begin='2026-09-26T00:00:00+00:00';self.work='2026-09-26T00:02:00+00:00';self.end='2026-09-26T08:02:00+00:00'
 def tearDown(self):
  for h in L._HOMES:shutil.rmtree(h,ignore_errors=True)
  L._HOMES.clear()
 def frozen(self,*args):
  return dict(schema='loop-freeze-check-v1',run_id='run',phase=args[-1],verdict='PASS',observed_at=R.utc_now(),checked_files=1,manifest_sha256='a'*64)
 def setup_start(self,deadline=None):
  import proof_ledger
  state=Path(self.tmp.name).resolve()/'state';state.mkdir();ledger=state/'openrouter-ledger.jsonl';ledger.write_text('');baseline=state/'baseline.json';digest=proof_ledger.capture(ledger,baseline)
  policy=mock.patch.dict(os.environ,BROTHER_PROOF_PHASE='RB',BROTHER_OR_STATE_ROOT=str(state),BROTHER_PROOF_BASELINE=str(baseline),BROTHER_PROOF_BASELINE_SHA256=digest);policy.start();self.addCleanup(policy.stop)
  with mock.patch.object(R,'freeze_boundary',side_effect=self.frozen),mock.patch.object(R,'sandbox_observation',side_effect=sandbox_fixture),mock.patch.object(R,'utc_now',return_value=self.begin):
   return R.proof_start(str(self.root),'RB','scope','manifest',deadline_epoch=deadline or datetime(2026,9,26,9,tzinfo=timezone.utc).timestamp())
 def test_work_clock_starts_after_setup_with_a_new_boundary(self):
  self.setup_start()
  with mock.patch.object(R,'utc_now',return_value=self.work),mock.patch.object(R,'freeze_boundary',side_effect=self.frozen):
   out=R.proof_work_start(str(self.root),datetime(2026,9,26,9,tzinfo=timezone.utc).timestamp())
  self.assertEqual(out,self.work)
  start=R.proof_read(self.root/'proof/start.json')
  self.assertEqual(start['work_start'],self.work)
  self.assertEqual(start['freeze']['start']['observed_at'],self.work)
 def test_short_deadline_refuses_before_proof_work(self):
  stop=datetime.fromisoformat(self.begin).timestamp()+28770
  err=io.StringIO()
  with mock.patch.dict(os.environ,BROTHER_PROOF_PHASE='RB'),mock.patch.object(R,'utc_now',return_value=self.begin),contextlib.redirect_stderr(err):
   try:code=R.proof_cli(['proof-start','--run-dir',str(self.root),'--deadline-epoch',str(stop)])
   except SystemExit as exc:code=exc.code
  self.assertEqual(code,2);self.assertIn('eight hours',err.getvalue())
  self.assertFalse((self.root/'proof/start.json').exists())
 def test_setup_consuming_deadline_is_refused_at_work_start(self):
  stop=datetime.fromisoformat(self.begin).timestamp()+28800+120;self.setup_start(stop)
  with mock.patch.object(R,'utc_now',return_value=self.work),mock.patch.object(R,'freeze_boundary',side_effect=self.frozen):
   with self.assertRaisesRegex(ValueError,'eight hours'):R.proof_work_start(str(self.root),stop)
 def test_end_boundary_is_observed_once_and_bound_to_supplied_end(self):
  self.setup_start()
  with mock.patch.object(R,'utc_now',return_value=self.end),mock.patch.object(R,'freeze_boundary',side_effect=self.frozen) as frozen:
   first=R.proof_finish(str(self.root),self.end)
   self.assertEqual(first['end'],self.end)
   with mock.patch.object(R,'utc_now',return_value='2026-09-26T08:10:00+00:00'):
    second=R.proof_finish(str(self.root),self.end)
   self.assertEqual(first,second);self.assertEqual(frozen.call_count,1)
  with self.assertRaises(ValueError):R.proof_finish(str(self.root),'2026-09-26T08:11:00+00:00')
 def test_driver_observes_end_before_report_work(self):
  h=L.make_home();p=Path(h)/'.claude/bin/loop_report.py'
  p.write_text('import os,pathlib\nr=pathlib.Path(os.environ["BROTHER_RUN_DIR"])/"proof/evidence.json"\n(pathlib.Path(os.environ["HOME"])/"report-saw-end").write_text(str(r.is_file()))\nprint("REPORT")\n')
  # The end report is detached and delayed by design (U12, objection 18: it never holds the driver's caller or the
  # handoff), so it runs after the driver exits: start it at once (BROTHER_REPORT_DELAY_S=0) and wait for its marker.
  # The property is unchanged: when the report's work begins, the end snapshot is already on disk.
  r=L.run(h,['23:59','1'],extra=dict(BROTHER_REPORT_DELAY_S='0'));saw=Path(h)/'report-saw-end'
  self.assertTrue(L._poll(lambda:saw.is_file() and saw.read_text()!='',20),'the end report never ran: '+r.stdout+r.stderr)
  self.assertEqual(saw.read_text(),'True',r.stdout+r.stderr)
  receipt=json.loads(next(Path(h).rglob('receipt/receipt.json')).read_text());proof=json.loads(next(Path(h).rglob('proof/evidence.json')).read_text());self.assertEqual(receipt['end'],proof['end'])
 def test_driver_receipt_start_is_after_worktree_claim(self):
  h=L.make_home();p=Path(h)/'Brother/.claude/worktrees/brother-unify-1.1/scripts/worktree_sentry.py'
  p.write_text('import os,pathlib,sys\nfrom datetime import datetime,timezone\nif sys.argv[1]=="claim":(pathlib.Path(os.environ["HOME"])/"claim-time").write_text(datetime.now(timezone.utc).isoformat())\n')
  r=L.run(h,['23:59','1']);receipt=json.loads(next(Path(h).rglob('receipt/receipt.json')).read_text())
  self.assertGreaterEqual(datetime.fromisoformat(receipt['start']),datetime.fromisoformat((Path(h)/'claim-time').read_text()),r.stdout+r.stderr)

if __name__=='__main__':unittest.main()
