#!/usr/bin/env python3
'''acc6_restore_evidence: ACC6.a's read only reader for the enterprise restore
drill record that the readiness gate's restore-drill row is granted from.

WHY THIS EXISTS (unit ACC6, sub unit ACC6.a). readiness_gate.py grants its
restore-drill row from one file: the JSON record a real, populated, two-tenant
backup, destruction, restore and validation drill wrote, at the exact path the
gate's own ITEMS declaration names. Main's record is old, so that row is red,
and a gate that is always red trains merging past red. This module is the
acceptance reader for that record: it names every reason the tracked record
cannot stand, so a record that goes stale again, or drifts away from the code
it says it ran against, turns the acceptance command red again.

WHAT THIS MODULE NEVER DOES. It never writes, moves or deletes the record, and
it never runs the drill. Reading is the whole job here; re-running the drill is
the release owner's data action, and no timestamp is edited to fake it.

WHERE THE DECLARATIONS COME FROM. The record path and the freshness bar come
from readiness_gate.ITEMS, and the covered set and the tenant names come from
restore_drill_enterprise.py's own COVERED_TOOLS and tenant list. Both are read
with ast, never imported: importing the gate would drag its process starting
import graph in merely to read one path and one integer. A declaration that
cannot be read is NO-DATA, never a guess.

ONE ROOT GUARD, at the boundary. Every public reader routes its root through
_require_root before any path is joined, and the low level module parser routes
through it too, so a wrong type, None, empty text, bytes, a bool or a NaN is
refused with ValueError at the first step, never a raw TypeError out of
os.path.join and never a silent accept.

Python 3.9 compatible, standard library only. Files are read as bytes. No em or
en dashes anywhere in this file.
'''
import ast
import hashlib
import json
import os
from datetime import date, datetime

GATE_RELPATH = os.path.join('scripts', 'readiness_gate.py')
DRILL_RELPATH = os.path.join('scripts', 'restore_drill_enterprise.py')
RECORD_ITEM_ID = 'restore-drill'
EXPECTED_DRILL = 'restore_drill_enterprise'
EXPECTED_TOOLS_DIR = 'products/brothermode/tools'
#: Sentinel for "this constant expression is not one of the shapes this reader
#: understands". Every caller turns it into NO-DATA rather than guessing.
UNKNOWN = object()


def _require_root(root):
    '''Refuse every root that is not usable nonempty text. This is the one
    validation every reader routes through: None, a wrong type, empty text,
    bytes, a bool and a float (NaN included) are corrupt input and raise
    ValueError, never a raw interpreter exception and never a silent accept.'''
    if isinstance(root, bool) or not isinstance(root, str) or not root:
        raise ValueError('root must be a nonempty str path, got %r' % (root,))
    return root


def _read_bytes(path):
    '''The file's bytes, or None when the path cannot be read at all. A
    missing, unreadable or directory path becomes NO-DATA at every caller,
    never a crash.'''
    try:
        with open(path, 'rb') as handle:
            return handle.read()
    except OSError:
        return None


def _parse_module(root, relpath):
    '''The parsed module at root/relpath, or None when it cannot be read and
    parsed. Empty, binary or syntactically broken source is NO-DATA, never a
    crash. root is refused here first, so the join below is handed str
    parts only.'''
    root = _require_root(root)
    raw = _read_bytes(os.path.join(root, relpath))
    if raw is None:
        return None
    try:
        return ast.parse(raw, filename=relpath)
    except (SyntaxError, ValueError):
        return None


def _literal(node, consts):
    '''The value of a constant expression, or UNKNOWN.

    Only the shapes these two declarations actually use are understood:
    literal constants, module level names, lists, tuples, dicts, string
    concatenation, and os.path.join of string parts. Everything else is
    UNKNOWN, and every caller turns UNKNOWN into NO-DATA rather than guessing.
    '''
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id, UNKNOWN)
    if isinstance(node, ast.List):
        values = [_literal(element, consts) for element in node.elts]
        return UNKNOWN if UNKNOWN in values else values
    if isinstance(node, ast.Tuple):
        values = [_literal(element, consts) for element in node.elts]
        return UNKNOWN if UNKNOWN in values else tuple(values)
    if isinstance(node, ast.Dict):
        keys = [_literal(key, consts) for key in node.keys]
        values = [_literal(value, consts) for value in node.values]
        if UNKNOWN in keys or UNKNOWN in values:
            return UNKNOWN
        try:
            return dict(zip(keys, values))
        except TypeError:
            return UNKNOWN
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr == 'join':
            parts = [_literal(argument, consts) for argument in node.args]
            if parts and all(isinstance(part, str) for part in parts):
                return os.path.join(*parts)
        return UNKNOWN
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal(node.left, consts)
        right = _literal(node.right, consts)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        return UNKNOWN
    return UNKNOWN


def _module_constants(tree):
    '''Module level NAME = <constant> values, so a declaration may name a
    constant instead of repeating the literal value.'''
    consts = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _literal(node.value, consts)
        if value is not UNKNOWN:
            consts[target.id] = value
    return consts


