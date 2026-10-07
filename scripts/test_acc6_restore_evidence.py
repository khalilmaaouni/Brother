#!/usr/bin/env python3
'''test_acc6_restore_evidence: the ACC6.a acceptance suite for the enterprise
restore drill record the readiness gate is granted from.

WHAT THIS PROVES. The unit's own control points, on isolated records first:
the record names the drill, the tools directory and the drill's two tenants;
it passed; its checks list is nonempty and every check passed; checks_total
equals the length of the checks list; checks_failed is zero;
unvalidated_categories is empty; every covered path the drill declares is named
with a matching sha256; and the run date is inside the gate's own inclusive
freshness bar. Then the same reader is pointed at the record this checkout
actually tracks, so a record that drifts out of step with the code it covers
goes red here.

WHAT THIS SUITE NEVER DOES. It never writes to the tracked record, never runs
the drill and never fakes a timestamp: every fixture lives in its own scratch
tree, and the tracked record is judged at the run date it declares. Re-running
the drill is the release owner's data action, not this suite's.

WHY IT DOES NOT IMPORT THE GATE OR THE DRILL. Both start processes for a
living, so this suite reaches their declarations through the reader under test
rather than importing them.

THE DECLARATION READERS ARE PUBLIC, so a hostile input reaches them directly:
None, a wrong type, empty text, bytes and a bool are refused with ValueError
before any path is joined, never a raw TypeError and never a silent accept.

Python 3.9 compatible, standard library only. No em or en dashes.
'''
import hashlib
import json
import os
import sys
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    from acc6_restore_evidence import (declared_drill_coverage,
                                       declared_restore_record,
                                       restore_evidence_errors)
except ImportError as _import_error:
    # A missing reader REFUSES. It is never replaced by a stand in that
    # approves, and this is the shape the red run takes without the reader.
    _MISSING = _import_error

    def declared_restore_record(root):
        raise RuntimeError('acc6_restore_evidence is missing (%s): refusing '
                           'rather than approving' % (_MISSING,))

    def declared_drill_coverage(root):
        raise RuntimeError('acc6_restore_evidence is missing (%s): refusing '
                           'rather than approving' % (_MISSING,))

    def restore_evidence_errors(root, today):
        raise RuntimeError('acc6_restore_evidence is missing (%s): refusing '
                           'rather than approving' % (_MISSING,))


GATE_FIXTURE_PATH = 'scripts/readiness_gate.py'
DRILL_FIXTURE_PATH = 'scripts/restore_drill_enterprise.py'
FIXTURE_RECORD_PATH = 'docs/plan/RESTORE-DRILL-ENTERPRISE-RESULT.json'
FIXTURE_TOOLS_DIR = 'products/brothermode/tools'
FIXTURE_TOOL_NAMES = ('bm_vault.py', 'bm_vault_assertions.py')
FIXTURE_COVERED = (DRILL_FIXTURE_PATH,) + tuple(
    '%s/%s' % (FIXTURE_TOOLS_DIR, name) for name in FIXTURE_TOOL_NAMES)
FIXTURE_TENANTS = ['tenant-alpha', 'tenant-beta']
FIXTURE_DATE = date(2026, 9, 28)

GATE_FIXTURE = '''import os

MAX_AGE_DAYS = 7

ITEMS = [
    {'id': 'restore-drill', 'title': 'Restore drill',
     'critical': True, 'kind': 'record',
     'max_age_days': MAX_AGE_DAYS,
     'path': os.path.join('docs', 'plan',
                          'RESTORE-DRILL-ENTERPRISE-RESULT.json'),
     'blocker': None},
]
'''

DRILL_FIXTURE = '''COVERED_TOOLS = (
    'bm_vault.py',
    'bm_vault_assertions.py',
)


def main(argv=None):
    tenants = [
        ('tenant-alpha', 'CANARY-ALPHA', 'CANARY-BETA'),
        ('tenant-beta', 'CANARY-BETA', 'CANARY-ALPHA'),
    ]
    return tenants
'''


