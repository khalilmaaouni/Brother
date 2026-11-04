#!/usr/bin/env python3
"""OUT1.k frozen contract, placed in the tree UNCHANGED from the 1.2.0 delivery pack's test_workflows.py.

The classes AccessContract and AssuranceAccess below are byte for byte the pack's (their sha256 is recorded with the commit
that placed them). The harness around them is the pack's own: need(), MissingContract and the PASS, FAIL, NO-DATA
result rows, with one change, ROOT: the pack defaults to a checkout path on one laptop; here it defaults to the checkout
holding this file. A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped
green.

Run: python3 -B scripts/test_assurance_access_contract.py --require-implementation
"""
import ast
import copy
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

class AccessContract:
    """Proposed shared door adapter: invoke_capability(spec, inputs, context, invoke).
    spec: ID/handler/prerequisites/required_authority. context: available/authority.
    invoke(handler, inputs) returns verdict/output/evidence_refs. The adapter must
    call the named existing handler, propagate evidence and never widen authority.
    These synthetic tests do NOT prove an installed domain engine was reached.
    """
    capability=''; handler=''
    def spec(self):
        return dict(id=self.capability,handler=self.handler,
                    prerequisites=['fixture'],required_authority=['read'])
    def run_adapter(self, context=None, reply=None):
        calls=[]
        def invoke(handler, inputs):
            calls.append((handler,inputs))
            return reply if reply is not None else dict(verdict='PASS',output='checked fixture',evidence_refs=['evidence-1'])
        got=need('scripts/door.py','invoke_capability')(
            self.spec(),{'source':'fixture'},
            context if context is not None else dict(available=['fixture'],authority=['read']),invoke)
        return got,calls
    def test_executes_existing_handler_and_preserves_evidence(self):
        got,calls=self.run_adapter()
        self.assertEqual([(self.handler,{'source':'fixture'})],calls)
        self.assertEqual('COMPLETE',got['state'])
        self.assertEqual(['evidence-1'],got['evidence_refs'])
        self.assertEqual('checked fixture',got['output'])
    def test_missing_prerequisite_does_not_launch(self):
        got,calls=self.run_adapter(dict(available=[],authority=['read']))
        self.assertEqual([],calls); self.assertNotEqual('COMPLETE',got['state'])
        self.assertIn('fixture',got['missing_prerequisites'])
    def test_missing_authority_cannot_be_filled_by_adapter(self):
        context=dict(available=['fixture'],authority=[]); before=copy.deepcopy(context)
        got,calls=self.run_adapter(context)
        self.assertEqual([],calls); self.assertEqual(before,context)
        self.assertNotEqual('COMPLETE',got['state'])
    def test_success_sentence_without_evidence_is_not_complete(self):
        got,calls=self.run_adapter(reply=dict(verdict='PASS',output='done',evidence_refs=[]))
        self.assertEqual(1,len(calls)); self.assertNotEqual('COMPLETE',got['state'])

class AssuranceAccess(AccessContract,unittest.TestCase):
    capability='assurance.review'; handler='assurance_existing_review'

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
