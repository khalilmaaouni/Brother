#!/usr/bin/env python3
"""Proof acceptance never turns missing observations into success."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL=Path(__file__).resolve().parent/'loop/proof_accept.py'
sys.path.insert(0,str(Path(__file__).resolve().parent/'loop'))   # named so the deploy parity check can read it (F47)
import proof_ledger
import loop_receipt
import proof_accept
LAUNCH_BOARD=TOOL.parents[2]/'docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json'

def launch_fixture(d):
    """What proof-start and proof-work-start record outside the run directory (D-12), kept in
    step with the fixture's start record. Refusal records are never removed here."""
    import proof_launch, proof_ledger
    where=proof_ledger.launch_dir(d)
    for name in ('launch.json','work-start.json'):
        try:(where/name).unlink()
        except FileNotFoundError:pass
    start=json.loads((d/'proof/start.json').read_text())
    proof_launch.record(d,start);proof_launch.admit(d,start['attempt_id'],start['work_start'])

def sha(raw):return hashlib.sha256(raw).hexdigest()

def iso(hour,minute=0):return '2026-09-26T%02d:%02d:00+00:00'%(hour,minute)

def jsonl(rows):return b''.join((json.dumps(r,sort_keys=True)+'\n').encode() for r in rows)

def landing_row(commit,at,verdict='LANDED',remote_sha=None,fetched=True):
    """A loop-landing-v1 row as land_batch.record_landing writes it (U10)."""
    return dict(schema='loop-landing-v1',commit=commit,subs=['U1.a'],builds=['/b.json'],verdict=verdict,remote_ref='hub/main',
                remote_sha=(remote_sha or commit) if fetched else None,fetched_at=at if fetched else None,at=at)

def bodies(commits):
    """The raw `git log --format=%H%x00%B <it>..<last>` of each landing in `commits`, every later landing, newest
    first; the last one's is empty. What git_log hands land_batch's writer."""
    return {c:b''.join(('%s\x00landing %s\n\n'%(later,later[-4:])).encode() for later in reversed(commits[i+1:]))
            for i,c in enumerate(commits)}

def chain_logs(commits,extra=None):
    """The retained histories an honest lander leaves after landing `commits` in order and refreshing at each fetch:
    each landing's log body (bodies), then `extra[commit]` when given, then the range line land_batch.write_history
    ends every history with (proof_accept.history_end, X1 finding 1). A history that stops short of the last fetch is
    what a failed refresh leaves (finding B2, 2026-09-27); one cut short has lost its range line."""
    import proof_accept
    return {c:raw+(extra or {}).get(c,b'')+proof_accept.history_end(c,commits[-1]) for c,raw in bodies(commits).items()}

def retain_landings(d,rows,logs):
    """Write proof/landings.jsonl and proof/landing-history/<commit>.log; the end record proof-end captures of them."""
    proof=d/'proof';hist=proof/'landing-history';hist.mkdir(exist_ok=True)
    for old in hist.iterdir():old.unlink()
    for commit,raw in logs.items():(hist/(commit+'.log')).write_bytes(raw)
    path=proof/'landings.jsonl';raw=jsonl(rows);path.write_bytes(raw)
    return dict(schema='loop-landings-end-v1',path=str(path.resolve()),sha256=sha(raw),absent=False,
                history={p.name:sha(p.read_bytes()) for p in sorted(hist.iterdir())})

def claude_rows(run,attempt,hour,cost=1,call=None):
    call=call or run+'-%d'%hour
    return [dict(call=call,at=iso(hour),expires_at=iso(hour,30),run=run,attempt=attempt),
            dict(call=call,event='done',cost_usd=cost,at=iso(hour,1),run=run,attempt=attempt)]

