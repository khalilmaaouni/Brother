#!/usr/bin/env python3
"""LOOP1.e frozen contract, placed in the tree UNCHANGED from the 1.2.0 delivery pack's test_workflows.py.

The class Recovery below is byte for byte the pack's class (its sha256 is recorded with the commit that placed it).
The harness around it is the pack's own: need(), MissingContract and the PASS, FAIL, NO-DATA result rows, with one
change, ROOT: the pack defaults to a checkout path on one laptop; here it defaults to the checkout holding this file.
A missing contract is NO-DATA (exit 2); --require-implementation makes it FAIL (exit 1). No skipped green.

Run: python3 -B scripts/test_recovery_contract.py --require-implementation
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

class Recovery(unittest.TestCase):
    """LOOP1.e: closure_retry(previous, current) -> {action, reason}.
    current carries known code/check/environment, attempts and existing limit.
    previous adds kind (assertion/infrastructure/queue), check_started and those identities.
    """
    def choose(self, kind='infrastructure', previous_env='bad', **changes):
        current = dict(code='a'*40, check='b'*64, environment='healthy', attempts=1, limit=2)
        current.update(changes)
        previous = dict(code='a'*40, check='b'*64, environment=previous_env,
                        kind=kind, check_started=kind != 'queue')
        return need('scripts/loop/land_batch.py','closure_retry')(previous, current)
    def test_recovered_environment_allows_one_bounded_retry(self):
        self.assertEqual('RUN', self.choose()['action'])
        self.assertNotEqual('RUN', self.choose(attempts=2)['action'])
    def test_unchanged_infrastructure_waits_for_recovery(self):
        self.assertEqual('WAIT', self.choose(previous_env='healthy')['action'])
    def test_assertion_failure_needs_changed_code_or_checker(self):
        self.assertEqual('WAIT', self.choose(kind='assertion')['action'])
        self.assertEqual('RUN', self.choose(kind='assertion', code='c'*40)['action'])
    def test_queue_timeout_does_not_claim_test_failure(self):
        got = self.choose(kind='queue')
        self.assertEqual('RUN', got['action'])
        self.assertNotIn('test failed', got['reason'].lower())
    def test_unknown_identity_cannot_authorize_retry(self):
        self.assertNotEqual('RUN', self.choose(check='')['action'])

class RecoveryEdges(unittest.TestCase):
    """Integrator cases beside the frozen class above (which stays byte for byte the pack's): each isolates one branch
    the pack's fixtures reach only together with another (its queue fixture also changes the environment)."""
    def choose(self, previous, **changes):
        current = dict(code='a'*40, check='b'*64, environment='healthy', attempts=1, limit=2)
        current.update(changes)
        base = dict(code='a'*40, check='b'*64, environment='healthy', kind='assertion', check_started=True)
        base.update(previous)
        return need('scripts/loop/land_batch.py','closure_retry')(base, current)
    def test_a_queue_wait_runs_with_nothing_else_changed(self):
        self.assertEqual('RUN', self.choose(dict(kind='queue', check_started=False))['action'])
    def test_an_unknown_kind_needs_input(self):
        self.assertEqual('NEEDS_INPUT', self.choose(dict(kind='flaky'))['action'])
    def test_an_unknown_previous_identity_cannot_show_a_change(self):
        self.assertEqual('NEEDS_INPUT', self.choose(dict(code=''), code='c'*40)['action'])
        self.assertEqual('NEEDS_INPUT', self.choose(dict(kind='infrastructure', environment=''))['action'])
    def test_a_spent_limit_holds_even_a_queue_wait(self):
        self.assertEqual('NEEDS_INPUT', self.choose(dict(kind='queue', check_started=False), attempts=2)['action'])
    def test_a_changed_checker_reruns_an_assertion_failure(self):
        self.assertEqual('RUN', self.choose({}, check='c'*64)['action'])

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