def _scratch_dir():
    '''A fresh directory for one fixture tree, removed by the test that made
    it. No shared temp helper is imported: a new file whose import graph
    reaches a module that runs or cleans up processes is refused.'''
    bases = [os.environ.get('TMPDIR'), os.environ.get('TEMP'), '/tmp',
             os.getcwd()]
    counter = 0
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        for _attempt in range(50):
            counter += 1
            path = os.path.join(base, 'acc6a-restore-evidence-%d-%d'
                                % (os.getpid(), counter))
            try:
                os.makedirs(path)
            except OSError:
                continue
            return path
    raise AssertionError('no writable scratch directory was available')


def _remove_tree(root):
    '''Delete one fixture tree bottom up, never raising about what is already
    gone: a cleanup must not fail a finished proof.'''
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name))
            except OSError:
                pass
        for name in dirnames:
            try:
                os.rmdir(os.path.join(dirpath, name))
            except OSError:
                pass
    try:
        os.rmdir(root)
    except OSError:
        pass


class _FixtureTree(unittest.TestCase):
    '''One isolated tree: a gate declaration, a drill declaration, the covered
    files and one record. Nothing here reads or writes the real checkout.'''

    def setUp(self):
        self.root = _scratch_dir()
        self.addCleanup(_remove_tree, self.root)
        self.write_file(GATE_FIXTURE_PATH, GATE_FIXTURE)
        self.hashes = {}
        # The drill's own file carries the declaration and is covered by its
        # own hash, exactly as the real drill script is.
        self.write_file(DRILL_FIXTURE_PATH, DRILL_FIXTURE)
        self.hashes[DRILL_FIXTURE_PATH] = hashlib.sha256(
            DRILL_FIXTURE.encode('utf-8')).hexdigest()
        for relpath in FIXTURE_COVERED:
            if relpath == DRILL_FIXTURE_PATH:
                continue
            body = 'fixture bytes for %s\n' % relpath
            self.write_file(relpath, body)
            self.hashes[relpath] = hashlib.sha256(
                body.encode('utf-8')).hexdigest()

    def write_file(self, relpath, text):
        path = os.path.join(self.root, relpath)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(text.encode('utf-8'))

    def record(self, **overrides):
        doc = {
            'drill': 'restore_drill_enterprise',
            'drill_date': FIXTURE_DATE.isoformat(),
            'covered': [{'path': relpath, 'sha256': self.hashes[relpath]}
                        for relpath in FIXTURE_COVERED],
            'tools_dir': FIXTURE_TOOLS_DIR,
            'tenants': list(FIXTURE_TENANTS),
            'passed': True,
            'checks': [{'name': 'fixture check', 'tenant': 'tenant-alpha',
                        'passed': True}],
            'checks_total': 1,
            'checks_failed': 0,
            'unvalidated_categories': [],
        }
        doc.update(overrides)
        return doc

    def write_record(self, **overrides):
        doc = self.record(**overrides)
        self.write_file(FIXTURE_RECORD_PATH,
                        json.dumps(doc, indent=2, sort_keys=True) + '\n')
        return doc

    def errors(self, today=None, **overrides):
        self.write_record(**overrides)
        when = FIXTURE_DATE if today is None else today
        return restore_evidence_errors(self.root, when)


