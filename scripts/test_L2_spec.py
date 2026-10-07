'''L2.1 envelope tests.'''

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict


class EnvelopeError(ValueError):
    '''Refusal for a missing, corrupt, or wrong-typed audit envelope.'''


class FrontMatter(TypedDict):
    audit_version: str
    commit_sha: str
    generated_at_utc: str
    non_test_py_count: int
    test_py_count: int
    domain_count: int
    grep_positive_control: str


_EXPECTED_FIELDS = (
    'audit_version',
    'commit_sha',
    'generated_at_utc',
    'non_test_py_count',
    'test_py_count',
    'domain_count',
    'grep_positive_control',
)
_SHA40 = re.compile(r'^[0-9a-f]{40}$')
_RFC3339Z = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]+)?Z$')
_INT_DECIMAL = re.compile(r'^[0-9]+$')


def _no_extra_arguments(name: str, args: tuple, kwargs: dict) -> None:
    if args or kwargs:
        raise EnvelopeError('%s received unexpected extra arguments' % name)


def _read_text(doc_path: Path) -> str:
    if not isinstance(doc_path, Path):
        raise EnvelopeError('doc_path must be a pathlib.Path')
    try:
        raw = doc_path.read_bytes()
    except OSError as exc:
        raise EnvelopeError('could not read envelope file: %s' % exc) from exc
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise EnvelopeError('envelope file is not valid UTF-8') from exc


def _parse_int_field(key: str, value: str) -> int:
    if not _INT_DECIMAL.match(value):
        raise EnvelopeError('%s must be a decimal integer' % key)
    try:
        return int(value, 10)
    except ValueError as exc:
        raise EnvelopeError('%s must be a decimal integer' % key) from exc


