#!/usr/bin/env python3
"""XS1.a frozen contract: cross-session orchestration, the session relay (owner order 2026-11-01).

The class SessionRelay below is the unit's frozen contract (its sha256 is recorded with the commit that placed it,
before any implementation existed). The harness around it is the 1.2.0 delivery pack's own, as placed for CUT2.a:
need(), MissingContract and the PASS, FAIL, NO-DATA result rows. ROOT defaults to the checkout holding this file.
A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped green.

The five edges are the design judge's (record: XS1-DESIGN-RECORD-2026-11-01.md in the coordinator's notes folder):
two coordinators, a builder turned reviewer, a review at an old head, an unreadable ledger, and a spend reading at
and past its threshold. may_merge is an AUDIT in this slice, not yet a control: slice 2 wires it into the land path.

Run: python3 -B scripts/test_session_relay_contract.py --require-implementation
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get('BROTHER_SOURCE_ROOT', str(Path(__file__).resolve().parents[1])))
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'scripts/loop')]
REQUIRE = '--require-implementation' in sys.argv
class MissingContract(Exception):
    pass

def need(path, name):
    source = ROOT/path
    if not source.is_file():
        raise MissingContract(path+' is absent')
    tree = ast.parse(source.read_text())
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in tree.body):
        raise MissingContract(path+' defines no '+name)
    key = '_workflow_contract_'+source.stem
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, source)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return getattr(sys.modules[key], name)

class SessionRelay(unittest.TestCase):
    """XS1.a: append(path, event, *, session) -> seq; read(path) -> events (raises LedgerUnreadable);
    builders(events, pr) -> frozenset; may_review(events, pr, reviewer) and may_merge(events, pr, head, actor)
    -> Decision(ok, reason); brief(state, task) -> str, STATE first; handover_due(spent, ceiling, reserve)
    -> CONTINUE | HANDOVER | STOP, raising ValueError on a bad reading; gate_command(argv, env) -> (argv, env).
    """
    H1, H2 = 'a'*40, 'b'*40
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='xs1-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, 'relay.json')
    def put(self, session, **event):
        return need('scripts/session_relay.py', 'append')(self.path, event, session=session)
    def events(self):
        return need('scripts/session_relay.py', 'read')(self.path)
    def merge(self, head, actor):
        return need('scripts/session_relay.py', 'may_merge')(self.events(), 7, head, actor)
    def test_builder_set_is_every_session_that_built(self):
        self.put('S1', kind='built', pr=7, head=self.H1)
        self.put('S2', kind='built', pr=7, head=self.H2)
        self.assertEqual(frozenset(('S1', 'S2')), need('scripts/session_relay.py', 'builders')(self.events(), 7))
        self.put('R', kind='reviewed', pr=7, head=self.H2, verdict='PASS')
        self.assertFalse(self.merge(self.H2, 'S1').ok)
        self.assertFalse(self.merge(self.H2, 'S2').ok)
        self.assertTrue(self.merge(self.H2, 'R').ok)
    def test_builder_turned_reviewer_is_refused(self):
        self.put('S', kind='built', pr=7, head=self.H1)
        self.assertFalse(need('scripts/session_relay.py', 'may_review')(self.events(), 7, 'S').ok)
        self.put('S', kind='reviewed', pr=7, head=self.H1, verdict='PASS')
        self.assertFalse(self.merge(self.H1, 'C').ok)
    def test_review_at_an_old_head_does_not_carry(self):
        self.put('S', kind='built', pr=7, head=self.H1)
        self.put('R', kind='reviewed', pr=7, head=self.H1, verdict='PASS')
        self.put('S', kind='built', pr=7, head=self.H2)
        self.assertFalse(self.merge(self.H2, 'R').ok)
        self.assertFalse(self.merge(self.H1, 'R').ok)
        self.assertFalse(self.merge(self.H2[:12], 'R').ok)
    def test_unreadable_ledger_blocks(self):
        self.put('S', kind='built', pr=7, head=self.H1)
        with open(self.path, 'a') as fh:
            fh.write('x')
        unreadable = need('scripts/session_relay.py', 'read').__globals__['LedgerUnreadable']
        with self.assertRaises(unreadable):
            self.events()
        with self.assertRaises(unreadable):
            self.put('R', kind='reviewed', pr=7, head=self.H1, verdict='PASS')
    def test_handover_epochs_must_step_by_one(self):
        self.put('C1', kind='handover', from_epoch=3, to_epoch=4, to_session='C2')
        with self.assertRaises(ValueError):
            self.put('C1', kind='handover', from_epoch=3, to_epoch=6, to_session='C3')
    def test_spend_threshold_and_ceiling(self):
        fn = need('scripts/session_relay.py', 'handover_due')
        self.assertEqual('CONTINUE', fn(100, 1000, 100))
        self.assertEqual('HANDOVER', fn(800, 1000, 100))
        self.assertEqual('HANDOVER', fn(700, 1000, 300))
        self.assertEqual('STOP', fn(1000, 1000, 100))
        for bad in ((None, 1000, 100), (-1, 1000, 100), (1, 0, 0), (1, 1000, 1000), (True, 1000, 1)):
            with self.assertRaises(ValueError):
                fn(*bad)
    def test_brief_leads_with_measured_state(self):
        fn = need('scripts/session_relay.py', 'brief')
        state = dict(repo='r', line_tip='c'*40, open_prs=[], disk_free_gib=6.8,
                     measured_utc='2026-11-01T00:00:00Z', session='C1')
        text = fn(state, 'Build unit U1.')
        self.assertLess(text.index('c'*40), text.index('Build unit U1.'))
        for field in ('line_tip', 'disk_free_gib', 'measured_utc'):
            for bad in (None, 'NO-DATA'):
                broken = dict(state, **{field: bad})
                with self.assertRaises(need('scripts/session_relay.py', 'brief').__globals__['BriefRefused']):
                    fn(broken, 'Build unit U1.')
    def test_gate_command_queues_on_one_slot(self):
        argv, env = need('scripts/session_relay.py', 'gate_command')(['sh', 'scripts/required_fast.sh'],
                                                                     {'BROTHER_HEAVY_SLOT': 'off'})
        self.assertTrue(argv[1].endswith('heavy_slot.py'))
        self.assertEqual(['sh', 'scripts/required_fast.sh'], argv[2:])
        self.assertEqual('1', env['LOCAL_SLOTS'])
        self.assertNotEqual('off', env.get('BROTHER_HEAVY_SLOT', ''))

class Results(unittest.TestResult):
    def __init__(self):
        super().__init__(); self.rows=[]
    def addSuccess(self,test):
        super().addSuccess(test); self.rows.append(dict(test=test.id(),verdict='PASS'))
    def addError(self,test,err):
        super().addError(test,err)
        verdict='FAIL' if REQUIRE or not issubclass(err[0],MissingContract) else 'NO-DATA'
        self.rows.append(dict(test=test.id(),verdict=verdict,reason=str(err[1])))
    def addFailure(self,test,err):
        super().addFailure(test,err); self.rows.append(dict(test=test.id(),verdict='FAIL',reason=str(err[1])))

if __name__=='__main__':
    names=[a for a in sys.argv[1:] if a!='--require-implementation']
    loader=unittest.TestLoader()
    suite=loader.loadTestsFromNames(names,sys.modules[__name__]) if names else loader.loadTestsFromModule(sys.modules[__name__])
    result=Results(); suite.run(result)
    counts={v:sum(r['verdict']==v for r in result.rows) for v in ('PASS','FAIL','NO-DATA')}
    print(json.dumps(dict(source=str(ROOT),scope='unit contract tests; not real host acceptance',
                          counts=counts,results=result.rows),indent=2))
    sys.exit(1 if counts['FAIL'] else 2 if counts['NO-DATA'] else 0)
