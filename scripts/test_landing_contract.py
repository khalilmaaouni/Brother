#!/usr/bin/env python3
"""LOOP1.d, the frozen pack contract for landing facts, in tree (1.2.0 delivery pack, test_workflows.py class Landing).

The class `Landing` below is a byte for byte copy of the frozen pack class; its sha256 is pinned in FROZEN_SHA256 and
test_the_frozen_class_is_unchanged refuses any edit to it. The scaffolding around it (need, Results, the main block) is
the pack's own, with the source root defaulting to this checkout instead of a fixed path.
usage: python3 -B scripts/test_landing_contract.py [Landing] [--require-implementation]
Missing contracts are NO-DATA, exit 2; --require-implementation makes them FAIL, exit 1. Assertion failures always FAIL."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get('BROTHER_SOURCE_ROOT', str(Path(__file__).resolve().parent.parent)))
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'scripts/loop')]
REQUIRE = '--require-implementation' in sys.argv
FROZEN_SHA256 = '048d22484d98bcaa26118f2f0083c6ab7e4f7bb5e61a373831914e9541640dce'
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

class Landing(unittest.TestCase):
    """LOOP1.d: landing_facts(events, unit, sub) -> {landed, commit, reason}.
    Events require schema=1, sequence, type, unit, sub, commit, evidence_id.
    Runtime ancestry/proof validation is additionally required at the real caller.
    """
    def fact(self, events):
        return need('scripts/loop/plan_store.py','landing_facts')(events, 'U1', 'U1.a')
    def event(self, **kw):
        return dict(dict(schema=1, sequence=1, type='landed', unit='U1', sub='U1.a',
                         commit='a'*40, evidence_id='b'*64), **kw)
    def test_landing_needs_structured_proof(self):
        self.assertTrue(self.fact([self.event()])['landed'])
        self.assertFalse(self.fact([self.event(evidence_id='')])['landed'])
    def test_revert_removes_current_landing(self):
        self.assertFalse(self.fact([self.event(), self.event(type='reverted', sequence=2)])['landed'])
    def test_sibling_and_replayed_event_cannot_restore_revert(self):
        got = self.fact([self.event(), self.event(type='reverted', sequence=2),
                         self.event(sub='U1.b', sequence=3), self.event()])
        self.assertFalse(got['landed'])

class FrozenPin(unittest.TestCase):
    def test_the_frozen_class_is_unchanged(self):
        source = Path(__file__).read_text()
        node = next(n for n in ast.parse(source).body if getattr(n, 'name', None) == 'Landing')
        text = ''.join(source.splitlines(keepends=True)[node.lineno-1:node.end_lineno])
        self.assertEqual(FROZEN_SHA256, hashlib.sha256(text.encode()).hexdigest())

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