def _parse_front_matter(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise EnvelopeError('missing front matter opening fence')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise EnvelopeError('missing front matter closing fence') from exc
    fields: list[tuple[str, str]] = []
    for raw_line in lines[1:end]:
        if raw_line.strip() == '':
            continue
        if ':' not in raw_line:
            raise EnvelopeError('front matter line is not a key: value pair')
        key, value = raw_line.split(':', 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise EnvelopeError('front matter key is empty')
        fields.append((key, value))
    return fields


def parse_envelope(doc_path: Path, *args: object, **kwargs: object) -> FrontMatter:
    _no_extra_arguments('parse_envelope', args, kwargs)
    text = _read_text(doc_path)
    fields = _parse_front_matter(text)
    if len(fields) != len(_EXPECTED_FIELDS):
        raise EnvelopeError('front matter must have exactly seven fields')
    keys = [key for key, _ in fields]
    if keys != list(_EXPECTED_FIELDS):
        raise EnvelopeError('front matter fields are missing, duplicated, or out of order')
    data = dict(fields)
    if len(data) != len(fields):
        raise EnvelopeError('front matter contains duplicate fields')
    audit_version = data['audit_version']
    if audit_version != 'L2-1':
        raise EnvelopeError('audit_version must be L2-1')
    commit_sha = data['commit_sha']
    if not _SHA40.match(commit_sha):
        raise EnvelopeError('commit_sha must be 40 lower hex characters')
    generated_at_utc = data['generated_at_utc']
    if not _RFC3339Z.match(generated_at_utc):
        raise EnvelopeError('generated_at_utc must be RFC3339 Z')
    try:
        datetime.fromisoformat(generated_at_utc.replace('Z', '+00:00'))
    except ValueError as exc:
        raise EnvelopeError('generated_at_utc is not a valid RFC3339 timestamp') from exc
    non_test_py_count = _parse_int_field('non_test_py_count', data['non_test_py_count'])
    test_py_count = _parse_int_field('test_py_count', data['test_py_count'])
    domain_count = _parse_int_field('domain_count', data['domain_count'])
    grep_positive_control = data['grep_positive_control']
    if not grep_positive_control:
        raise EnvelopeError('grep_positive_control must be non-empty')
    return {
        'audit_version': audit_version,
        'commit_sha': commit_sha,
        'generated_at_utc': generated_at_utc,
        'non_test_py_count': non_test_py_count,
        'test_py_count': test_py_count,
        'domain_count': domain_count,
        'grep_positive_control': grep_positive_control,
    }


def assert_envelope(doc_path: Path, *args: object, **kwargs: object) -> None:
    _no_extra_arguments('assert_envelope', args, kwargs)
    parse_envelope(doc_path)


class ReproducibilityError(ValueError):
    '''Refusal for a missing, corrupt, or wrong-typed reproducibility section.'''


_NL = chr(10)
_FENCE = '```'
_SHA256_HEX = re.compile('^[0-9a-f]{64}$')
_EXIT_CODE = re.compile('^[0-9]+$')


def _read_doc_text(doc_path: Path) -> str:
    if not isinstance(doc_path, Path):
        raise ReproducibilityError('doc_path must be a pathlib.Path')
    try:
        raw = doc_path.read_bytes()
    except OSError as exc:
        raise ReproducibilityError('could not read doc') from exc
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ReproducibilityError('doc is not valid UTF-8') from exc


def _is_h1_or_h2(line: str) -> bool:
    if line.startswith('# '):
        return True
    if line.startswith('## '):
        return True
    return line == '#'


def _reproducibility_body(text: str) -> str:
    marker = '## Reproducibility'
    if marker not in text:
        raise ReproducibilityError('missing Reproducibility section')
    remainder = text.split(marker, 1)[1]
    kept = []
    for line in remainder.splitlines():
        if _is_h1_or_h2(line):
            break
        kept.append(line)
    return _NL.join(kept)


def _g_records(text: str) -> list:
    body = _reproducibility_body(text)
    records = []
    title = None
    lines = []
    for line in body.splitlines():
        if line.startswith('### '):
            if title is not None:
                records.append({'title': title, 'text': _NL.join(lines)})
            title = line[4:].strip()
            lines = []
        elif title is not None:
            lines.append(line)
    if title is not None:
        records.append({'title': title, 'text': _NL.join(lines)})
    return records


def _field(text: str, name: str) -> str:
    match = re.search('^' + re.escape(name) + ': (.+)$', text, flags=re.M)
    if not match:
        raise ReproducibilityError('missing %s field' % name)
    return match.group(1).strip()


def _output_block(text: str) -> str:
    idx = text.find('output:')
    if idx < 0:
        raise ReproducibilityError('missing output field')
    rest = text[idx + len('output:'):]
    open_idx = rest.find(_FENCE)
    if open_idx < 0:
        raise ReproducibilityError('output fenced block missing')
    after_open = rest[open_idx + len(_FENCE):]
    if after_open.startswith(_NL):
        after_open = after_open[len(_NL):]
    close_idx = after_open.find(_NL + _FENCE)
    if close_idx < 0:
        raise ReproducibilityError('output fenced block not closed')
    return after_open[:close_idx]


def _check_g_numbers(records: list) -> None:
    for index, record in enumerate(records, start=1):
        expected = 'G-%02d' % index
        head = record['title'].split(None, 1)[0]
        if head != expected:
            raise ReproducibilityError('expected %s, found %s' % (expected, head))


def assert_g_records(doc_path: Path, minimum: int = 8) -> None:
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        raise ReproducibilityError('minimum must be an int')
    if minimum < 0:
        raise ReproducibilityError('minimum must be non-negative')
    text = _read_doc_text(doc_path)
    records = _g_records(text)
    if len(records) < minimum:
        raise ReproducibilityError('need at least %d G records, found %d' % (minimum, len(records)))
    _check_g_numbers(records)
    commands = []
    for record in records:
        body = record['text']
        command = _field(body, 'command')
        if not (command.startswith('grep ') or command.startswith('find ') or command.startswith('ls ')):
            raise ReproducibilityError('%s command must start with grep, find, or ls' % record['title'])
        commands.append(command)
        code_text = _field(body, 'exit_code')
        if not _EXIT_CODE.match(code_text):
            raise ReproducibilityError('%s exit_code must be an integer' % record['title'])
        code = int(code_text, 10)
        if code not in (0, 1, 2):
            raise ReproducibilityError('%s exit_code must be 0, 1, or 2' % record['title'])
        sha = _field(body, 'sha256')
        if not _SHA256_HEX.match(sha):
            raise ReproducibilityError('%s sha256 must be 64 lower hex' % record['title'])
        if 'output_pasted: true' not in body:
            raise ReproducibilityError('%s missing output_pasted: true' % record['title'])
        _output_block(body)
    combined = _NL.join(commands)
    for extension in ('.py', '.sh', '.json', '.md'):
        if extension not in combined:
            raise ReproducibilityError('taxonomy missing extension %s' % extension)
    if '.yml' not in combined and '.yaml' not in combined:
        raise ReproducibilityError('taxonomy missing yml or yaml extension')
    if 'python3 -m' not in combined:
        raise ReproducibilityError('taxonomy missing python3 -m string search')


def sha256_stdout(command: str) -> str:
    if not isinstance(command, str):
        raise ReproducibilityError('command must be a str')
    if not command:
        raise ReproducibilityError('command must be non-empty')
    completed = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return hashlib.sha256(completed.stdout).hexdigest()


def record_exit_code(command: str) -> Literal[0, 1, 2] | int:
    if not isinstance(command, str):
        raise ReproducibilityError('command must be a str')
    if not command:
        raise ReproducibilityError('command must be non-empty')
    completed = subprocess.run(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return completed.returncode


class TestL21Envelope(unittest.TestCase):
    DOC = Path(__file__).resolve().parent.parent / 'docs' / 'architecture' / 'ONE-SYSTEM-WIRING-AUDIT.md'

    def test_front_matter_has_seven_fields_once_each(self) -> None:
        text = _read_text(self.DOC)
        fields = _parse_front_matter(text)
        keys = [key for key, _ in fields]
        self.assertEqual(keys, list(_EXPECTED_FIELDS))
        self.assertEqual(len(set(keys)), 7)

    def test_envelope_is_valid(self) -> None:
        data = parse_envelope(self.DOC)
        self.assertEqual(data['audit_version'], 'L2-1')
        self.assertEqual(len(data['commit_sha']), 40)
        self.assertTrue(data['grep_positive_control'])

    def test_hostile_input_is_refused(self) -> None:
        hostile = [None, 1, True, b'---', [], {}, object(), '', 1.5]
        for value in hostile:
            with self.assertRaises(EnvelopeError):
                parse_envelope(value)
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing.md'
            with self.assertRaises(EnvelopeError):
                parse_envelope(missing)
            with self.assertRaises(EnvelopeError):
                parse_envelope(Path(tmp))

    def test_extra_arguments_are_refused(self) -> None:
        with self.assertRaises(EnvelopeError):
            parse_envelope(self.DOC, 'extra')
        with self.assertRaises(EnvelopeError):
            parse_envelope(self.DOC, extra=1)
        with self.assertRaises(EnvelopeError):
            assert_envelope(self.DOC, 'extra')
        with self.assertRaises(EnvelopeError):
            assert_envelope(self.DOC, extra=1)

    def test_duplicate_field_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'dup.md'
            p.write_text(
                '''---
audit_version: L2-1
audit_version: L2-1
commit_sha: 0000000000000000000000000000000000000000
generated_at_utc: 2026-09-20T00:00:00Z
non_test_py_count: 1
test_py_count: 1
domain_count: 1
grep_positive_control: x
---
''',
                encoding='utf-8',
            )
            with self.assertRaises(EnvelopeError):
                parse_envelope(p)

    def test_bad_commit_sha_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'badsha.md'
            p.write_text(
                '''---
audit_version: L2-1
commit_sha: ABC
generated_at_utc: 2026-09-20T00:00:00Z
non_test_py_count: 1
test_py_count: 1
domain_count: 1
grep_positive_control: x
---
''',
                encoding='utf-8',
            )
            with self.assertRaises(EnvelopeError):
                parse_envelope(p)

    def test_bad_timestamp_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'badtime.md'
            p.write_text(
                '''---
audit_version: L2-1
commit_sha: 0000000000000000000000000000000000000000
generated_at_utc: nope
non_test_py_count: 1
test_py_count: 1
domain_count: 1
grep_positive_control: x
---
''',
                encoding='utf-8',
            )
            with self.assertRaises(EnvelopeError):
                parse_envelope(p)

    def test_empty_control_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'emptycontrol.md'
            p.write_text(
                '''---
audit_version: L2-1
commit_sha: 0000000000000000000000000000000000000000
generated_at_utc: 2026-09-20T00:00:00Z
non_test_py_count: 1
test_py_count: 1
domain_count: 1
grep_positive_control: 
---
''',
                encoding='utf-8',
            )
            with self.assertRaises(EnvelopeError):
                parse_envelope(p)

    def test_non_utf8_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'badbytes.md'
            p.write_bytes(bytes([0xff, 0xfe, 45, 45, 45, 10]))
            with self.assertRaises(EnvelopeError):
                parse_envelope(p)


class TestL22Reproducibility(unittest.TestCase):
    DOC = Path(__file__).resolve().parent.parent / 'docs' / 'architecture' / 'ONE-SYSTEM-WIRING-AUDIT.md'

    def test_reproducibility_records_are_well_formed(self) -> None:
        assert_g_records(self.DOC)
        text = _read_doc_text(self.DOC)
        self.assertGreaterEqual(len(_g_records(text)), 8)

    def test_each_g_record_has_sha256(self) -> None:
        text = _read_doc_text(self.DOC)
        records = _g_records(text)
        self.assertGreaterEqual(len(records), 8)
        for record in records:
            match = re.search('^sha256: [0-9a-f]{64}$', record['text'], flags=re.M)
            self.assertIsNotNone(match, 'record %s is missing a sha256 line' % record['title'])

    def test_positive_control_present(self) -> None:
        envelope = parse_envelope(self.DOC)
        control = envelope['grep_positive_control']
        records = _g_records(_read_doc_text(self.DOC))
        hits = []
        for record in records:
            if control in _output_block(record['text']):
                hits.append(record['title'])
        self.assertEqual(len(hits), 1, 'grep_positive_control must hit exactly one pasted G output, saw %r' % (hits,))

    def test_taxonomy_coverage(self) -> None:
        assert_g_records(self.DOC)

    def test_hostile_input_is_refused(self) -> None:
        for value in (None, 1, True, b'bytes', [], {}, object(), 1.5, ''):
            with self.assertRaises(ReproducibilityError):
                assert_g_records(value)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ReproducibilityError):
                assert_g_records(Path(tmp) / 'missing.md')
            with self.assertRaises(ReproducibilityError):
                assert_g_records(Path(tmp))
            not_utf8 = Path(tmp) / 'not_utf8.md'
            not_utf8.write_bytes(bytes([255, 254, 45, 45, 45, 10]))
            with self.assertRaises(ReproducibilityError):
                assert_g_records(not_utf8)
        with self.assertRaises(ReproducibilityError):
            assert_g_records(self.DOC, True)
        with self.assertRaises(ReproducibilityError):
            assert_g_records(self.DOC, -1)
        with self.assertRaises(ReproducibilityError):
            assert_g_records(self.DOC, 'eight')

    def test_helper_hostile_input_is_refused(self) -> None:
        for value in (None, 1, True, b'bytes', [], {}, object(), 1.5):
            with self.assertRaises(ReproducibilityError):
                sha256_stdout(value)
            with self.assertRaises(ReproducibilityError):
                record_exit_code(value)
        with self.assertRaises(ReproducibilityError):
            sha256_stdout('')
        with self.assertRaises(ReproducibilityError):
            record_exit_code('')


class FindingsError(ValueError):
    '''Refusal for a missing, corrupt, or wrong-typed findings table.'''


_FINDINGS_HEADER = (
    '| finding_id | domain | file_path | symbol_line | symbol | capability | '
    'status | caller_path | caller_line | grep_import | grep_string | mutation |'
)
_ALLOWED_DOMAINS = frozenset({
    'core', 'mode', 'assurance', 'data', 'data/adapters', 'vault', 'mobile', 'hosts',
})
_ALLOWED_STATUSES = frozenset({'WIRED', 'ORPHAN', 'NO_DATA'})
_OUT_OF_REPO_PREFIXES = ('~/', '/home/', '/Users/', '/tmp/')


def _read_findings_text(doc_path: Path) -> str:
    if not isinstance(doc_path, Path):
        raise FindingsError('doc_path must be a pathlib.Path')
    try:
        raw = doc_path.read_bytes()
    except OSError as exc:
        raise FindingsError('could not read findings doc: %s' % exc) from exc
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise FindingsError('findings doc is not valid UTF-8') from exc


def parse_findings(doc_path: Path, *args: object, **kwargs: object) -> list[dict[str, str]]:
    if args or kwargs:
        raise FindingsError('parse_findings received unexpected extra arguments')
    text = _read_findings_text(doc_path)
    lines = text.splitlines()
    try:
        header_index = lines.index(_FINDINGS_HEADER)
    except ValueError as exc:
        raise FindingsError('missing findings table header') from exc
    rows: list[dict[str, str]] = []
    keys = [cell.strip() for cell in _FINDINGS_HEADER.strip('|').split('|')]
    for line in lines[header_index + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith('|'):
            break
        if stripped.startswith('| ---'):
            continue
        cells = [cell.strip() for cell in stripped.strip('|').split('|')]
        if len(cells) != len(keys):
            raise FindingsError('findings row has %d cells, expected %d' % (len(cells), len(keys)))
        rows.append(dict(zip(keys, cells)))
    if not rows:
        raise FindingsError('findings table has no rows')
    return rows


def non_test_py_files(root: Path, *args: object, **kwargs: object) -> list[Path]:
    if args or kwargs:
        raise FindingsError('non_test_py_files received unexpected extra arguments')
    if not isinstance(root, Path):
        raise FindingsError('root must be a pathlib.Path')
    base = root / 'plugin' / 'runtime' / 'brother'
    if not base.is_dir():
        raise FindingsError('plugin/runtime/brother directory missing')
    found: list[Path] = []
    for path in base.rglob('*.py'):
        if path.is_dir():
            continue
        if path.name.startswith('test_'):
            continue
        if '__pycache__' in path.parts:
            continue
        found.append(path)
    return sorted(found)


def assert_finding_integrity(doc_path: Path, *args: object, **kwargs: object) -> None:
    if args or kwargs:
        raise FindingsError('assert_finding_integrity received unexpected extra arguments')
    findings = parse_findings(doc_path)
    envelope = parse_envelope(doc_path)
    expected_count = envelope['non_test_py_count']
    if len(findings) != expected_count:
        raise FindingsError('findings row count %d does not match non_test_py_count %d' % (
            len(findings), expected_count))
    seen_ids: set[str] = set()
    for index, row in enumerate(findings, start=1):
        expected_id = 'F-%03d' % index
        if row['finding_id'] != expected_id:
            raise FindingsError('expected finding_id %s, found %s' % (expected_id, row['finding_id']))
        if row['finding_id'] in seen_ids:
            raise FindingsError('duplicate finding_id %s' % row['finding_id'])
        seen_ids.add(row['finding_id'])
        if row['domain'] not in _ALLOWED_DOMAINS:
            raise FindingsError('domain %s is not allowed' % row['domain'])
        if row['status'] not in _ALLOWED_STATUSES:
            raise FindingsError('status %s is not allowed' % row['status'])
        if not row['mutation']:
            raise FindingsError('mutation must be non-empty')
        if row['status'] == 'ORPHAN':
            if row['grep_import'] in ('', '-'):
                raise FindingsError('ORPHAN row %s requires grep_import' % row['finding_id'])
            if row['grep_string'] in ('', '-'):
                raise FindingsError('ORPHAN row %s requires grep_string' % row['finding_id'])
        if row['status'] == 'WIRED':
            caller_path = row['caller_path']
            if caller_path in ('', '-'):
                raise FindingsError('WIRED row %s requires caller_path' % row['finding_id'])
            for prefix in _OUT_OF_REPO_PREFIXES:
                if caller_path.startswith(prefix):
                    raise FindingsError('WIRED row %s has out-of-repo caller_path' % row['finding_id'])
            if caller_path.startswith('/'):
                raise FindingsError('WIRED row %s has absolute caller_path' % row['finding_id'])
            try:
                caller_line = int(row['caller_line'], 10)
            except ValueError as exc:
                raise FindingsError('WIRED row %s caller_line must be an integer' % row['finding_id']) from exc
            if caller_line <= 0:
                raise FindingsError('WIRED row %s caller_line must be > 0' % row['finding_id'])
        if row['status'] == 'NO_DATA':
            if row['grep_import'] != '-' or row['grep_string'] != '-':
                raise FindingsError('NO_DATA row %s must use - for grep refs' % row['finding_id'])


class TestL23Findings(unittest.TestCase):
    DOC = Path(__file__).resolve().parent.parent / 'docs' / 'architecture' / 'ONE-SYSTEM-WIRING-AUDIT.md'

    def test_finding_table_shape(self) -> None:
        assert_finding_integrity(self.DOC)

    def test_finding_ids_sequential(self) -> None:
        findings = parse_findings(self.DOC)
        expected = ['F-%03d' % i for i in range(1, len(findings) + 1)]
        found = [row['finding_id'] for row in findings]
        self.assertEqual(found, expected)

    def test_orphan_requires_two_g_refs(self) -> None:
        findings = parse_findings(self.DOC)
        orphans = [row for row in findings if row['status'] == 'ORPHAN']
        self.assertGreater(len(orphans), 0, 'expected at least one ORPHAN row')
        for row in orphans:
            self.assertNotIn(row['grep_import'], ('', '-'), 'ORPHAN %s missing grep_import' % row['finding_id'])
            self.assertNotIn(row['grep_string'], ('', '-'), 'ORPHAN %s missing grep_string' % row['finding_id'])

    def test_wired_requires_in_repo_caller(self) -> None:
        findings = parse_findings(self.DOC)
        wired = [row for row in findings if row['status'] == 'WIRED']
        for row in wired:
            caller_path = row['caller_path']
            self.assertNotIn(caller_path, ('', '-'))
            self.assertFalse(caller_path.startswith('~/'))
            self.assertFalse(caller_path.startswith('/home/'))
            self.assertFalse(caller_path.startswith('/Users/'))
            self.assertFalse(caller_path.startswith('/tmp/'))
            self.assertFalse(caller_path.startswith('/'))
            self.assertGreater(int(row['caller_line'], 10), 0)

    def test_hostile_input_is_refused(self) -> None:
        for value in (None, 1, True, b'bytes', [], {}, object(), 1.5, ''):
            with self.assertRaises(FindingsError):
                parse_findings(value)
            with self.assertRaises(FindingsError):
                non_test_py_files(value)
            with self.assertRaises(FindingsError):
                assert_finding_integrity(value)
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing.md'
            with self.assertRaises(FindingsError):
                parse_findings(missing)
            with self.assertRaises(FindingsError):
                parse_findings(Path(tmp))
            not_utf8 = Path(tmp) / 'not_utf8.md'
            not_utf8.write_bytes(bytes([255, 254, 45, 45, 45, 10]))
            with self.assertRaises(FindingsError):
                parse_findings(not_utf8)
        with self.assertRaises(FindingsError):
            non_test_py_files(self.DOC)


class EntryPointsError(ValueError):
    '''Refusal for a missing, corrupt, or wrong-typed entry points table.'''


_ENTRY_HEADER = (
    '| surface | manifest_path | manifest_version | claude_marketplace_registered | '
    'claude_marketplace_line | cursor_marketplace_registered | cursor_marketplace_line | mutation |'
)
_ALLOWED_SURFACES = ('bundle', 'products/brothermode', 'products/brothersbe', 'plugin')
_BOOL_VALUES = frozenset({'true', 'false'})
_INT_TEXT = re.compile(r'^[0-9]+$')


def _read_entry_text(doc_path: Path) -> str:
    if not isinstance(doc_path, Path):
        raise EntryPointsError('doc_path must be a pathlib.Path')
    try:
        raw = doc_path.read_bytes()
    except OSError as exc:
        raise EntryPointsError('could not read entry points doc: %s' % exc) from exc
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise EntryPointsError('entry points doc is not valid UTF-8') from exc


def parse_entry_points(doc_path: Path, *args: object, **kwargs: object) -> list[dict[str, str]]:
    if args or kwargs:
        raise EntryPointsError('parse_entry_points received unexpected extra arguments')
    text = _read_entry_text(doc_path)
    lines = text.splitlines()
    try:
        header_index = lines.index(_ENTRY_HEADER)
    except ValueError as exc:
        raise EntryPointsError('missing entry points table header') from exc
    keys = [cell.strip() for cell in _ENTRY_HEADER.strip('|').split('|')]
    rows: list[dict[str, str]] = []
    for line in lines[header_index + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith('|'):
            break
        if stripped.startswith('| ---'):
            continue
        cells = [cell.strip() for cell in stripped.strip('|').split('|')]
        if len(cells) != len(keys):
            raise EntryPointsError('entry row has %d cells, expected %d' % (len(cells), len(keys)))
        rows.append(dict(zip(keys, cells)))
    if not rows:
        raise EntryPointsError('entry points table has no rows')
    return rows


def assert_entry_table(doc_path: Path, *args: object, **kwargs: object) -> None:
    if args or kwargs:
        raise EntryPointsError('assert_entry_table received unexpected extra arguments')
    rows = parse_entry_points(doc_path)
    surfaces = [row['surface'] for row in rows]
    if len(rows) != 4 or set(surfaces) != set(_ALLOWED_SURFACES):
        raise EntryPointsError('expected exactly four surfaces %r, found %r' % (list(_ALLOWED_SURFACES), surfaces))
    for row in rows:
        for field in ('claude_marketplace_registered', 'cursor_marketplace_registered'):
            if row[field] not in _BOOL_VALUES:
                raise EntryPointsError('%s must be true or false' % field)
        for field in ('claude_marketplace_line', 'cursor_marketplace_line'):
            text = row[field]
            if not _INT_TEXT.match(text):
                raise EntryPointsError('%s must be a decimal integer' % field)
        if row['claude_marketplace_registered'] == 'false' and row['claude_marketplace_line'] != '0':
            raise EntryPointsError('claude_marketplace_line must be 0 when false')
        if row['cursor_marketplace_registered'] == 'false' and row['cursor_marketplace_line'] != '0':
            raise EntryPointsError('cursor_marketplace_line must be 0 when false')
        if row['claude_marketplace_registered'] == 'true' and row['claude_marketplace_line'] == '0':
            raise EntryPointsError('claude_marketplace_line must be nonzero when true')
        if row['cursor_marketplace_registered'] == 'true' and row['cursor_marketplace_line'] == '0':
            raise EntryPointsError('cursor_marketplace_line must be nonzero when true')
        if row['manifest_path'] == '-' and row['manifest_version'] != 'NO-DATA':
            raise EntryPointsError('manifest_version must be NO-DATA when manifest_path is -')
        if 'plugin/marketplace.json' in row['manifest_path']:
            raise EntryPointsError('plugin/marketplace.json NEW must not be cited as existing')
        if not row['mutation']:
            raise EntryPointsError('mutation must be non-empty')


class TestL24EntryPoints(unittest.TestCase):
    DOC = Path(__file__).resolve().parent.parent / 'docs' / 'architecture' / 'ONE-SYSTEM-WIRING-AUDIT.md'

    def test_entry_table_complete(self) -> None:
        assert_entry_table(self.DOC)

    def test_four_surfaces_present(self) -> None:
        rows = parse_entry_points(self.DOC)
        surfaces = [row['surface'] for row in rows]
        self.assertEqual(len(rows), 4)
        self.assertEqual(set(surfaces), {'bundle', 'products/brothermode', 'products/brothersbe', 'plugin'})

    def test_booleans_and_lines(self) -> None:
        rows = parse_entry_points(self.DOC)
        for row in rows:
            for field in ('claude_marketplace_registered', 'cursor_marketplace_registered'):
                self.assertIn(row[field], {'true', 'false'})
            if row['claude_marketplace_registered'] == 'false':
                self.assertEqual(row['claude_marketplace_line'], '0')
            if row['cursor_marketplace_registered'] == 'false':
                self.assertEqual(row['cursor_marketplace_line'], '0')
            if row['claude_marketplace_registered'] == 'true':
                self.assertNotEqual(row['claude_marketplace_line'], '0')
            if row['cursor_marketplace_registered'] == 'true':
                self.assertNotEqual(row['cursor_marketplace_line'], '0')

    def test_manifest_unknown_and_new_file_not_cited(self) -> None:
        rows = parse_entry_points(self.DOC)
        for row in rows:
            self.assertEqual(row['manifest_path'], '-')
            self.assertEqual(row['manifest_version'], 'NO-DATA')
            self.assertNotIn('plugin/marketplace.json', row['manifest_path'])

    def test_hostile_input_is_refused(self) -> None:
        for value in (None, 1, True, b'bytes', [], {}, object(), 1.5, ''):
            with self.assertRaises(EntryPointsError):
                parse_entry_points(value)
            with self.assertRaises(EntryPointsError):
                assert_entry_table(value)
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing.md'
            with self.assertRaises(EntryPointsError):
                parse_entry_points(missing)
            with self.assertRaises(EntryPointsError):
                parse_entry_points(Path(tmp))
            not_utf8 = Path(tmp) / 'not_utf8.md'
            not_utf8.write_bytes(bytes([255, 254, 45, 45, 45, 10]))
            with self.assertRaises(EntryPointsError):
                parse_entry_points(not_utf8)
        with self.assertRaises(EntryPointsError):
            parse_entry_points(self.DOC, 'extra')
        with self.assertRaises(EntryPointsError):
            assert_entry_table(self.DOC, extra=1)


import os


class StatusSemanticsError(ValueError):
    '''Raised when the status semantics section is missing or malformed.'''


_STATUS_HEADING = '## Status semantics'
_STATUS_SENTENCES = (
    'ORPHAN means no caller outside plugin/runtime/brother/ was found by the pasted grep.',
    'NO_DATA must never be rendered as ORPHAN.',
    'ORPHAN is a wiring observation, not a defect.',
)


def _read_status_text(doc_path: Path) -> str:
    if not isinstance(doc_path, Path):
        raise StatusSemanticsError('doc_path must be a pathlib.Path')
    try:
        data = doc_path.read_bytes()
    except OSError as exc:
        raise StatusSemanticsError('cannot read audit doc: %s' % exc) from exc
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise StatusSemanticsError('audit doc is not valid utf-8') from exc


def assert_status_sentences(doc_path: Path) -> None:
    text = _read_status_text(doc_path)
    if _STATUS_HEADING not in text:
        raise StatusSemanticsError('missing heading: %s' % _STATUS_HEADING)
    for sentence in _STATUS_SENTENCES:
        if sentence not in text:
            raise StatusSemanticsError('missing sentence: %s' % sentence)


class TestL25StatusSemantics(unittest.TestCase):

    @unittest.skipUnless(
        os.path.isfile('docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md'),
        'audit doc is not present in this tree',
    )
    def test_required_sentences(self) -> None:
        assert_status_sentences(Path('docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md'))

    def test_hostile_input_refused(self) -> None:
        for bad in (None, 'not-a-path', 1, [], object()):
            with self.assertRaises(StatusSemanticsError):
                assert_status_sentences(bad)
        with self.assertRaises(StatusSemanticsError):
            assert_status_sentences(Path('.'))


class StalenessOutOfScopeError(Exception):
    '''Refusal raised when the L2.6 document sections are missing or malformed.'''


def _l26_read_doc_text(doc_path):
    '''Read the audit document as UTF-8 bytes, refusing hostile input.'''
    if isinstance(doc_path, bool) or not isinstance(doc_path, Path):
        raise StalenessOutOfScopeError(
            'doc_path must be a pathlib.Path, got %s' % type(doc_path).__name__
        )
    try:
        raw = doc_path.read_bytes()
    except FileNotFoundError:
        raise StalenessOutOfScopeError('doc_path does not exist: %s' % doc_path)
    except IsADirectoryError:
        raise StalenessOutOfScopeError('doc_path is a directory: %s' % doc_path)
    except OSError as exc:
        raise StalenessOutOfScopeError('doc_path could not be read: %s' % exc)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise StalenessOutOfScopeError('doc_path is not utf-8: %s' % exc)
    if not text.strip():
        raise StalenessOutOfScopeError('doc_path carries no text: %s' % doc_path)
    return text


def _l26_section_body(text, heading):
    '''Return one level-two section body, refusing a missing or duplicate heading.'''
    count = text.count(heading)
    if count != 1:
        raise StalenessOutOfScopeError(
            'expected exactly one %r heading, found %d' % (heading, count)
        )
    body = text.split(heading, 1)[1]
    kept = []
    for line in body.splitlines():
        if line.startswith('## ') and line.strip() != heading:
            break
        kept.append(line)
    return chr(10).join(kept)


_STALENESS_REQUIRED = (
    'valid only at the `commit_sha`',
    're-run all G records',
    '- any change to the `plugin/runtime/brother/` glob',
    '- any change to `scripts/required_fast.sh`',
    '- any change to `scripts/plugin_runtime_fast_discover.py`',
    '- any change to `.claude-plugin/marketplace.json` (NEW)',
    '- any change to `.cursor-plugin/marketplace.json` (NEW)',
    '- any change to `plugin/marketplace.json` (NEW)',
)

_L26_STALENESS_BULLETS = (
    '- any change to the `plugin/runtime/brother/` glob',
    '- any change to `scripts/required_fast.sh`',
    '- any change to `scripts/plugin_runtime_fast_discover.py`',
    '- any change to `.claude-plugin/marketplace.json` (NEW)',
    '- any change to `.cursor-plugin/marketplace.json` (NEW)',
    '- any change to `plugin/marketplace.json` (NEW)',
)

_OUT_OF_SCOPE_REQUIRED = (
    '- U6:',
    '- U7:',
    '- U8:',
    '- PARITY-MATRIX:',
    'out-of-repo bridge path:',
    '- `scripts/audit_one_system_wiring.sh` NEW:',
    'no code file changes in this unit',
)

_L26_AUDIT_DOC = Path('docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md')


def assert_staleness_section(doc_path: Path) -> None:
    '''Require every staleness input named by the L2.6 signature.'''
    text = _l26_read_doc_text(doc_path)
    body = _l26_section_body(text, '## Staleness')
    missing = [item for item in _STALENESS_REQUIRED if item not in body]
    if missing:
        raise StalenessOutOfScopeError(
            'staleness section missing: %s' % ', '.join(missing)
        )


def assert_out_of_scope_section(doc_path: Path) -> None:
    '''Require every out-of-scope bullet named by the L2.6 signature.'''
    text = _l26_read_doc_text(doc_path)
    body = _l26_section_body(text, '## Out of scope')
    missing = [item for item in _OUT_OF_SCOPE_REQUIRED if item not in body]
    if missing:
        raise StalenessOutOfScopeError(
            'out of scope section missing: %s' % ', '.join(missing)
        )


@unittest.skipUnless(
    os.path.isfile(str(_L26_AUDIT_DOC)),
    reason='L2 audit doc not present in this tree',
)
class TestL26StalenessOutOfScope(unittest.TestCase):

    def test_both_sections_present(self):
        text = _l26_read_doc_text(_L26_AUDIT_DOC)
        self.assertIn('## Staleness', text)
        self.assertIn('## Out of scope', text)

    def test_staleness_binds_commit_sha(self):
        body = _l26_section_body(_l26_read_doc_text(_L26_AUDIT_DOC), '## Staleness')
        self.assertIn('valid only at the `commit_sha`', body)
        self.assertIn('- any change to the `plugin/runtime/brother/` glob', body)
        self.assertIn('- any change to `scripts/required_fast.sh`', body)

    def test_staleness_requires_full_g_rerun(self):
        body = _l26_section_body(_l26_read_doc_text(_L26_AUDIT_DOC), '## Staleness')
        self.assertIn('re-run all G records', body)

    def test_staleness_lists_invalidation_inputs(self):
        assert_staleness_section(_L26_AUDIT_DOC)

    def test_staleness_bullet_list_is_exact(self):
        body = _l26_section_body(_l26_read_doc_text(_L26_AUDIT_DOC), '## Staleness')
        bullets = tuple(
            line.strip()
            for line in body.splitlines()
            if line.startswith('- any change to ')
        )
        self.assertEqual(bullets, _L26_STALENESS_BULLETS)

    def test_staleness_lists_all_new_marketplace_files(self):
        body = _l26_section_body(_l26_read_doc_text(_L26_AUDIT_DOC), '## Staleness')
        for bullet in _L26_STALENESS_BULLETS[3:]:
            self.assertIn(bullet, body)

    def test_out_of_scope_required_bullets(self):
        assert_out_of_scope_section(_L26_AUDIT_DOC)

    def test_out_of_scope_names_every_named_item(self):
        body = _l26_section_body(_l26_read_doc_text(_L26_AUDIT_DOC), '## Out of scope')
        for item in _OUT_OF_SCOPE_REQUIRED:
            self.assertIn(item, body)

    def test_no_code_file_changes_verbatim(self):
        self.assertIn(
            'no code file changes in this unit',
            _l26_read_doc_text(_L26_AUDIT_DOC),
        )

    def test_helpers_refuse_hostile_input(self):
        hostile = (
            None,
            'docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md',
            1,
            True,
            3.5,
            float('nan'),
            float('inf'),
            b'## Staleness',
            bytearray(b'## Staleness'),
            ['## Staleness'],
            {'path': 'docs'},
            {('docs',)},
            Path('.'),
        )
        for bad in hostile:
            with self.assertRaises(StalenessOutOfScopeError):
                assert_staleness_section(bad)
            with self.assertRaises(StalenessOutOfScopeError):
                assert_out_of_scope_section(bad)
        absent = Path('docs/architecture/no-such-l26-audit-doc.md')
        for helper in (assert_staleness_section, assert_out_of_scope_section):
            with self.assertRaises(StalenessOutOfScopeError):
                helper(absent)
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            nl = bytes([10])
            non_utf8 = scratch / 'non_utf8.md'
            non_utf8.write_bytes(bytes([0xff, 0xfe, 0x00]) + b'## Staleness' + nl)
            with self.assertRaises(StalenessOutOfScopeError):
                assert_staleness_section(non_utf8)
            empty = scratch / 'empty.md'
            empty.write_bytes(b'')
            with self.assertRaises(StalenessOutOfScopeError):
                assert_out_of_scope_section(empty)
            duplicate = scratch / 'duplicate.md'
            duplicate.write_bytes(
                b'## Staleness' + nl + b'A' + nl + b'## Staleness' + nl + b'B' + nl
            )
            with self.assertRaises(StalenessOutOfScopeError):
                assert_staleness_section(duplicate)
            partial = scratch / 'partial.md'
            partial.write_bytes(b'## Staleness' + nl + b'nothing bound here' + nl)
            with self.assertRaises(StalenessOutOfScopeError):
                assert_staleness_section(partial)
            with self.assertRaises(StalenessOutOfScopeError):
                assert_out_of_scope_section(partial)


class GateLimitsError(Exception):
    '''L2.7 gate limits section or its source is missing, corrupt or malformed.'''


_L27_AUDIT_DOC = Path('docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md')
_L27_GATE_HEADING = '## Gate limits noted, not fixed'
_L27_REQUIRED_KEYS = ('HEAVY', 'codes_file', 'exit `1` conflation', 'BROTHER_JEV_STATE_DIR')
_L27_TAIL = 'fix deferred, audit only'
_L27_HEAVY_NAMES = frozenset({
    'test_openrouter_ledger.py',
    'test_leases.py',
    'test_dispatch_semaphore.py',
})
_L27_HEAVY_SOURCE = Path('scripts/plugin_runtime_fast_discover.py')
_L27_FILE_RE = re.compile(r'scripts/[A-Za-z0-9_./-]+[.](?:py|sh)')
_L27_LINE_RE = re.compile(r'line ([0-9]+)')
_L27_G_RE = re.compile(r'G-([0-9]{2})')
_L27_G_HEADING_RE = re.compile(r'^### G-([0-9]{2})', re.M)
_L27_DOC_SKIP = 'docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md not present'
_L27_SOURCE_SKIP = 'scripts/plugin_runtime_fast_discover.py not present'


def _l27_read_bytes(path):
    if not isinstance(path, Path):
        raise GateLimitsError('path must be a pathlib.Path, got %r' % type(path).__name__)
    try:
        is_file = path.is_file()
    except OSError as exc:
        raise GateLimitsError('cannot stat %s: %s' % (path, exc))
    if not is_file:
        raise GateLimitsError('not a regular file: %s' % path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GateLimitsError('cannot read %s: %s' % (path, exc))
    try:
        data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise GateLimitsError('file is not utf-8: %s: %s' % (path, exc))
    return data


def _l27_doc_text(doc_path):
    return _l27_read_bytes(doc_path).decode('utf-8')


def _l27_section_body(text, heading):
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        raise GateLimitsError('missing heading: %s' % heading)
    body = []
    for line in lines[start + 1:]:
        if line.startswith('## '):
            break
        body.append(line)
    return '\n'.join(body)


def _l27_parse_heavy(text):
    if not isinstance(text, str):
        raise GateLimitsError('source text must be str, got %r' % type(text).__name__)
    candidates = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('HEAVY') and '=' in stripped:
            candidates.append(stripped)
    if len(candidates) != 1:
        raise GateLimitsError('expected exactly one HEAVY assignment, found %d' % len(candidates))
    line = candidates[0]
    start = line.find('{')
    end = line.rfind('}')
    if start < 0 or end < 0 or end < start:
        raise GateLimitsError('HEAVY assignment has no set literal')
    if line[end + 1:].strip():
        raise GateLimitsError('unexpected text after HEAVY set literal')
    body = line[start + 1:end]
    tokens = body.split(',')
    if tokens and not tokens[-1].strip():
        tokens = tokens[:-1]
    if not tokens:
        raise GateLimitsError('HEAVY set is empty')
    names = []
    for token in tokens:
        stripped = token.strip()
        if len(stripped) < 2 or stripped[0] != '"' or stripped[-1] != '"' or '"' in stripped[1:-1]:
            raise GateLimitsError('HEAVY element is not a simple double-quoted name: %r' % stripped)
        names.append(stripped[1:-1])
    if len(set(names)) != len(names):
        raise GateLimitsError('HEAVY set lists the same name more than once')
    resolved = set(names)
    if resolved != set(_L27_HEAVY_NAMES):
        raise GateLimitsError('HEAVY set does not match the audited name list')
    return resolved


def heavy_basenames(*args, **kwargs):
    '''Return the HEAVY basenames from scripts/plugin_runtime_fast_discover.py.'''
    if args or kwargs:
        raise GateLimitsError('heavy_basenames takes no arguments')
    return _l27_parse_heavy(_l27_read_bytes(_L27_HEAVY_SOURCE).decode('utf-8'))


def _l27_g_numbers(text):
    return set(_L27_G_HEADING_RE.findall(text))


def assert_gate_limits(doc_path):
    text = _l27_doc_text(doc_path)
    body = _l27_section_body(text, _L27_GATE_HEADING)
    bullets = [line.strip() for line in body.splitlines() if line.strip().startswith('- ')]
    if len(bullets) != 4:
        raise GateLimitsError('expected exactly 4 bullets, found %d' % len(bullets))
    joined = '\n'.join(bullets)
    for key in _L27_REQUIRED_KEYS:
        if key not in joined:
            raise GateLimitsError('missing required key in bullets: %r' % key)
    known_g = _l27_g_numbers(text)
    for bullet in bullets:
        if not bullet.endswith(_L27_TAIL):
            raise GateLimitsError('bullet does not end with %r: %r' % (_L27_TAIL, bullet))
        refs = _L27_G_RE.findall(bullet)
        if not refs:
            raise GateLimitsError('bullet lacks a G ref: %r' % bullet)
        for ref in refs:
            if ref not in known_g:
                raise GateLimitsError('bullet cites unknown G record G-%s: %r' % (ref, bullet))
        if not _L27_FILE_RE.search(bullet):
            raise GateLimitsError('bullet lacks a file cite: %r' % bullet)
        if not _L27_LINE_RE.search(bullet):
            raise GateLimitsError('bullet lacks a line cite: %r' % bullet)
    heavy_bullet = None
    for bullet in bullets:
        if 'HEAVY' in bullet:
            heavy_bullet = bullet
            break
    if heavy_bullet is None:
        raise GateLimitsError('no HEAVY bullet found')
    for name in sorted(_L27_HEAVY_NAMES):
        if name not in heavy_bullet:
            raise GateLimitsError('HEAVY bullet missing basename: %r' % name)


class TestL27GateLimits(unittest.TestCase):

    def test_parser_accepts_canonical_names(self):
        text = 'HEAVY = {"test_openrouter_ledger.py", "test_leases.py", "test_dispatch_semaphore.py"}\n'
        self.assertEqual(_l27_parse_heavy(text), set(_L27_HEAVY_NAMES))

    def test_parser_refuses_duplicate_name(self):
        text = 'HEAVY = {"test_leases.py", "test_leases.py", "test_openrouter_ledger.py", "test_dispatch_semaphore.py"}\n'
        with self.assertRaises(GateLimitsError):
            _l27_parse_heavy(text)

    def test_parser_refuses_extra_name(self):
        text = 'HEAVY = {"test_leases.py", "test_openrouter_ledger.py", "test_dispatch_semaphore.py", "test_extra.py"}\n'
        with self.assertRaises(GateLimitsError):
            _l27_parse_heavy(text)

    def test_parser_refuses_missing_name(self):
        text = 'HEAVY = {"test_leases.py", "test_openrouter_ledger.py"}\n'
        with self.assertRaises(GateLimitsError):
            _l27_parse_heavy(text)

    def test_parser_refuses_unquoted_element(self):
        text = 'HEAVY = {test_leases.py, "test_openrouter_ledger.py", "test_dispatch_semaphore.py"}\n'
        with self.assertRaises(GateLimitsError):
            _l27_parse_heavy(text)

    def test_parser_refuses_hostile_text(self):
        for bad in (None, 0, 1.5, True, b'BYTES', ['LIST'], {'k': 'v'}):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(GateLimitsError):
                    _l27_parse_heavy(bad)

    def test_parser_refuses_missing_assignment(self):
        with self.assertRaises(GateLimitsError):
            _l27_parse_heavy('no assignment here\n')

    def test_heavy_basenames_refuses_extra_arguments(self):
        cases = (
            (True,),
            (0,),
            (7,),
            (1.5,),
            (float('nan'),),
            (float('inf'),),
            ('../',),
            ('scripts/plugin_runtime_fast_discover.py',),
            (_L27_HEAVY_SOURCE,),
            ([],),
            ({'k': 'v'},),
            (bytes([255, 254]),),
            (None,),
            ((i for i in range(1)),),
        )
        for extra in cases:
            with self.subTest(extra=repr(extra)):
                with self.assertRaises(GateLimitsError):
                    heavy_basenames(*extra)
        with self.assertRaises(GateLimitsError):
            heavy_basenames(path=_L27_HEAVY_SOURCE)

    def test_heavy_basenames_refuses_unhashable_argument(self):
        with self.assertRaises(GateLimitsError):
            heavy_basenames({'not': 'hashable'})

    @unittest.skipUnless(os.path.isfile(_L27_AUDIT_DOC), _L27_DOC_SKIP)
    def test_section_present(self):
        text = _l27_doc_text(_L27_AUDIT_DOC)
        self.assertIn(_L27_GATE_HEADING, text)

    @unittest.skipUnless(os.path.isfile(_L27_AUDIT_DOC), _L27_DOC_SKIP)
    def test_four_bullets(self):
        assert_gate_limits(_L27_AUDIT_DOC)

    @unittest.skipUnless(os.path.isfile(_L27_AUDIT_DOC), _L27_DOC_SKIP)
    def test_bullet_tails(self):
        text = _l27_doc_text(_L27_AUDIT_DOC)
        body = _l27_section_body(text, _L27_GATE_HEADING)
        bullets = [line.strip() for line in body.splitlines() if line.strip().startswith('- ')]
        self.assertEqual(len(bullets), 4)
        for bullet in bullets:
            self.assertTrue(bullet.endswith(_L27_TAIL), bullet)

    @unittest.skipUnless(os.path.isfile(_L27_AUDIT_DOC), _L27_DOC_SKIP)
    def test_heavy_names_disclosed(self):
        text = _l27_doc_text(_L27_AUDIT_DOC)
        body = _l27_section_body(text, _L27_GATE_HEADING)
        heavy_bullet = ''
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith('- ') and 'HEAVY' in stripped:
                heavy_bullet = stripped
                break
        for name in sorted(_L27_HEAVY_NAMES):
            self.assertIn(name, heavy_bullet)

    @unittest.skipUnless(os.path.isfile(_L27_AUDIT_DOC), _L27_DOC_SKIP)
    def test_bullet_g_refs_exist(self):
        text = _l27_doc_text(_L27_AUDIT_DOC)
        known = _l27_g_numbers(text)
        body = _l27_section_body(text, _L27_GATE_HEADING)
        bullets = [line.strip() for line in body.splitlines() if line.strip().startswith('- ')]
        for bullet in bullets:
            refs = _L27_G_RE.findall(bullet)
            self.assertTrue(refs, bullet)
            for ref in refs:
                self.assertIn(ref, known, bullet)

    @unittest.skipUnless(os.path.isfile(_L27_HEAVY_SOURCE), _L27_SOURCE_SKIP)
    def test_source_heavy_names(self):
        self.assertEqual(heavy_basenames(), set(_L27_HEAVY_NAMES))

    def test_hostile_doc_input_refused(self):
        for bad in (None, 0, 1.5, True, b'BYTES', 'a string', ['LIST'], {'k': 'v'}):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(GateLimitsError):
                    assert_gate_limits(bad)
        with self.assertRaises(GateLimitsError):
            assert_gate_limits(Path('/nonexistent-l27-doc'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GateLimitsError):
                assert_gate_limits(Path(tmp))
            bad_utf8 = Path(tmp) / 'bad.md'
            bad_utf8.write_bytes(bytes([255, 254, 0]) + b' not utf8')
            with self.assertRaises(GateLimitsError):
                assert_gate_limits(bad_utf8)
            empty = Path(tmp) / 'empty.md'
            empty.write_bytes(b'')
            with self.assertRaises(GateLimitsError):
                assert_gate_limits(empty)
            no_heading = Path(tmp) / 'noheading.md'
            no_heading.write_bytes(b'# some doc\n')
            with self.assertRaises(GateLimitsError):
                assert_gate_limits(no_heading)


if __name__ == '__main__':
    unittest.main()
