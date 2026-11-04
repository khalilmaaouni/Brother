#!/usr/bin/env python3
"""LOOP1.f frozen contract, placed in the tree UNCHANGED from the 1.2.0 delivery pack's test_workflows.py.

The class LiveState below is byte for byte the pack's class (its sha256 is recorded with the commit that placed it).
The harness around it is the pack's own: need(), MissingContract and the PASS, FAIL, NO-DATA result rows, with one
change, ROOT: the pack defaults to a checkout path on one laptop; here it defaults to the checkout holding this file.
A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped green.

Run: python3 -B scripts/test_live_state.py --require-implementation
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
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

class LiveState(unittest.TestCase):
    """LOOP1.f: current_run(snapshot, events, now) -> canonical state dict.
    Events belong to one run and are monotonic by sequence. Snapshot isn't authority.
    Expired monitor lease yields next_action=renew_monitor, never DONE.
    """
    def fixture(self):
        return dict(schema=1, run='r1', sequence=1, candidate='old', state='WAITING',
                    phase='capacity', lease_until=200)
    def test_new_candidate_progress_overrides_old_disk_checkpoint(self):
        old=self.fixture(); new=dict(old, sequence=2, candidate='new', state='RUNNING',phase='gate')
        got=need('scripts/loop/stage_log.py','current_run')(old,[new,old],100)
        self.assertEqual(('new','gate',2),(got['candidate'],got['phase'],got['sequence']))
    def test_watch_expiry_does_not_finish_or_restart_child(self):
        got=need('scripts/loop/stage_log.py','current_run')(self.fixture(),[],201)
        self.assertNotEqual('DONE',got['state'])
        self.assertEqual('renew_monitor',got['next_action'])
    def test_other_run_cannot_overwrite_this_one(self):
        old=self.fixture(); other=dict(old,run='r2',sequence=99,state='DONE')
        got=need('scripts/loop/stage_log.py','current_run')(old,[other],100)
        self.assertEqual(('r1',1),(got['run'],got['sequence']))

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
