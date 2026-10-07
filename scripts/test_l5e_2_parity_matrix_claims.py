'''L5e.2 PARITY-MATRIX claim spot-check.

Holds extract_parity_claims and verify_claim next to the live test
test_parity_claims_exist that spot-checks docs/architecture/PARITY-MATRIX.md
against the real tree. This is a scripts/ unit whose only named files are
this one and the matrix it reads. A matrix with zero extractable claims is a
block, never a pass.

Done check: python3 -B scripts/test_l5e_2_parity_matrix_claims.py
'''

import fnmatch
import os
import re
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARITY_MATRIX_PATH = os.path.join(
    _REPO_ROOT, 'docs', 'architecture', 'PARITY-MATRIX.md'
)

_MAX_MATRIX_BYTES = 1024 * 1024
_MAX_CLAIMS = 100

_STATUS_MARKERS = ('MIGRATED', 'COVERED')

_PATH_SUFFIXES = (
    '.py', '.md', '.json', '.toml', '.cfg', '.ini',
    '.yaml', '.yml', '.txt', '.rst',
)
_PATTERN_CHARS = ('*', '?', '[', ']')

_TICK_RE = re.compile(r'`([^`\n]+)`')
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

_REQUIRED_TREE_PATHS = (
    _PARITY_MATRIX_PATH,
    os.path.join(_REPO_ROOT, 'plugin', 'runtime', 'brother'),
)

_INDEX_CACHE = {}


# --------------------------------------------------------------------------
# Extraction (RQ-3)
# --------------------------------------------------------------------------

def extract_parity_claims(matrix_text):
    '''Return backticked claims from MIGRATED or COVERED rows.'''
    if not isinstance(matrix_text, str):
        raise ValueError('matrix_text must be a str')
    if len(matrix_text.encode('utf-8', 'surrogatepass')) > _MAX_MATRIX_BYTES:
        raise ValueError('matrix_text exceeds %d bytes' % _MAX_MATRIX_BYTES)

    claims = []
    for line in matrix_text.splitlines():
        if not any(marker in line for marker in _STATUS_MARKERS):
            continue
        for token in _TICK_RE.findall(line):
            token = token.strip()
            if not token:
                continue
            claims.append(token)
            if len(claims) > _MAX_CLAIMS:
                raise ValueError(
                    'parity claim count exceeds %d' % _MAX_CLAIMS
                )
    return claims


# --------------------------------------------------------------------------
# Verification (RQ-4)
# --------------------------------------------------------------------------

def _read_text(path):
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _repo_index(root):
    cached = _INDEX_CACHE.get(root)
    if cached is not None:
        return cached
    names = set()
    paths_by_name = {}
    py_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != '.git')
        for name in dirnames:
            names.add(name)
            paths_by_name.setdefault(name, os.path.join(dirpath, name))
        for name in filenames:
            names.add(name)
            paths_by_name.setdefault(name, os.path.join(dirpath, name))
            if name.endswith('.py'):
                py_files.append(os.path.join(dirpath, name))
    index = {
        'names': names,
        'paths_by_name': paths_by_name,
        'py_files': tuple(py_files),
    }
    _INDEX_CACHE[root] = index
    return index


def _expand_braces(text):
    start = text.find('{')
    if start == -1:
        return [text]
    end = text.find('}', start + 1)
    if end == -1:
        return [text]
    prefix = text[:start]
    inner = text[start + 1:end]
    suffix = text[end + 1:]
    expansions = []
    for tail in _expand_braces(suffix):
        for option in inner.split(','):
            expansions.append(prefix + option + tail)
    return expansions


def _first_glob_hit(root, pattern):
    norm = pattern.replace('\\', '/')
    dirname = os.path.dirname(norm)
    basename = os.path.basename(norm)
    if dirname:
        base_dir = os.path.join(root, dirname)
        if not os.path.isdir(base_dir):
            return None
        try:
            entries = sorted(os.listdir(base_dir))
        except OSError:
            return None
        for entry in entries:
            if fnmatch.fnmatchcase(entry, basename):
                return os.path.join(base_dir, entry)
        return None
    index = _repo_index(root)
    for name in sorted(index['names']):
        if fnmatch.fnmatchcase(name, basename):
            return index['paths_by_name'].get(name, name)
    return None