class TheRecordTheGateDeclares(_FixtureTree):
    '''REQ-ACC6-A-RECORD, REQ-ACC6-A-BOUND and REQ-ACC6-A-FRESH on isolated
    records: every rule the unit names is shown able to go red.'''

    def test_a_record_the_unit_describes_has_no_errors(self):
        self.assertEqual([], self.errors())

    def test_a_stale_drill_date_is_named(self):
        stale = (FIXTURE_DATE - timedelta(days=8)).isoformat()
        errors = self.errors(drill_date=stale)
        self.assertTrue(any(error.startswith('STALE:') for error in errors),
                        errors)

    def test_a_drill_date_at_the_bar_is_not_named(self):
        edge = (FIXTURE_DATE - timedelta(days=7)).isoformat()
        self.assertEqual([], self.errors(drill_date=edge))

    def test_a_drill_date_after_today_is_named(self):
        future = (FIXTURE_DATE + timedelta(days=1)).isoformat()
        errors = self.errors(drill_date=future)
        self.assertTrue(any('in the future' in error for error in errors),
                        errors)

    def test_the_freshness_bar_comes_from_the_gate_declaration(self):
        self.write_file(GATE_FIXTURE_PATH,
                        GATE_FIXTURE.replace('= 7', '= 3', 1))
        five = (FIXTURE_DATE - timedelta(days=5)).isoformat()
        errors = self.errors(drill_date=five)
        self.assertTrue(any('3 day bar' in error for error in errors), errors)

    def test_a_missing_record_is_named_no_data(self):
        path = os.path.join(self.root, FIXTURE_RECORD_PATH)
        if os.path.exists(path):
            os.remove(path)
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith('NO-DATA:'), errors)
        self.assertIn('does not exist', errors[0])

    def test_record_bytes_that_are_not_utf8_are_named_no_data(self):
        path = os.path.join(self.root, FIXTURE_RECORD_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(b'\xff\xfe\x00 not utf-8 text')
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)

    def test_a_record_that_is_not_one_json_object_is_named_no_data(self):
        self.write_file(FIXTURE_RECORD_PATH, '[1, 2, 3]\n')
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)

    def test_a_wrong_drill_is_named(self):
        errors = self.errors(drill='some_other_drill')
        self.assertTrue(any('record drill' in error for error in errors),
                        errors)

    def test_wrong_tenants_are_named(self):
        errors = self.errors(tenants=['tenant-alpha', 'tenant-gamma'])
        self.assertTrue(any('tenants' in error for error in errors), errors)

    def test_a_tenant_list_that_is_not_text_is_named(self):
        errors = self.errors(tenants=['tenant-alpha', 7])
        self.assertTrue(any('tenants' in error for error in errors), errors)

    def test_a_wrong_tools_dir_is_named(self):
        errors = self.errors(tools_dir='products/somewhere-else/tools')
        self.assertTrue(any('tools_dir' in error for error in errors), errors)

    def test_passed_false_is_named(self):
        errors = self.errors(passed=False)
        self.assertTrue(any('record passed' in error for error in errors),
                        errors)

    def test_a_nan_passed_is_named_not_accepted(self):
        errors = self.errors(passed=float('nan'))
        self.assertTrue(any('record passed' in error for error in errors),
                        errors)

    def test_unvalidated_categories_must_be_empty(self):
        errors = self.errors(
            unvalidated_categories=['behavior legal_hold_active'])
        self.assertTrue(any('unvalidated_categories' in error for error in errors),
                        errors)

    def test_a_failed_check_is_named(self):
        errors = self.errors(checks=[{'name': 'fixture check',
                                      'passed': False}], checks_failed=1)
        self.assertTrue(any('did not pass' in error for error in errors), errors)
        self.assertTrue(any('checks_failed is 1' in error for error in errors),
                        errors)

    def test_checks_that_are_not_a_list_are_named(self):
        errors = self.errors(checks='yes')
        self.assertTrue(any('checks is not a list' in error for error in errors),
                        errors)

    def test_an_empty_checks_list_is_named(self):
        errors = self.errors(checks=[], checks_total=0, checks_failed=0)
        self.assertTrue(any('checks is empty' in error for error in errors),
                        errors)

    def test_checks_total_that_disagrees_with_checks_raises(self):
        self.write_record(checks_total=2)
        with self.assertRaises(ValueError):
            restore_evidence_errors(self.root, FIXTURE_DATE)

    def test_checks_failed_that_disagrees_with_checks_raises(self):
        self.write_record(checks_failed=3)
        with self.assertRaises(ValueError):
            restore_evidence_errors(self.root, FIXTURE_DATE)

    def test_a_count_that_is_not_an_integer_raises(self):
        for bad in (True, False, 1.5, '1', None, [1]):
            self.write_record(checks_total=bad)
            with self.assertRaises(ValueError):
                restore_evidence_errors(self.root, FIXTURE_DATE)

    def test_a_nan_count_raises(self):
        self.write_record(checks_failed=float('nan'))
        with self.assertRaises(ValueError):
            restore_evidence_errors(self.root, FIXTURE_DATE)

    def test_a_changed_covered_file_is_named(self):
        relpath = FIXTURE_COVERED[1]
        self.write_file(relpath, 'changed after the record was written\n')
        errors = self.errors()
        self.assertTrue(any(error.startswith('INVALID: covered %s' % relpath)
                            for error in errors), errors)

    def test_a_missing_covered_file_is_named_no_data(self):
        relpath = FIXTURE_COVERED[1]
        os.remove(os.path.join(self.root, relpath))
        errors = self.errors()
        self.assertTrue(any(error.startswith('NO-DATA:') and relpath in error
                            for error in errors), errors)

    def test_a_dropped_covered_path_is_named(self):
        dropped = FIXTURE_COVERED[2]
        kept = [{'path': relpath, 'sha256': self.hashes[relpath]}
                for relpath in FIXTURE_COVERED if relpath != dropped]
        errors = self.errors(covered=kept)
        self.assertTrue(any(error.startswith('NO-DATA:') and dropped in error
                            for error in errors), errors)

    def test_an_extra_covered_path_is_named(self):
        entries = [{'path': relpath, 'sha256': self.hashes[relpath]}
                   for relpath in FIXTURE_COVERED]
        entries.append({'path': 'scripts/not_covered_by_the_drill.py',
                        'sha256': '0' * 64})
        errors = self.errors(covered=entries)
        self.assertTrue(any('does not cover' in error for error in errors),
                        errors)

    def test_a_covered_path_outside_the_tree_is_refused(self):
        entries = [{'path': relpath, 'sha256': self.hashes[relpath]}
                   for relpath in FIXTURE_COVERED]
        entries.append({'path': '../../etc/passwd', 'sha256': '0' * 64})
        errors = self.errors(covered=entries)
        self.assertTrue(any('outside the tree' in error for error in errors),
                        errors)

    def test_a_covered_entry_with_a_hostile_path_is_named(self):
        errors = self.errors(covered=[{'path': ['not', 'text'],
                                       'sha256': '0' * 64}])
        self.assertTrue(any('covered[0]' in error for error in errors), errors)

    def test_a_covered_entry_that_is_not_an_object_is_named(self):
        errors = self.errors(covered=[['not', 'an', 'object']])
        self.assertTrue(any('covered[0]' in error for error in errors), errors)

    def test_a_covered_list_that_is_not_a_list_is_named(self):
        errors = self.errors(covered={'path': 'scripts/one.py'})
        self.assertTrue(any('covered is not a list' in error for error in errors),
                        errors)

    def test_a_gate_without_the_restore_item_is_named_no_data(self):
        self.write_file(GATE_FIXTURE_PATH, 'ITEMS = [{"id": "other"}]\n')
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)

    def test_a_missing_gate_declaration_is_named_no_data(self):
        os.remove(os.path.join(self.root, GATE_FIXTURE_PATH))
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)

    def test_a_missing_drill_declaration_is_named_no_data(self):
        os.remove(os.path.join(self.root, DRILL_FIXTURE_PATH))
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)

    def test_a_gate_that_declares_an_absolute_record_path_is_no_data(self):
        self.write_file(GATE_FIXTURE_PATH,
                        "ITEMS = [{'id': 'restore-drill', "
                        "'path': '/etc/passwd', 'max_age_days': 7}]\n")
        errors = restore_evidence_errors(self.root, FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)


