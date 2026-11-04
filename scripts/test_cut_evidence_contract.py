#!/usr/bin/env python3
"""CUT2.b frozen contract, placed in the tree UNCHANGED from the 1.2.0 delivery pack's test_workflows.py.

The IDENTITY constant and the classes Evidence and CheckSelection below are byte for byte the pack's (their sha256
is recorded with the commit that placed them).
The harness around it is the pack's own: need(), MissingContract and the PASS, FAIL, NO-DATA result rows, with one
change, ROOT: the pack defaults to a checkout path on one laptop; here it defaults to the checkout holding this file.
A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped green.

Run: python3 -B scripts/test_cut_evidence_contract.py --require-implementation
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

IDENTITY = dict(code='a'*64, checker='b'*64, policy='c'*64, interpreter='d'*64,
                environment='e'*64, export='f'*64, host='g'*64, home='h'*64)
class Evidence(unittest.TestCase):
    """CUT2.b: shared_evidence_usable(record, expected, now, verify) -> bool.
    verify is a trusted digest/provenance verifier, never a flag read from the record.
    All identities are opaque digests; missing, expired or corrupt proof misses.
    """
    def record(self, **kw):
        return dict(dict(schema=1, verdict='PASS', inputs=copy.deepcopy(IDENTITY),
                         created_at=10, valid_until=200, evidence_id='i'*64), **kw)
    def test_exact_fresh_verified_pass_can_be_reused(self):
        fn=need('scripts/cut_driver.py','shared_evidence_usable')
        calls=[]
        self.assertTrue(fn(self.record(),IDENTITY,100,lambda r: calls.append(r['evidence_id']) or True))
        self.assertEqual(['i'*64],calls)
    def test_every_dependency_change_invalidates(self):
        fn=need('scripts/cut_driver.py','shared_evidence_usable')
        for key in IDENTITY:
            with self.subTest(key=key):
                altered=dict(IDENTITY); altered[key]='z'*64
                self.assertFalse(fn(self.record(),altered,100,lambda _:True))
    def test_missing_dependency_or_nodata_never_hits(self):
        fn=need('scripts/cut_driver.py','shared_evidence_usable')
        missing=dict(IDENTITY); missing.pop('checker')
        self.assertFalse(fn(self.record(inputs=missing),IDENTITY,100,lambda _:True))
        self.assertFalse(fn(self.record(),missing,100,lambda _:True))
        for verdict in ['FAIL','NO-DATA','',None]:
            self.assertFalse(fn(self.record(verdict=verdict),IDENTITY,100,lambda _:True))
    def test_expiry_integrity_and_future_record_refuse(self):
        fn=need('scripts/cut_driver.py','shared_evidence_usable')
        self.assertFalse(fn(self.record(),IDENTITY,201,lambda _:True))
        self.assertFalse(fn(self.record(),IDENTITY,100,lambda _:False))
        self.assertFalse(fn(self.record(created_at=300),IDENTITY,100,lambda _:True))

class CheckSelection(unittest.TestCase):
    """CUT2.b: affected_checks(graph, changed_paths, known_base=True) -> IDs.
    Nodes: inputs (exact paths in these fixtures), deps (IDs), always (bool).
    Select direct changes, downstream consumers and their prerequisites.
    Unknown paths/base conservatively select all. Invalid graphs raise ValueError.
    This tests selection only, not cache trust or real dependency completeness.
    """
    def graph(self):
        return {'a':dict(inputs=['a.py'],deps=[],always=False),
                'a_test':dict(inputs=['test_a.py'],deps=['a'],always=False),
                'b':dict(inputs=['b.py'],deps=[],always=False),
                'guard':dict(inputs=['policy.json'],deps=[],always=True)}
    def select(self, paths, graph=None, known_base=True):
        return set(need('scripts/cut_driver.py','affected_checks')(
            self.graph() if graph is None else graph, paths, known_base=known_base))
    def test_leaf_change_reaches_consumer_and_keeps_guard(self):
        self.assertEqual({'a','a_test','guard'},self.select(['a.py']))
    def test_checker_change_keeps_its_prerequisite(self):
        self.assertEqual({'a','a_test','guard'},self.select(['test_a.py']))
    def test_unknown_path_or_baseline_selects_all(self):
        self.assertEqual(set(self.graph()),self.select(['new_unknown.py']))
        self.assertEqual(set(self.graph()),self.select([],known_base=False))
    def test_no_change_still_runs_mandatory_guard(self):
        self.assertEqual({'guard'},self.select([]))
    def test_shared_dependency_invalidates_both_consumers(self):
        graph=self.graph(); graph['b']['deps']=['a']
        self.assertEqual(set(graph),self.select(['a.py'],graph))
    def test_cycles_and_missing_dependencies_refuse(self):
        fn=need('scripts/cut_driver.py','affected_checks')
        graph=self.graph(); graph['a']['deps']=['a_test']
        with self.assertRaises(ValueError): fn(graph,['a.py'],known_base=True)
        graph=self.graph(); graph['a']['deps']=['missing']
        with self.assertRaises(ValueError): fn(graph,['a.py'],known_base=True)

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