def declared_restore_record(root):
    '''(record relpath, max age in days) from the gate's restore-drill item, or
    None when that declaration cannot be read.

    The gate is parsed, never imported: this reader must not pull the gate's
    process starting import graph in to read one path and one integer. root is
    refused by _require_root before any path is joined. A declaration whose
    path reaches outside the tree, or whose bar is not a non negative integer,
    is unreadable, and an unreadable declaration refuses.'''
    tree = _parse_module(root, GATE_RELPATH)
    if tree is None:
        return None
    consts = _module_constants(tree)
    items = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == 'ITEMS':
            candidate = _literal(node.value, consts)
            items = candidate if isinstance(candidate, list) else None
            break
    if items is None:
        return None
    for item in items:
        if not isinstance(item, dict) or item.get('id') != RECORD_ITEM_ID:
            continue
        relpath = item.get('path')
        max_age_days = item.get('max_age_days')
        if not isinstance(relpath, str) or not relpath:
            return None
        if os.path.isabs(relpath) or '..' in relpath.split('/'):
            return None
        if isinstance(max_age_days, bool) or not isinstance(max_age_days, int):
            return None
        if max_age_days < 0:
            return None
        return relpath, max_age_days
    return None


def _drill_tenants(tree):
    '''The tenant names the drill's own run declares, or None. Each tenant is a
    tuple whose first element is the tenant name used in the record.'''
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != 'tenants':
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        names = []
        for element in node.value.elts:
            if not isinstance(element, (ast.Tuple, ast.List)) or not element.elts:
                return None
            first = element.elts[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                return None
            names.append(first.value)
        return names or None
    return None


def declared_drill_coverage(root):
    '''(covered relpaths, tenant names) declared by the drill itself, or None
    when that cannot be read. root is refused by _require_root before any path
    is joined. The covered set is the drill script itself plus every vault tool
    the drill's own COVERED_TOOLS names, under the tools directory the record
    names: nine paths today, derived rather than frozen.'''
    tree = _parse_module(root, DRILL_RELPATH)
    if tree is None:
        return None
    consts = _module_constants(tree)
    tools = consts.get('COVERED_TOOLS')
    if not isinstance(tools, (list, tuple)) or not tools:
        return None
    if not all(isinstance(name, str) and name for name in tools):
        return None
    tenants = _drill_tenants(tree)
    if not tenants:
        return None
    covered = {DRILL_RELPATH.replace(os.sep, '/')}
    for name in tools:
        covered.add('%s/%s' % (EXPECTED_TOOLS_DIR, name))
    return covered, tenants


def _identity_errors(doc, expected_tenants):
    '''Which drill wrote the record, against which tools directory, for which
    tenants, and whether it passed.'''
    errors = []
    if doc.get('drill') != EXPECTED_DRILL:
        errors.append('INVALID: record drill is %r, not %r'
                      % (doc.get('drill'), EXPECTED_DRILL))
    tenants = doc.get('tenants')
    if not isinstance(tenants, list) or not all(isinstance(name, str)
                                                for name in tenants):
        errors.append('INVALID: record tenants is not a list of tenant names: %r'
                      % (tenants,))
    elif tenants != expected_tenants:
        errors.append('INVALID: record tenants %r are not the tenants the '
                      'drill declares %r' % (tenants, expected_tenants))
    if doc.get('tools_dir') != EXPECTED_TOOLS_DIR:
        errors.append('INVALID: record tools_dir is %r, not %r'
                      % (doc.get('tools_dir'), EXPECTED_TOOLS_DIR))
    if doc.get('passed') is not True:
        errors.append('INVALID: record passed is %r, not true'
                      % (doc.get('passed'),))
    unvalidated = doc.get('unvalidated_categories')
    if not isinstance(unvalidated, list):
        errors.append('INVALID: record unvalidated_categories is not a list: %r'
                      % (unvalidated,))
    elif unvalidated:
        errors.append('INVALID: record unvalidated_categories is not empty: %r'
                      % (unvalidated,))
    return errors


def _checks_errors(doc):
    '''The record's checks list and its counts. Counts that cannot all be true
    are corrupt input and raise; every other defect is named.'''
    checks = doc.get('checks')
    if not isinstance(checks, list):
        return ['INVALID: record checks is not a list: %r' % (checks,)]
    errors = []
    checks_total = doc.get('checks_total')
    checks_failed = doc.get('checks_failed')
    for field, value in (('checks_total', checks_total),
                         ('checks_failed', checks_failed)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('record %s is %r, not an integer count'
                             % (field, value))
    nonpassing = 0
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            nonpassing += 1
            errors.append('INVALID: record checks[%d] is not an object: %r'
                          % (index, check))
            continue
        if check.get('passed') is not True:
            nonpassing += 1
            errors.append('INVALID: record checks[%d] %r did not pass'
                          % (index, check.get('name')))
    if checks_total != len(checks) or checks_failed != nonpassing:
        raise ValueError('record counts cannot all be true: checks_total=%r '
                         'len(checks)=%d checks_failed=%r nonpassing=%d'
                         % (checks_total, len(checks), checks_failed,
                            nonpassing))
    if not checks:
        errors.append('INVALID: record checks is empty')
    if checks_failed != 0:
        errors.append('INVALID: record checks_failed is %d, not 0'
                      % (checks_failed,))
    return errors


def _covered_errors(doc, root, expected_covered):
    '''The covered list: which files the drill ran against, and whether their
    bytes in this tree are still the bytes the record names.'''
    covered = doc.get('covered')
    if not isinstance(covered, list):
        return ['INVALID: record covered is not a list: %r' % (covered,)]
    errors = []
    declared = {}
    for index, entry in enumerate(covered):
        if not isinstance(entry, dict):
            errors.append('INVALID: record covered[%d] is not an object: %r'
                          % (index, entry))
            continue
        relpath = entry.get('path')
        digest = entry.get('sha256')
        if not isinstance(relpath, str) or not relpath:
            errors.append('INVALID: record covered[%d] carries no text path: %r'
                          % (index, relpath))
            continue
        if os.path.isabs(relpath) or '..' in relpath.split('/'):
            errors.append('INVALID: record covered names a path outside the '
                          'tree: %r' % relpath)
            continue
        if not isinstance(digest, str) or not digest:
            errors.append('INVALID: record covered[%d] %s carries no sha256'
                          % (index, relpath))
            continue
        if relpath in declared:
            errors.append('INVALID: record covered names %s twice' % relpath)
            continue
        declared[relpath] = digest
    for relpath in sorted(set(expected_covered) - set(declared)):
        errors.append('NO-DATA: record covered does not name %s' % relpath)
    for relpath in sorted(set(declared) - set(expected_covered)):
        errors.append('INVALID: record covered names %s, which the drill does '
                      'not cover' % relpath)
    for relpath in sorted(set(declared) & set(expected_covered)):
        current = _read_bytes(os.path.join(root, relpath))
        if current is None:
            errors.append('NO-DATA: covered %s is missing, so its declared '
                          'hash cannot be checked' % relpath)
            continue
        got = hashlib.sha256(current).hexdigest()
        if got != declared[relpath]:
            errors.append('INVALID: covered %s hash is %s, record says %s'
                          % (relpath, got, declared[relpath]))
    return errors


def _freshness_errors(doc, today, max_age_days):
    '''The record's run date against the gate's own freshness bar. The bar is
    inclusive: age 0 and age max_age_days both stand, and a run date after
    today does not.'''
    drill_date = doc.get('drill_date')
    if not isinstance(drill_date, str) or not drill_date:
        return ['NO-DATA: record carries no ISO drill_date']
    try:
        ran = date.fromisoformat(drill_date)
    except ValueError:
        return ['NO-DATA: record drill_date %r is not an ISO date'
                % (drill_date,)]
    age_days = (today - ran).days
    if age_days < 0:
        return ['INVALID: record drill_date %s is %d day(s) in the future'
                % (drill_date, -age_days)]
    if age_days > max_age_days:
        return ['STALE: record drill_date %s is %d day(s) old, over the %d day '
                'bar declared by %s' % (drill_date, age_days, max_age_days,
                                        GATE_RELPATH)]
    return []


def restore_evidence_errors(root, today):
    '''The named reasons the gate's declared restore-drill record cannot
    stand, or [] when it stands.

    root  : the tree holding the record and the covered files.
    today : a datetime.date the freshness bar is measured against.

    A wrong argument type, and a record whose own counts cannot all be true,
    are corrupt input and raise ValueError. Every other defect comes back as a
    named error, so one call names everything that is wrong at once. A missing,
    empty, binary or hostile input is refused or named, never a crash.'''
    root = _require_root(root)
    if isinstance(today, datetime):
        today = today.date()
    if isinstance(today, bool) or not isinstance(today, date):
        raise ValueError('today must be a datetime.date, got %r' % (today,))

    declared = declared_restore_record(root)
    if declared is None:
        return ['NO-DATA: %s declares no readable %r item carrying a record '
                'path and a max age in days' % (GATE_RELPATH, RECORD_ITEM_ID)]
    record_relpath, max_age_days = declared

    drill = declared_drill_coverage(root)
    if drill is None:
        return ['NO-DATA: %s declares no readable COVERED_TOOLS and tenants'
                % DRILL_RELPATH]
    expected_covered, expected_tenants = drill

    raw = _read_bytes(os.path.join(root, record_relpath))
    if raw is None:
        return ['NO-DATA: %s does not exist' % record_relpath]
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        return ['NO-DATA: %s is not utf-8 text: %s' % (record_relpath, exc)]
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return ['NO-DATA: %s is not readable JSON: %s' % (record_relpath, exc)]
    if not isinstance(doc, dict):
        return ['NO-DATA: %s does not hold one JSON object' % record_relpath]

    errors = []
    errors.extend(_identity_errors(doc, expected_tenants))
    errors.extend(_checks_errors(doc))
    errors.extend(_covered_errors(doc, root, expected_covered))
    errors.extend(_freshness_errors(doc, today, max_age_days))
    return errors