class HostileArgumentsAreRefused(unittest.TestCase):
    '''None, a wrong type, empty, NaN and a bool are refused with ValueError,
    never a crash and never a silent accept.'''

    def test_a_none_root_is_refused(self):
        with self.assertRaises(ValueError):
            restore_evidence_errors(None, FIXTURE_DATE)

    def test_an_empty_root_is_refused(self):
        with self.assertRaises(ValueError):
            restore_evidence_errors('', FIXTURE_DATE)

    def test_a_non_text_root_is_refused(self):
        for bad in (b'/tmp', 17, 1.5, float('nan'), ['/tmp'], {'/tmp': 1}, True):
            with self.assertRaises(ValueError):
                restore_evidence_errors(bad, FIXTURE_DATE)

    def test_a_today_that_is_not_a_date_is_refused(self):
        for bad in (None, '2026-09-28', 0, 1.5, float('nan'), b'2026-09-28',
                    True, [], date):
            with self.assertRaises(ValueError):
                restore_evidence_errors('no-such-tree-acc6a', bad)

    def test_a_missing_tree_is_no_data_not_a_crash(self):
        errors = restore_evidence_errors('no-such-tree-acc6a', FIXTURE_DATE)
        self.assertTrue(errors and errors[0].startswith('NO-DATA:'), errors)