def _literal_hit(root, loc):
    candidate = os.path.join(root, loc)
    if os.path.isfile(candidate) or os.path.isdir(candidate):
        return candidate
    if '/' not in loc:
        index = _repo_index(root)
        return index['paths_by_name'].get(os.path.basename(loc))
    return None


def _resolve_location(root, location):
    loc = location.strip().replace('\\', '/')
    while loc.endswith('/'):
        loc = loc[:-1]
    if not loc:
        return None
    if '{' in loc:
        expanded = _expand_braces(loc)
        if expanded == [loc]:
            return _literal_hit(root, loc)
        for candidate in expanded:
            hit = _resolve_location(root, candidate)
            if hit is not None:
                return hit
        return None
    if any(ch in loc for ch in _PATTERN_CHARS):
        return _first_glob_hit(root, loc)
    return _literal_hit(root, loc)


def _file_defines_symbol(path, name):
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        return False
    text = _read_text(path)
    if text is None:
        return False
    pattern = r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])'
    return re.search(pattern, text) is not None


def _symbol_defined_somewhere(root, name):
    index = _repo_index(root)
    for path in index['py_files']:
        if _file_defines_symbol(path, name):
            return True
    return False


def _looks_like_location(token):
    if '/' in token:
        return True
    if '{' in token or '}' in token:
        return True
    for ch in _PATTERN_CHARS:
        if ch in token:
            return True
    lowered = token.lower()
    for suffix in _PATH_SUFFIXES:
        if lowered.endswith(suffix):
            return True
    return False


def verify_claim(claim, repo_root):
    '''Return True when the claim path or module and symbol exist.'''
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError('claim must be a non-empty str')
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ValueError('repo_root must be a non-empty str')
    root = os.path.realpath(repo_root)
    if not os.path.isdir(root):
        raise ValueError('repo_root must be an existing directory')

    token = claim.strip()

    if '::' in token:
        location, _, symbol = token.partition('::')
        location = location.strip()
        symbol = symbol.strip()
        if not location or not symbol:
            return False
        hit = _resolve_location(root, location)
        if hit is None:
            return False
        return _file_defines_symbol(hit, symbol)

    if _looks_like_location(token):
        return _resolve_location(root, token) is not None

    name = token[:-2] if token.endswith('()') else token
    if _IDENT_RE.match(name):
        return _symbol_defined_somewhere(root, name)

    return True


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

class ClaimExtractionTests(unittest.TestCase):
    '''RQ-3: backticked tokens from MIGRATED or COVERED rows only.'''

    def test_extract_picks_only_marked_rows(self):
        text = (
            '| header | more |\n'
            '| `kept_one` | `kept_two` | MIGRATED |\n'
            '| `dropped` | plain | NO-DATA/BLOCKED |\n'
            '| `kept_three` | COVERED |\n'
        )
        self.assertEqual(
            extract_parity_claims(text),
            ['kept_one', 'kept_two', 'kept_three'],
        )

    def test_extract_zero_claims_for_unmarked_matrix(self):
        text = '| header |\n| `dropped` | NO-DATA/BLOCKED |\n'
        self.assertEqual(extract_parity_claims(text), [])

    def test_extract_empty_text_has_no_claims(self):
        self.assertEqual(extract_parity_claims(''), [])

    def test_extract_keeps_every_backticked_token(self):
        text = '| `a` | `b` | `a` | MIGRATED |\n'
        self.assertEqual(extract_parity_claims(text), ['a', 'b', 'a'])

    def test_extract_refuses_too_many_claims(self):
        tokens = ' '.join('`sym_%d`' % i for i in range(_MAX_CLAIMS + 5))
        with self.assertRaises(ValueError):
            extract_parity_claims('| ' + tokens + ' | MIGRATED |\n')