class Proof(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.dirs=[self.root/'RB',self.root/'RC'];self.records=[]
        # One shared ledger and pinned baseline; each run retains its own end snapshot.
        ledger=self.root/'openrouter-ledger.jsonl';baseline=self.root/'baseline.json'
        ledger.write_text(json.dumps(dict(type='RESERVE',reservation_id='historic',at=1,estimated_cost=2))+'\n')
        digest=proof_ledger.capture(ledger,baseline)
        # The pair's frozen manifest (acceptance reads its environment for the rehearsal knob) and its Claude ledger.
        self.manifest=self.root/'manifest.json';self.manifest.write_text(json.dumps({'environment':{'BROTHER_SCOPE':'f'*64}}))
        self.manifest_sha=sha(self.manifest.read_bytes())
        self.claude=self.root/'pair-claude-calls.jsonl';self.claude.write_bytes(b'')
        self.policy=dict(ledger_path=str(ledger),ledger_baseline=str(baseline),ledger_baseline_sha256=digest,
                         manifest=str(self.manifest),claude_ledger_path=str(self.claude))
        for i,d in enumerate(self.dirs):
            (d/'receipt').mkdir(parents=True);(d/'proof').mkdir()
            start='2026-09-26T%02d:00:00+00:00'%(i*8);end='2026-09-26T%02d:00:00+00:00'%((i+1)*8)
            log=d/'driver.log';log.write_text('fixture unattended log\n')
            rec={'run_id':d.name,'start':start,'end':end,'end_state':'COMPLETE','openrouter_spend_usd':3,'claude_spend_usd':1,'landings':3,'driver_log_path':str(log),'driver_log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'runtime_revision':'a'*40}
            evidence={'schema':'loop-proof-v1','run_id':d.name,'phase':d.name,'unattended':True,'interventions':[], 'scope':proof_accept.SCOPE, 'liability':{'unknown_cost_calls':0,'abandoned_unsettled_calls':0,'reserved_liability_usd':0},'sandbox':{'enforced':True,'escape_attempts':3,'blocked_attempts':3,'evidence_sha256':'c'*64},'freeze':{}}
            for phase,at in [('start',start),('end',end)]:
                evidence['freeze'][phase]={'schema':'loop-freeze-check-v1','run_id':d.name,'phase':phase,'observed_at':at,'verdict':'PASS','manifest_sha256':self.manifest_sha,'checked_files':8}
            attempt = ('a' if i == 0 else 'b') * 32
            rec['proof_attempt_id'] = attempt
            evidence.update(attempt_id=attempt, work_start=start, end=end)
            artifact = d/'proof/sandbox.json'
            artifact.write_text(json.dumps(dict(schema='loop-sandbox-observation-v1',run_id=d.name,attempt_id=attempt,
                exit_code=0,observation=dict(control=True,escape_attempts=3,blocked_attempts=3))))
            evidence['sandbox'].update(evidence_path=str(artifact), evidence_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
            (d/'proof/start.json').write_text(json.dumps(dict(schema='loop-proof-start-v1',run_id=d.name,
                phase=d.name,attempt_id=attempt,start=start,work_start=start,**self.policy)))
            rows=[dict(type='RESERVE',reservation_id=d.name,at=10,estimated_cost=3,run_id=d.name,attempt_id=attempt),
                  dict(type='DISPATCH_START',reservation_id=d.name,at=11,run_id=d.name,attempt_id=attempt),
                  dict(type='RECONCILE',reservation_id=d.name,at=12,actual_cost=3,cost_source='provider_usage',run_id=d.name,attempt_id=attempt)]
            with ledger.open('a') as stream:
                for row in rows:stream.write(json.dumps(row)+'\n')
            raw=ledger.read_bytes();snapshot=d/'proof/ledger-end.jsonl';snapshot.write_bytes(raw)
            evidence['accounting']=loop_receipt.proof_accounting(str(d),raw=raw)
            evidence['ledger_snapshot']=dict(schema='loop-ledger-end-v1',path=str(snapshot.resolve()),sha256=hashlib.sha256(raw).hexdigest(),
                ledger_path=str(ledger),baseline_path=str(baseline),baseline_sha256=digest,run_id=d.name,attempt_id=attempt)
            rec['proof_baseline_sha256']=digest
            # The intervention history between its two markers, retained with its digest (U11, objection 14).
            events=jsonl([dict(kind='start-marker',detail='',observed_at=start),dict(kind='end-marker',detail='',observed_at=end)])
            (d/'proof/events.jsonl').write_bytes(events);evidence['history_sha256']=sha(events)
            # The pair Claude ledger: each run's end snapshot is the ledger as it stood at that run's end (U9).
            with self.claude.open('ab') as out:out.write(jsonl(claude_rows(d.name,attempt,i*8+1)))
            raw=self.claude.read_bytes();(d/'proof/claude-end.jsonl').write_bytes(raw)
            evidence['claude_snapshot']=dict(schema='loop-claude-end-v1',path=str((d/'proof/claude-end.jsonl').resolve()),sha256=sha(raw),
                ledger_path=str(self.claude),run_id=d.name,attempt_id=attempt)
            # Three landings this run made, each observed on the remote with nothing after it (U10).
            commits=['%040x'%(16*i+n+1) for n in range(3)]
            evidence['landings_record']=retain_landings(d,[landing_row(c,iso(i*8+2,n)) for n,c in enumerate(commits)],chain_logs(commits))
            self.records.append((rec,evidence))
        self.pair=self.root/'pair-x';self.pair.mkdir()
        import proof_launch
        proof_launch.write_pair(self.pair,self.dirs[0],self.dirs[1],'e'*64,self.manifest_sha,'2026-09-27T00:00:00+00:00')
        self.save();self.record_results()   # a subclass save() may not record them

    def save(self):
        for d,(rec,ev) in zip(self.dirs,self.records):
            ev.update(work_start=rec['start'],end=rec['end'])
            (d/'proof/start.json').write_text(json.dumps(dict(schema='loop-proof-start-v1',run_id=d.name,
                phase=d.name,attempt_id=rec['proof_attempt_id'],start=rec['start'],work_start=rec['start'],sandbox=ev.get('sandbox'),**self.policy)))
            (d/'receipt/receipt.json').write_text(json.dumps(rec));(d/'proof/evidence.json').write_text(json.dumps(ev))
            launch_fixture(d)
        self.record_results()

    def record_results(self,codes=(0,0)):
        """The launcher's record of each driver's exit and receipt (pair-<id>/RB.result.json, RC.result.json)."""
        import proof_launch
        p=getattr(self,'pair',None)
        if p is None:return   # setUp saves once before the pair exists
        for (phase,d),code in zip((('RB',self.dirs[0]),('RC',self.dirs[1])),codes):
            (p/('%s.result.json'%phase)).unlink(missing_ok=True)
            proof_launch.record_result(p,phase,code,d/'receipt/receipt.json')

    def run_tool(self,expected):
        self.assertTrue(TOOL.is_file(),'proof implementation required');self.save()
        r=subprocess.run([sys.executable,'-B',str(TOOL),'--pair',str(self.pair)]+[str(x) for x in self.dirs],env=dict(os.environ,HOME=str(self.root)),text=True,capture_output=True)
        self.assertEqual(r.returncode,expected,r.stdout+r.stderr);return json.loads(r.stdout)

    def test_complete_pair_passes(self):
        d=self.run_tool(0);self.assertEqual(d['verdict'],'PASS');self.assertAlmostEqual(d['runs'][0]['cost_per_landing_usd'],3/3)  # cash only: OpenRouter 3 over 3 landings; Claude is plan usage (owner 2026-10-05)

    def test_each_run_cost_bound_and_zero_landings(self):
        self.records[1][0]['openrouter_spend_usd']=5;self.run_tool(1)
        self.records[1][0]['openrouter_spend_usd']=3;self.records[0][0]['landings']=0;self.run_tool(1)

    def test_unknown_cost_and_liability_no_data(self):
        self.records[0][0]['claude_spend_usd']='NO-DATA: absent';self.run_tool(2)
        self.records[0][0]['claude_spend_usd']=1;self.records[0][1]['liability']['abandoned_unsettled_calls']=1;self.run_tool(2)

    def test_missing_sandbox_no_data(self):
        del self.records[0][1]['sandbox'];self.run_tool(2)

    def test_sandbox_escape_fails(self):
        self.records[0][1]['sandbox']['blocked_attempts']=2;self.run_tool(1)

    def test_sandbox_evidence_outside_the_run_fails(self):
        # a survivor of the generic mutation sweep (2026-09-27): the same bytes at another path passed every sandbox test
        rec=self.records[0][1]['sandbox'];outside=self.root/'outside-sandbox.json'
        outside.write_bytes(Path(rec['evidence_path']).read_bytes());rec['evidence_path']=str(outside);self.run_tool(1)

    def test_freeze_drift_and_missing_boundary(self):
        self.records[1][1]['freeze']['end']['manifest_sha256']='d'*64;self.run_tool(1)
        del self.records[1][1]['freeze']['end'];self.run_tool(2)

    def test_duration_continuity_and_intervention(self):
        self.records[0][0]['end']='2026-09-26T07:59:00+00:00';self.records[0][1]['freeze']['end']['observed_at']=self.records[0][0]['end'];self.run_tool(1)
        self.records[0][0]['end']='2026-09-26T08:00:00+00:00';self.records[0][1]['freeze']['end']['observed_at']=self.records[0][0]['end']
        self.records[1][1]['interventions']=['manual repair'];self.run_tool(1)

    def test_log_digest_and_scope(self):
        self.records[0][0]['driver_log_sha256']='e'*64;self.run_tool(1)
        self.records[0][0]['driver_log_sha256']=hashlib.sha256((self.dirs[0]/'driver.log').read_bytes()).hexdigest();self.records[1][1]['scope']='.*';self.run_tool(1)

    def test_malformed_nonfinite_and_boolean_cost_no_data(self):
        for value in (True,-1,float('nan'),float('inf'),None):
            self.records[0][0]['openrouter_spend_usd']=value;self.run_tool(2)

    def test_any_unknown_prevents_overall_pass(self):
        self.records[0][0]['landings']=0;self.records[1][0]['claude_spend_usd']=None
        self.assertEqual(self.run_tool(2)['verdict'],'NO-DATA')

    def test_missing_receipt_no_data(self):
        self.assertTrue(TOOL.is_file(),'proof implementation required');(self.dirs[0]/'receipt/receipt.json').unlink()
        r=subprocess.run([sys.executable,'-B',str(TOOL)]+[str(x) for x in self.dirs],capture_output=True,text=True)
        self.assertEqual(r.returncode,2,r.stdout+r.stderr)

class ExtraProof(Proof):
 def test_documented_intake_scope(self):
  doc=TOOL.parents[2]/'docs/plan/PROOF-ACCEPTANCE.md'
  if not doc.is_file() and not (TOOL.parents[2]/'.brother-edition').is_file():
   self.skipTest('proof acceptance documentation is not shipped in the public export')
  self.assertTrue(doc.is_file(),'proof acceptance documentation required')
  self.assertIn(self.records[0][1]['scope'],doc.read_text())
 def test_short_run_without_other_failure(self):
  self.records[0][0]['end']='2026-09-26T07:59:00+00:00'
  self.records[1][0]['start']=self.records[0][0]['end'];self.records[1][0]['end']='2026-09-26T15:59:00+00:00'
  for rec,ev in self.records:
   for k in ('start','end'):ev['freeze'][k]['observed_at']=rec[k]
  self.run_tool(1)
 def test_nonconsecutive_pair_fails(self):
  self.records[1][0]['start']='2026-09-26T08:01:00+00:00';self.records[1][0]['end']='2026-09-26T16:01:00+00:00'
  for k in ('start','end'):self.records[1][1]['freeze'][k]['observed_at']=self.records[1][0][k]
  self.run_tool(1)
 def test_boundary_identity_staleness_and_empty_files(self):
  import copy
  original=copy.deepcopy(self.records[0][1]['freeze']['start'])
  for field,value in [('run_id','other'),('phase','end'),('observed_at','2026-09-25T23:00:00+00:00'),('checked_files',0),('verdict','FAIL')]:
   self.records[0][1]['freeze']['start']=dict(original,**{field:value});self.run_tool(1)
 def test_unknown_boundary_remains_no_data(self):
  self.records[0][1]['freeze']['start']['verdict']='NO-DATA';self.run_tool(2)
 def test_false_unattended_and_bad_identity(self):
  self.records[0][1]['unattended']=False;self.run_tool(1)
  self.records[0][1]['unattended']=True;self.records[0][1]['run_id']='other';self.run_tool(1)
 def test_terminal_failure(self):
  self.records[0][0]['end_state']='INTERRUPTED';self.run_tool(1)

class ClockContract(Proof):
 def test_actual_driver_deadline_is_a_normal_terminal_state(self):
  for rec,ev in self.records:rec['end_state']='DEADLINE'
  self.run_tool(0)
 def test_thirty_second_handoff_is_bounded(self):
  rec,ev=self.records[1];rec['start']='2026-09-26T08:00:30+00:00';rec['end']='2026-09-26T16:00:30+00:00'
  for key in ('start','end'):ev['freeze'][key]['observed_at']=rec[key]
  self.run_tool(0)
 def test_sixty_one_second_handoff_fails(self):
  rec,ev=self.records[1];rec['start']='2026-09-26T08:01:01+00:00';rec['end']='2026-09-26T16:01:01+00:00'
  for key in ('start','end'):ev['freeze'][key]['observed_at']=rec[key]
  self.run_tool(1)
 def test_boundary_can_bracket_window_by_thirty_seconds(self):
  self.records[1][1]['freeze']['start']['observed_at']='2026-09-26T07:59:30+00:00'
  self.records[1][1]['freeze']['end']['observed_at']='2026-09-26T16:00:30+00:00'
  self.run_tool(0)
 def test_boundary_cannot_be_inside_work_window(self):
  self.records[1][1]['freeze']['start']['observed_at']='2026-09-26T08:00:01+00:00'
  self.run_tool(1)
class LaneR(Proof):
 """Lane R rows (DESIGN-FINAL R-1): each case breaks exactly one retained fact and names the one row it must move."""
 def verdict(self):self.save();import proof_accept as A;return A.accept(*self.dirs,pair=self.pair)
 def row(self,name,run=0):
  return next(r for r in self.verdict()['runs'][run]['checks'] if r['check']==name)['verdict']
 def pair_row(self,name):return next(r for r in self.verdict()['pair_checks'] if r['check']==name)['verdict']
 def proof(self,i=0):return self.dirs[i]/'proof'
 def resnap_claude(self,i,raw):
  (self.proof(i)/'claude-end.jsonl').write_bytes(raw);self.records[i][1]['claude_snapshot']['sha256']=sha(raw)
 def ledger_end(self,i):return (self.proof(i)/'ledger-end.jsonl').read_bytes()
 def resnap_ledger(self,i,raw):
  (self.proof(i)/'ledger-end.jsonl').write_bytes(raw);ev=self.records[i][1]
  ev['ledger_snapshot']['sha256']=sha(raw);ev['accounting']=loop_receipt.proof_accounting(str(self.dirs[i]),raw=raw)
 def relanding(self,i,rows,logs):self.records[i][1]['landings_record']=retain_landings(self.dirs[i],rows,logs)
 def rb_commits(self):return ['%040x'%(n+1) for n in range(3)]

 def test_honest_pair_passes_every_new_row(self):
  d=self.run_tool(0)
  for run in d['runs']:
   rows={r['check']:r['verdict'] for r in run['checks']}
   for name in ('landings','claude_snapshot','unattended','no_rehearsal_knob'):self.assertEqual(rows.get(name),'PASS',(name,rows))
  pair={r['check']:r['verdict'] for r in d['pair_checks']}
  for name in ('rb_closed_before_rc','claude_pair','pair_record','pair_result_RB','pair_result_RC'):self.assertEqual(pair.get(name),'PASS',(name,pair))

 # U5, B5-05: an RB call settled after RB's end snapshot, before RC's rows
 def test_b5_05_rb_spend_after_rb_snapshot_fails_the_pair(self):
  rb,rc=self.ledger_end(0),self.ledger_end(1);att='a'*32
  late=jsonl([dict(type='RESERVE',reservation_id='rb-late',at=13,estimated_cost=1,run_id='RB',attempt_id=att),
              dict(type='DISPATCH_START',reservation_id='rb-late',at=14,run_id='RB',attempt_id=att),
              dict(type='RECONCILE',reservation_id='rb-late',at=15,actual_cost=40,cost_source='provider_usage',run_id='RB',attempt_id=att)])
  self.resnap_ledger(1,rb+late+rc[len(rb):])
  self.assertEqual(self.pair_row('rb_closed_before_rc'),'FAIL');self.assertEqual(self.run_tool(1)['verdict'],'FAIL')

 # U9, objection 13: claude_pair, one guard per fixture
 def test_rb_claude_row_after_rb_end_fails_claude_pair(self):
  rb,rc=[(self.proof(i)/'claude-end.jsonl').read_bytes() for i in (0,1)]
  self.resnap_claude(1,rb+jsonl(claude_rows('RB','a'*32,9,cost=0,call='rb-late'))+rc[len(rb):])
  self.assertEqual(self.row('claude_snapshot',1),'PASS');self.assertEqual(self.pair_row('claude_pair'),'FAIL')
 def test_claude_row_tagged_with_neither_run_fails_claude_pair(self):
  rc=(self.proof(1)/'claude-end.jsonl').read_bytes()
  self.resnap_claude(1,rc+jsonl(claude_rows('other','c'*32,15,cost=0)))
  self.assertEqual(self.row('claude_snapshot',1),'PASS');self.assertEqual(self.pair_row('claude_pair'),'FAIL')
 def test_rb_figure_changed_inside_rc_snapshot_fails_claude_pair(self):
  rb,rc=[(self.proof(i)/'claude-end.jsonl').read_bytes() for i in (0,1)]
  self.resnap_claude(1,rb+jsonl(claude_rows('RB','a'*32,7,cost=5,call='rb-backdated'))+rc[len(rb):])
  self.assertEqual(self.pair_row('claude_pair'),'FAIL')
 def test_rc_snapshot_that_rewrote_rb_rows_fails_claude_pair(self):
  rb,rc=[(self.proof(i)/'claude-end.jsonl').read_bytes() for i in (0,1)]
  reordered=b''.join((json.dumps(json.loads(l),sort_keys=False,indent=None,separators=(',',':'))+'\n').encode() for l in rb.splitlines())
  self.assertNotEqual(reordered,rb);self.resnap_claude(1,reordered+rc[len(rb):])
  self.assertEqual(self.row('claude_snapshot',1),'PASS');self.assertEqual(self.pair_row('claude_pair'),'FAIL')

 # Lane X4: plain json kept the LAST of repeated members, so an RB row after RB's end, tagged "run":"RB" then
 # "run":"RC", read as RC's and passed; the strict parser refuses the row, which is NO-DATA like any unreadable row
 def late_rb_row(self,run):
  return ('{"call":"rb-late","at":"%s","expires_at":"%s",%s,"attempt":"%s"}\n'%(iso(15),iso(15,30),run,'a'*32)).encode()
 def test_an_rc_claude_row_the_control_tags_as_rc_passes_claude_pair(self):
  self.resnap_claude(1,(self.proof(1)/'claude-end.jsonl').read_bytes()+self.late_rb_row('"run":"RC"'))
  self.assertEqual(self.pair_row('claude_pair'),'PASS')
 def test_a_claude_row_with_a_repeated_run_is_no_data_for_claude_pair(self):
  self.resnap_claude(1,(self.proof(1)/'claude-end.jsonl').read_bytes()+self.late_rb_row('"run":"RB","run":"RC"'))
  self.assertEqual(self.pair_row('claude_pair'),'NO-DATA')

 # U9: claude_snapshot, the receipt's figure against the retained bytes
 def test_receipt_claude_edited_to_zero_fails_claude_snapshot(self):
  self.records[0][0]['claude_spend_usd']=0;self.assertEqual(self.row('claude_snapshot'),'FAIL')
 def test_claude_snapshot_bytes_changed_after_the_end_fail(self):
  (self.proof(0)/'claude-end.jsonl').write_bytes(jsonl(claude_rows('RB','a'*32,1,cost=0)));self.assertEqual(self.row('claude_snapshot'),'FAIL')
 def test_a_call_pending_at_the_end_under_a_numeric_receipt_fails(self):
  raw=(self.proof(0)/'claude-end.jsonl').read_bytes()+jsonl([dict(call='open',at=iso(7),expires_at=iso(9),run='RB',attempt='a'*32)])
  self.resnap_claude(0,raw);self.assertEqual(self.row('claude_snapshot'),'FAIL')
 def open_rb_call(self,expires):
  return jsonl([dict(call='rb-open',at=iso(7),expires_at=expires,run='RB',attempt='a'*32)])
 def test_a_claude_receipt_no_data_stays_no_data(self):
  # receipt and bytes agree the figure is unknown: NO-DATA, not a contradiction
  self.resnap_claude(0,(self.proof(0)/'claude-end.jsonl').read_bytes()+self.open_rb_call(iso(9)))
  self.records[0][0]['claude_spend_usd']='NO-DATA: pending';self.assertEqual(self.row('claude_snapshot'),'NO-DATA')
 def test_a_symlinked_claude_snapshot_fails(self):
  p=self.proof(0)/'claude-end.jsonl';outside=self.root/'outside-claude.jsonl';outside.write_bytes(p.read_bytes());p.unlink();p.symlink_to(outside)
  self.assertEqual(self.row('claude_snapshot'),'FAIL')
 def test_a_claude_snapshot_record_naming_another_run_fails(self):
  self.records[0][1]['claude_snapshot']['run_id']='RC';self.assertEqual(self.row('claude_snapshot'),'FAIL')
 def test_an_rb_call_left_open_inside_rc_snapshot_fails_claude_pair(self):
  rb,rc=[(self.proof(i)/'claude-end.jsonl').read_bytes() for i in (0,1)]
  self.resnap_claude(1,rb+self.open_rb_call(iso(7,30))+rc[len(rb):]);self.assertEqual(self.pair_row('claude_pair'),'FAIL')
 def test_an_rb_no_data_receipt_with_its_open_call_is_no_data_for_the_pair(self):
  rb,rc=[(self.proof(i)/'claude-end.jsonl').read_bytes() for i in (0,1)];rb2=rb+self.open_rb_call(iso(9))
  self.resnap_claude(0,rb2);self.resnap_claude(1,rb2+rc[len(rb):]);self.records[0][0]['claude_spend_usd']='NO-DATA: pending'
  self.assertEqual(self.pair_row('claude_pair'),'NO-DATA')

 # U10 and objection 15: landings rederived from the retained record and history
 def test_receipt_three_landings_with_a_record_of_one_fails(self):
  c=self.rb_commits()[0];self.relanding(0,[landing_row(c,iso(2))],{c:b''});self.assertEqual(self.row('landings'),'FAIL')
 def test_a_revert_in_the_observed_range_removes_the_landing(self):
  cs=self.rb_commits();log=('%s\x00Revert "landing"\n\nThis reverts commit %s.\n\n'%('f'*40,cs[0])).encode()
  logs=chain_logs(cs,{cs[0]:log})
  self.relanding(0,[landing_row(c,iso(2,n)) for n,c in enumerate(cs)],logs)
  self.assertEqual(self.row('landings'),'FAIL')
  self.records[0][0]['landings']=2;self.assertEqual(self.row('landings'),'PASS')
 def test_a_revert_the_run_never_observed_keeps_the_landing_and_names_the_fetch(self):
  d=self.verdict()['runs'][0]
  self.assertEqual(next(r['verdict'] for r in d['checks'] if r['check']=='landings'),'PASS')
  self.assertEqual(d['landings_detail']['observed_at'][self.rb_commits()[0]],iso(2,0))
 def test_missing_retained_history_is_no_data(self):
  (self.proof(0)/'landing-history'/(self.rb_commits()[0]+'.log')).unlink();self.assertEqual(self.row('landings'),'NO-DATA')
 def test_a_landing_whose_remote_was_never_observed_is_no_data(self):
  cs=self.rb_commits()
  self.relanding(0,[landing_row(cs[0],iso(2),'UNVERIFIED',fetched=False)]+[landing_row(c,iso(2,n+1)) for n,c in enumerate(cs[1:])],chain_logs(cs[1:]))
  self.assertEqual(self.row('landings'),'NO-DATA')
 def test_a_landing_record_changed_after_the_end_fails(self):
  with (self.proof(0)/'landings.jsonl').open('ab') as out:out.write(jsonl([landing_row('%040x'%99,iso(3))]))
  self.assertEqual(self.row('landings'),'FAIL')
 def test_landing_history_changed_after_the_end_fails(self):
  with (self.proof(0)/'landing-history'/(self.rb_commits()[0]+'.log')).open('ab') as out:out.write(b'x')
  self.assertEqual(self.row('landings'),'FAIL')
 def test_each_unobserved_condition_leaves_survival_unknown(self):
  cs=self.rb_commits();rest=[landing_row(c,iso(2,n+1)) for n,c in enumerate(cs[1:])];all_logs=chain_logs(cs)
  for name,row,logs in [('verdict',dict(landing_row(cs[0],iso(2)),verdict='UNVERIFIED'),all_logs),
                        ('remote sha',dict(landing_row(cs[0],iso(2)),remote_sha=None),all_logs),
                        ('fetch time',dict(landing_row(cs[0],iso(2)),fetched_at=None),all_logs),
                        ('retained history',landing_row(cs[0],iso(2)),{c:all_logs[c] for c in cs[1:]})]:
   with self.subTest(missing=name):
    self.relanding(0,[row]+rest,logs);self.assertEqual(self.row('landings'),'NO-DATA')
 def test_a_row_that_is_not_a_landing_row_is_no_data(self):
  cs=self.rb_commits();self.relanding(0,[dict(landing_row(c,iso(2,n)),schema='loop-landing-v0' if n==0 else 'loop-landing-v1') for n,c in enumerate(cs)],chain_logs(cs))
  self.assertEqual(self.row('landings'),'NO-DATA')
 def test_a_repeated_row_for_a_landed_commit_keeps_it(self):
  cs=self.rb_commits();rows=[landing_row(c,iso(2,n)) for n,c in enumerate(cs)]+[landing_row(cs[0],iso(3),'UNVERIFIED',fetched=False)]
  self.relanding(0,rows,chain_logs(cs));self.assertEqual(self.row('landings'),'PASS')
 def test_a_landing_outside_the_window_is_not_counted(self):
  cs=self.rb_commits();self.relanding(0,[landing_row(cs[0],iso(9))]+[landing_row(c,iso(2,n)) for n,c in enumerate(cs[1:])],chain_logs(cs))
  self.assertEqual(self.row('landings'),'FAIL')
  self.records[0][0]['landings']=2;self.assertEqual(self.row('landings'),'PASS')

 # finding B2 (2026-09-27): survival is judged at the run's LAST fetch, so a history that stops short of it is unknown
 def test_a_history_the_last_fetch_did_not_refresh_is_no_data(self):
  # a refresh that could not write leaves A's history as written at A's own fetch: empty, and silent about a revert
  cs=self.rb_commits();logs=chain_logs(cs);logs[cs[0]]=b''
  self.relanding(0,[landing_row(c,iso(2,n)) for n,c in enumerate(cs)],logs);self.assertEqual(self.row('landings'),'NO-DATA')
 def test_a_history_refreshed_at_an_earlier_fetch_only_is_no_data(self):
  cs=self.rb_commits();logs=chain_logs(cs);logs[cs[0]]=chain_logs(cs[:2])[cs[0]]   # reaches cs[1], never the last fetch
  self.relanding(0,[landing_row(c,iso(2,n)) for n,c in enumerate(cs)],logs);self.assertEqual(self.row('landings'),'NO-DATA')
 # X1 finding 1 (2026-09-27): a history write that failed part way kept its newest entry, which names the last fetch,
 # and lost the older revert below it; naming the horizon was read as full coverage, so a reverted landing counted
 def test_a_history_cut_after_its_newest_entry_is_no_data(self):
  cs=self.rb_commits();revert=('%s\x00Revert "landing"\n\nThis reverts commit %s.\n\n'%('f'*40,cs[0])).encode()
  full=('%s\x00landing %s\n\n%s\x00landing %s\n\n'%(cs[2],cs[2][-4:],cs[1],cs[1][-4:])).encode()+revert
  logs=chain_logs(cs);logs[cs[0]]=full[:full.index(b'\n\n')+2]
  self.relanding(0,[landing_row(c,iso(2,n)) for n,c in enumerate(cs)],logs);self.assertEqual(self.row('landings'),'NO-DATA')
 def refresh_at_a_fourth_landing(self,answer):
  """RB's three landings, then a fourth landing's fetch: the real land_batch.refresh_histories rewrites the earlier
  histories with `answer(commit)` for their range, and the fourth landing's own row and empty history are retained.
  Returns (histories rewritten, printed lines)."""
  import contextlib,io,land_batch
  d=self.dirs[0];new='%040x'%99;buf=io.StringIO()
  with contextlib.redirect_stdout(buf):refreshed=land_batch.refresh_histories(str(d),new,lambda commit,sha:answer(commit))
  (d/'proof/landing-history'/(new+'.log')).write_bytes(b'')
  with (d/'proof/landings.jsonl').open('ab') as out:out.write(jsonl([landing_row(new,iso(2,3))]))
  self.records[0][1]['landings_record']=loop_receipt.landing_record(str(d));self.records[0][0]['landings']=4
  return refreshed,buf.getvalue()
 def test_a_refresh_that_failed_for_one_earlier_range_only_is_no_data(self):
  # the coordinator's second shape (SBE on hub main, 2026-09-27): one earlier range unreadable, the others refreshed
  cs=self.rb_commits();full=bodies(cs+['%040x'%99])
  refreshed,said=self.refresh_at_a_fourth_landing(lambda c:None if c==cs[0] else full[c])
  self.assertEqual(refreshed,2);self.assertIn(cs[0][:12],said)
  self.assertEqual(self.row('landings'),'NO-DATA')
 def test_the_same_refresh_reaching_every_range_counts_the_revert_it_carries(self):
  # the control: identical, except the first range is read, and it carries the revert of the first landing
  cs=self.rb_commits();full=bodies(cs+['%040x'%99])
  revert=('%s\x00Revert "landing"\n\nThis reverts commit %s.\n\n'%('e'*40,cs[0])).encode()
  refreshed,said=self.refresh_at_a_fourth_landing(lambda c:full[c]+(revert if c==cs[0] else b''))
  self.assertEqual((refreshed,said),(3,''))
  self.assertEqual(self.row('landings'),'FAIL')
  self.records[0][0]['landings']=3;self.assertEqual(self.row('landings'),'PASS')
 def test_a_last_fetch_whose_remote_is_not_text_is_unknown_never_a_crash(self):
  import proof_accept as A
  cs=self.rb_commits();rows=[landing_row(c,iso(2,n)) for n,c in enumerate(cs)];rows[-1]['remote_sha']=5
  surviving,unknown,_=A.surviving_landings(rows,chain_logs(cs),iso(0),iso(8))
  self.assertEqual((surviving,unknown),(0,3))

 # U11, objection 14: the history markers and the second failure record
 def test_history_without_its_end_marker_is_no_data(self):
  raw=jsonl([dict(kind='start-marker',detail='',observed_at=iso(0))]);(self.proof(0)/'events.jsonl').write_bytes(raw)
  self.records[0][1]['history_sha256']=sha(raw);self.assertEqual(self.row('unattended'),'NO-DATA')
 def test_a_failed_event_append_record_is_no_data(self):
  (self.dirs[0]/'proof-events-failed').write_text('');self.assertEqual(self.row('unattended'),'NO-DATA')
 def test_history_changed_after_the_end_fails(self):
  with (self.proof(0)/'events.jsonl').open('ab') as out:out.write(jsonl([dict(kind='pause',detail='x',observed_at=iso(3))]))
  self.assertEqual(self.row('unattended'),'FAIL')
 def test_an_intervention_the_evidence_left_out_fails(self):
  raw=jsonl([dict(kind='start-marker',detail='',observed_at=iso(0)),dict(kind='hold-observed',detail='x',observed_at=iso(3)),dict(kind='end-marker',detail='',observed_at=iso(8))])
  (self.proof(0)/'events.jsonl').write_bytes(raw);self.records[0][1]['history_sha256']=sha(raw);self.assertEqual(self.row('unattended'),'FAIL')

 # objection 18: the rehearsal knob never reaches a production verdict
 def rewrite_manifest(self,env):
  self.manifest.write_text(json.dumps({'environment':env}));new=sha(self.manifest.read_bytes())
  for rec,ev in self.records:
   for b in ('start','end'):ev['freeze'][b]['manifest_sha256']=new
 def test_a_manifest_carrying_the_rehearsal_knob_fails(self):
  self.rewrite_manifest({'BROTHER_SCOPE':'f'*64,'BROTHER_PROOF_MIN_WINDOW_S':'0'*64});self.assertEqual(self.row('no_rehearsal_knob'),'FAIL')
 def test_a_manifest_that_is_not_the_verified_one_fails(self):
  self.manifest.write_text(json.dumps({'environment':{}}));self.assertEqual(self.row('no_rehearsal_knob'),'FAIL')
 def test_a_missing_manifest_is_no_data(self):
  self.manifest.unlink();self.assertEqual(self.row('no_rehearsal_knob'),'NO-DATA')

 # objection 7, D-12: the launcher's pair record
 def test_no_pair_record_supplied_is_no_data(self):
  self.save();import proof_accept as A
  row=next(r for r in A.accept(*self.dirs)['pair_checks'] if r['check']=='pair_record')
  self.assertEqual(row['verdict'],'NO-DATA');self.assertIn('--pair',row['reason'])
 def test_runs_with_no_frozen_manifest_leave_the_pair_record_no_data(self):
  for rec,ev in self.records:ev['freeze']={}
  self.assertEqual(self.pair_row('pair_record'),'NO-DATA')
 def test_each_result_field_is_bound(self):
  for key,value in [('phase','RC'),('run_dir',str(self.dirs[1].resolve())),('schema','loop-proof-result-v0')]:
   with self.subTest(field=key):
    self.save();p=self.pair/'RB.result.json';v=json.loads(p.read_text());v[key]=value;p.write_text(json.dumps(v))
    import proof_accept as A
    self.assertEqual(next(r['verdict'] for r in A.accept(*self.dirs,pair=self.pair)['pair_checks'] if r['check']=='pair_result_RB'),'FAIL')
 def test_a_pair_naming_other_run_directories_fails(self):
  import proof_launch
  other=self.root/'pair-y';other.mkdir();proof_launch.write_pair(other,self.root/'elsewhere',self.dirs[1],'e'*64,self.manifest_sha,'2026-09-27T00:00:00+00:00')
  self.pair=other;self.assertEqual(self.pair_row('pair_record'),'FAIL')
 def test_a_pair_that_froze_another_manifest_fails(self):
  import proof_launch
  other=self.root/'pair-z';other.mkdir();proof_launch.write_pair(other,self.dirs[0],self.dirs[1],'e'*64,'0'*64,'2026-09-27T00:00:00+00:00')
  self.pair=other;self.assertEqual(self.pair_row('pair_record'),'FAIL')
 def test_a_receipt_changed_after_its_result_was_recorded_fails(self):
  self.save();p=self.dirs[1]/'receipt/receipt.json';p.write_text(p.read_text()+' ')
  import proof_accept as A
  self.assertEqual(next(r['verdict'] for r in A.accept(*self.dirs,pair=self.pair)['pair_checks'] if r['check']=='pair_result_RC'),'FAIL')

class DocumentationBoundary(unittest.TestCase):
 def check_case(self, edition, document):
  from unittest import mock
  with tempfile.TemporaryDirectory() as root:
   root=Path(root);tool=root/'scripts/loop/proof_accept.py';tool.parent.mkdir(parents=True)
   if edition:(root/'.brother-edition').write_text('private')
   if document:
    doc=root/'docs/plan/PROOF-ACCEPTANCE.md';doc.parent.mkdir(parents=True);doc.write_text('fixture-scope')
   case=ExtraProof('test_documented_intake_scope');case.records=[({}, {'scope':'fixture-scope'})]
   with mock.patch.dict(globals(), TOOL=tool):
    result=unittest.TestResult()
    try:case.test_documented_intake_scope()
    except unittest.SkipTest as exc:result.addSkip(case,str(exc))
    except AssertionError:result.addFailure(case,sys.exc_info())
    except Exception:result.addError(case,sys.exc_info())
   return result
 def test_export_without_document_is_named_skip(self):
  r=self.check_case(False,False);self.assertEqual(len(r.skipped),1);self.assertFalse(r.failures+r.errors)
 def test_repository_document_runs_without_skip(self):
  r=self.check_case(True,True);self.assertFalse(r.skipped+r.failures+r.errors)
 def test_repository_missing_document_fails_without_skip(self):
  r=self.check_case(True,False);self.assertEqual(len(r.failures),1);self.assertFalse(r.skipped+r.errors)
 def test_document_present_without_edition_still_runs(self):
  r=self.check_case(False,True);self.assertFalse(r.skipped+r.failures+r.errors)

class Scope(unittest.TestCase):
 """The proof scope after the owner's ruling of 2026-10-04 ("A: Widen to held 1.1.0 units"). One fixture per guard:
 each held sub unit's parent is admitted (the pool admits at unit grain), a 1.1.1 unit is refused, a prefix sibling is
 refused (every alternative is anchored), and the zero landing FAIL still fires on its own."""
 HELD=('FX-31.8','PR1.c','PR1.d','PR1.e','PR1.f','OP1.d','MG1.e')
 def admitted(self,unit_id):
  import re
  return re.match(proof_accept.SCOPE,unit_id) is not None
 def test_each_held_sub_units_parent_is_admitted(self):
  if not LAUNCH_BOARD.is_file():self.skipTest('NO-DATA: the launch board is not shipped in this tree')
  units=json.loads(LAUNCH_BOARD.read_text(encoding='utf-8'))['units']
  parent={s:u['id'] for u in units for s in (u.get('sub_units') or [])}
  for sub in self.HELD:
   self.assertIn(sub,parent,'%s is not a sub unit on the launch board'%sub)
   self.assertTrue(self.admitted(parent[sub]),'%s (parent of %s) must be in the proof scope'%(parent[sub],sub))
 def test_a_1_1_1_unit_is_refused(self):
  for unit_id in ('SO','FB1','U8'):
   self.assertFalse(self.admitted(unit_id),'%s is 1.1.1 work and must stay out of the proof scope'%unit_id)
 def test_a_prefix_sibling_is_refused(self):
  for unit_id in ('D2.70','PR1.cc','L3b','D20','FX-310','OP10','MG10','M4'):
   self.assertFalse(self.admitted(unit_id),'%s shares a prefix with an admitted id and must be refused'%unit_id)

class Supply(LaneR):
 """owner 2026-10-04: a proof lands only the sub units named as its supply (plan_store.SUPPLY). One fixture per guard."""
 def test_a_landing_of_a_sub_unit_outside_the_widened_units_passes(self):
  self.assertEqual(self.row('supply'),'PASS')
 def test_a_landing_of_a_sub_unit_the_owner_kept_from_the_loop_fails_the_run(self):
  commits=['%040x'%(n+1) for n in range(3)];rows=[landing_row(c,iso(2,n)) for n,c in enumerate(commits)];rows[1]['subs']=['D2.6']
  self.records[0][1]['landings_record']=retain_landings(self.dirs[0],rows,chain_logs(commits))
  self.assertEqual(self.row('supply'),'FAIL');self.assertEqual(self.row('landings'),'PASS')
 def test_a_landing_of_a_named_supply_sub_unit_passes(self):
  commits=['%040x'%(n+1) for n in range(3)];rows=[landing_row(c,iso(2,n)) for n,c in enumerate(commits)];rows[1]['subs']=['MG1.e']
  self.records[0][1]['landings_record']=retain_landings(self.dirs[0],rows,chain_logs(commits))
  self.assertEqual(self.row('supply'),'PASS')
 def test_a_landing_of_d2_7_fails_the_run_since_it_left_the_supply(self):
  # owner 2026-10-05 (proofs-unlandable.json, option A): D2.7 is a reviewed hand-route item, D2's supply is empty
  commits=['%040x'%(n+1) for n in range(3)];rows=[landing_row(c,iso(2,n)) for n,c in enumerate(commits)];rows[1]['subs']=['D2.7']
  self.records[0][1]['landings_record']=retain_landings(self.dirs[0],rows,chain_logs(commits))
  self.assertEqual(self.row('supply'),'FAIL')
 def test_a_landing_of_mg1_f_fails_the_run_since_it_left_the_supply(self):
  # 2026-10-06: the loop landed MG1.f on 2026-10-05, the cut condition review refused it, it was removed and moves to
  # 1.1.1; a pool or a proof that still treated it as supply would build and land the refused sub unit again
  commits=['%040x'%(n+1) for n in range(3)];rows=[landing_row(c,iso(2,n)) for n,c in enumerate(commits)];rows[1]['subs']=['MG1.f']
  self.records[0][1]['landings_record']=retain_landings(self.dirs[0],rows,chain_logs(commits))
  self.assertEqual(self.row('supply'),'FAIL');self.assertEqual(self.row('landings'),'PASS')

class ZeroLandings(Proof):
 def test_zero_landings_alone_fails_the_run(self):
  """Only the landing count is zero: the retained record is empty too, so the landings row stays PASS and the one row
  that moves is cost_per_landing (proof_accept.py: landings == 0 is False, never a ratio)."""
  rec,ev=self.records[0];rec['landings']=0
  ev['landings_record']=retain_landings(self.dirs[0],[],{})
  d=self.run_tool(1);checks={r['check']:r['verdict'] for r in d['runs'][0]['checks']}
  self.assertEqual(checks['cost_per_landing'],'FAIL',checks)
  self.assertEqual(checks['landings'],'PASS',checks)

if __name__=='__main__':unittest.main()