class TheDeclarationReadersRefuseHostileRoots(unittest.TestCase):
    '''declared_restore_record and declared_drill_coverage are public, so a
    hostile input reaches them directly. None, a wrong type, empty text, bytes
    and a bool are refused with ValueError before any path is joined, never a
    raw TypeError and never a silent accept. One test per finding class.'''

    def test_declared_drill_coverage_refuses_a_none_root(self):
        with self.assertRaises(ValueError):
            declared_drill_coverage(None)

    def test_declared_drill_coverage_refuses_an_int_root(self):
        with self.assertRaises(ValueError):
            declared_drill_coverage(17)

    def test_declared_restore_record_refuses_a_none_root(self):
        with self.assertRaises(ValueError):
            declared_restore_record(None)

    def test_declared_restore_record_refuses_an_int_root(self):
        with self.assertRaises(ValueError):
            declared_restore_record(17)

    def test_every_public_reader_refuses_a_hostile_root(self):
        for bad in (None, '', b'/tmp', 17, 1.5, float('nan'), ['/tmp'],
                    {'/tmp': 1}, True):
            for reader in (declared_restore_record, declared_drill_coverage):
                with self.assertRaises(ValueError):
                    reader(bad)


GATE_PATH = os.path.join(REPO_ROOT, 'scripts', 'readiness_gate.py')
DRILL_PATH = os.path.join(REPO_ROOT, 'scripts', 'restore_drill_enterprise.py')
TRACKED_RECORD = os.path.join(REPO_ROOT, 'docs', 'plan',
                              'RESTORE-DRILL-ENTERPRISE-RESULT.json')


class TheDeclarationsThisReaderReusesAreReadable(unittest.TestCase):
    '''The record path, the freshness bar, the covered set and the tenant names
    stay single sourced in the gate and the drill. A declaration that stops
    being readable is NO-DATA, never a silent pass.'''

    @unittest.skipUnless(os.path.isfile(GATE_PATH),
                         'readiness_gate.py is not in this checkout')
    def test_the_gate_declares_the_restore_drill_record(self):
        declared = declared_restore_record(REPO_ROOT)
        self.assertIsNotNone(
            declared,
            'scripts/readiness_gate.py declares no readable restore-drill item')
        relpath, max_age_days = declared
        self.assertTrue(relpath.endswith('.json'), relpath)
        self.assertGreaterEqual(max_age_days, 0)

    @unittest.skipUnless(os.path.isfile(DRILL_PATH),
                         'restore_drill_enterprise.py is not in this checkout')
    def test_the_drill_declares_nine_covered_paths_and_two_tenants(self):
        declared = declared_drill_coverage(REPO_ROOT)
        self.assertIsNotNone(
            declared,
            'scripts/restore_drill_enterprise.py declares no readable '
            'COVERED_TOOLS and tenants')
        covered, tenants = declared
        self.assertIn('scripts/restore_drill_enterprise.py', covered)
        self.assertEqual(9, len(covered))
        self.assertEqual(2, len(tenants))


class TheTrackedRecordStandsAtItsOwnRunDate(unittest.TestCase):
    '''The record this checkout actually tracks, judged at the run date it
    declares: the same reader, the same rules, on the real bytes.

    WHY NOT date.today() HERE. The freshness half of the requirement is a DATA
    question: only a real drill run can replace the record, and this suite must
    never fake a timestamp to make itself green. The freshness rule itself is
    asserted with injected dates in TheRecordTheGateDeclares; this class
    asserts everything else about the real record, covered hashes included. On
    a checkout without docs/plan it skips, and every rule above still runs on
    fixtures.'''

    @unittest.skipUnless(os.path.isfile(TRACKED_RECORD),
                         'the tracked restore-drill record is not in this checkout')
    def test_the_tracked_record_has_no_named_errors_at_its_own_run_date(self):
        with open(TRACKED_RECORD, 'rb') as handle:
            raw = handle.read()
        doc = json.loads(raw.decode('utf-8'))
        self.assertIsInstance(doc, dict,
                              'the tracked record is not one JSON object')
        ran = doc.get('drill_date')
        self.assertIsInstance(ran, str,
                              'the tracked record carries no drill_date')
        errors = restore_evidence_errors(REPO_ROOT, date.fromisoformat(ran))
        self.assertEqual([], errors,
                         'the tracked restore-drill record cannot stand: %s'
                         % '; '.join(errors))


if __name__ == '__main__':
    unittest.main()