class ClaimVerificationTests(unittest.TestCase):
    '''RQ-4: a claim resolves to a real path/module and symbol.'''

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='l5e2_')
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        package = os.path.join(self.root, 'pkg')
        os.makedirs(package)
        with open(os.path.join(package, 'mod.py'), 'w', encoding='utf-8') as handle:
            handle.write('class Widget:\n    pass\n\ndef attend():\n    return 1\n')
        with open(os.path.join(package, 'one_facade.py'), 'w', encoding='utf-8') as handle:
            handle.write('pass\n')

    def test_verify_known_path(self):
        self.assertIs(verify_claim('pkg/mod.py', self.root), True)

    def test_verify_missing_path(self):
        self.assertIs(verify_claim('pkg/absent.py', self.root), False)

    def test_verify_known_symbol_pair(self):
        self.assertIs(verify_claim('pkg/mod.py::Widget', self.root), True)

    def test_verify_missing_symbol(self):
        self.assertIs(verify_claim('pkg/mod.py::Absent', self.root), False)

    def test_verify_bare_symbol_present(self):
        self.assertIs(verify_claim('Widget', self.root), True)

    def test_verify_bare_symbol_missing(self):
        self.assertIs(verify_claim('ZzAbsentSymbol', self.root), False)

    def test_verify_brace_pattern(self):
        self.assertIs(verify_claim('pkg/{one,two}_facade.py', self.root), True)

    def test_verify_non_location_token_passes_narrowly(self):
        self.assertIs(verify_claim('--unattended', self.root), True)


class HostileInputTests(unittest.TestCase):
    '''RQ-10: hostile or missing input is refused with ValueError.'''

    def test_extract_refuses_non_str(self):
        for bad in (None, 0, 1.5, float('nan'), True, b'rows', ['rows'], {'rows': 1}):
            with self.assertRaises(ValueError):
                extract_parity_claims(bad)

    def test_extract_refuses_oversize(self):
        with self.assertRaises(ValueError):
            extract_parity_claims('x' * (_MAX_MATRIX_BYTES + 1))

    def test_verify_refuses_non_str_claim(self):
        for bad in (None, 0, 1.5, float('nan'), True, b'pkg/mod.py', ['pkg']):
            with self.assertRaises(ValueError):
                verify_claim(bad, _REPO_ROOT)

    def test_verify_refuses_non_str_root(self):
        for bad in (None, 0, 1.5, float('nan'), True, b'/tmp', ['/tmp']):
            with self.assertRaises(ValueError):
                verify_claim('pkg/mod.py', bad)

    def test_verify_refuses_missing_root(self):
        with self.assertRaises(ValueError):
            verify_claim('pkg/mod.py', os.path.join(_REPO_ROOT, 'zz_absent_dir'))

    def test_verify_refuses_empty_claim(self):
        for bad in ('', '   ', '\t'):
            with self.assertRaises(ValueError):
                verify_claim(bad, _REPO_ROOT)


class LiveMatrixTests(unittest.TestCase):
    '''RQ-3, RQ-4, RQ-11: the real PARITY-MATRIX.md spot-check.'''

    @unittest.skipUnless(
        all(os.path.exists(path) for path in _REQUIRED_TREE_PATHS),
        'required repository documents are missing',
    )
    def test_parity_claims_exist(self):
        with open(_PARITY_MATRIX_PATH, 'rb') as handle:
            raw = handle.read()
        text = raw.decode('utf-8')
        claims = extract_parity_claims(text)
        self.assertGreater(
            len(claims), 0,
            'a PARITY-MATRIX with zero extractable claims is a block',
        )
        unverified = [c for c in claims if not verify_claim(c, _REPO_ROOT)]
        self.assertEqual(
            unverified, [],
            'unverified parity claims: %r' % (unverified,),
        )

    @unittest.skipUnless(
        os.path.isfile(_PARITY_MATRIX_PATH),
        'required repository documents are missing',
    )
    def test_parity_matrix_declares_l5e2_self_check_claim(self):
        with open(_PARITY_MATRIX_PATH, 'rb') as handle:
            raw = handle.read()
        text = raw.decode('utf-8')
        claims = extract_parity_claims(text)
        self.assertIn('extract_parity_claims', claims)
        self.assertIn('verify_claim', claims)


if __name__ == '__main__':
    unittest.main()
