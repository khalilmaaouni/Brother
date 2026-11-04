#!/usr/bin/env python3
"""CUT2.f frozen contract, placed in the tree UNCHANGED from the 1.2.0 delivery pack's test_workflows.py.

The class Publication below is byte for byte the pack's class (its sha256 is recorded with the commit that placed it).
The harness around it is the pack's own: need(), MissingContract and the PASS, FAIL, NO-DATA result rows, with one
change, ROOT: the pack defaults to a checkout path on one laptop; here it defaults to the checkout holding this file.
A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped green.

Run: python3 -B scripts/test_cut_publish_contract.py --require-implementation
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

class Publication(unittest.TestCase):
    """CUT2.f: publication_next(state, now) -> {action, reason}.
    state is trusted controller readback, not untrusted release-page content.
    identity binds candidate/export/body; approval binds that exact identity.
    Existing cut.py performs cryptographic/host authorization and actual effects.
    """
    def state(self, **kw):
        identity=dict(candidate='a'*40,export='b'*64,body='c'*64)
        return dict(dict(ready=True,identity=identity, approval=dict(identity=identity,expires=200,used=False),
                         remote_commit=None,artifacts_verified=False,receipt_written=False),**kw)
    def next(self, state):
        return need('scripts/cut_driver.py','publication_next')(state,100)['action']
    def test_prepare_before_asking_for_approval(self):
        self.assertEqual('PREPARE',self.next(self.state(ready=False,approval=None)))
        self.assertEqual('ASK_APPROVAL',self.next(self.state(approval=None)))
    def test_expired_changed_or_replayed_approval_cannot_push(self):
        for change in ({'expires':99},{'used':True},{'identity':{'candidate':'z'*40}}):
            state=self.state(); state['approval']=dict(state['approval'],**change)
            self.assertNotEqual('PUSH',self.next(state))
    def test_foreign_remote_tag_refuses_and_exact_tag_verifies(self):
        self.assertEqual('REFUSE',self.next(self.state(remote_commit='z'*40)))
        self.assertEqual('VERIFY',self.next(self.state(remote_commit='a'*40)))
    def test_done_requires_verified_artifacts_and_durable_receipt(self):
        state=self.state(remote_commit='a'*40,artifacts_verified=True)
        self.assertEqual('WRITE_RECEIPT',self.next(state))
        state['receipt_written']=True
        self.assertEqual('DONE',self.next(state))

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
