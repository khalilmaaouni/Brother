#!/usr/bin/env python3
"""UI1.a frozen contract, placed in the tree UNCHANGED from the 1.2.0 delivery pack's test_workflows.py.

The class ConsumerView below is byte for byte the pack's class (its sha256 is recorded with the commit that placed it).
The harness around it is the pack's own: need(), MissingContract and the PASS, FAIL, NO-DATA result rows, with one
change, ROOT: the pack defaults to a checkout path on one laptop; here it defaults to the checkout holding this file.
A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped green.

Run: python3 -B scripts/test_consumer_view_contract.py --require-implementation
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

class ConsumerView(unittest.TestCase):
    """UI1.a: workflow_card(state) -> text. Input is canonical consumer facts.
    Technical diagnostics and credential-like synthetic fields are never echoed.
    """
    def card(self, **kw):
        state=dict(outcome='Correct the report',state='WAITING',phase='verification',
                   summary='Waiting for enough space',next_action='Resume when space is available',
                   owner_action=None,verdict='NO-DATA',debug='SYNTHETIC_SECRET_DO_NOT_ECHO',
                   internal_command='python3 scripts/cut.py --cold --approval-file secret')
        state.update(kw)
        return need('scripts/proof_card.py','workflow_card')(state)
    def test_default_flow_has_outcome_and_next_action_without_internals(self):
        got=self.card()
        self.assertIn('Correct the report',got)
        self.assertIn('space',got.lower())
        self.assertLessEqual(len(got),1200)
        for forbidden in ['--cold','--approval-file','scripts/cut.py','SYNTHETIC_SECRET_DO_NOT_ECHO']:
            self.assertNotIn(forbidden,got)
    def test_unknown_proof_is_not_presented_as_complete(self):
        got=self.card(state='DONE',verdict='NO-DATA').lower()
        self.assertNotIn('successfully completed',got)
        self.assertTrue('not verified' in got or 'no-data' in got or 'not yet verified' in got)
    def test_required_owner_action_has_scope_and_reason(self):
        got=self.card(owner_action=dict(action='Approve publication',scope='version 1.2.0',
                                        reason='Publishing changes the public release'))
        for value in ['Approve publication','version 1.2.0','public release']:
            self.assertIn(value,got)

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
