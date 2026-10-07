from __future__ import annotations
import ast
import os
from dataclasses import dataclass
from typing import Optional, Tuple

NETWORK_SYMBOLS = frozenset({
    'urllib.request.urlopen', 'urllib.request.urlretrieve',
    'http.client.HTTPConnection', 'http.client.HTTPSConnection',
    'socket.socket', 'socket.create_connection',
    'requests.get', 'requests.post', 'requests.request',
    'httpx.get', 'httpx.post', 'httpx.request',
    'aiohttp.ClientSession',
})
FILE_IO_SYMBOLS = frozenset({
    'open', 'os.path.exists', 'os.path.isfile', 'os.path.isdir', 'os.path.abspath',
    'os.makedirs', 'os.mkdir', 'os.remove', 'os.unlink', 'os.replace',
    'os.rename', 'os.open', 'os.close', 'os.fdopen', 'os.fsync',
    'tempfile.mkstemp', 'tempfile.NamedTemporaryFile',
    'fcntl.flock', 'fcntl.lockf',
})
JSON_SYMBOLS = frozenset({
    'json.load', 'json.loads', 'json.dump', 'json.dumps',
})
SUBPROCESS_SYMBOLS = frozenset({
    'subprocess.run', 'subprocess.call', 'subprocess.check_call',
    'subprocess.check_output', 'subprocess.Popen',
    'os.system', 'os.popen',
})

@dataclass(frozen=True)
class BoundaryCall:
    file: str
    line: int
    column: int
    kind: str
    symbol: str
    enclosing_function: str
    callee_defined_here: bool
    qualname: str = '<module>'
    ordinal: int = 1


def call_id(file: str, qualname: str, symbol: str, ordinal: int) -> str:
    """The one call id every reader builds: <file>:<qualname>:<symbol>#<ordinal>.

    2026-10-03: ids carried the line number, so 107 lines landing above a call renamed every call below it and the
    audit record of ce19afcf8 could no longer be re-derived (SCAN_ERROR, tested 0). The qualname is the dotted chain
    of enclosing classes and functions (<module> at top level) and the ordinal counts that symbol inside that
    qualname in source order, so the id survives any edit outside its own function.
    """
    for label, value in (('file', file), ('qualname', qualname), ('symbol', symbol)):
        if not isinstance(value, str) or not value:
            raise ValueError('%s must be a non-empty str' % label)
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ValueError('ordinal must be a positive int')
    return '%s:%s:%s#%d' % (file, qualname, symbol, ordinal)

def build_import_map(tree: ast.Module) -> dict[str, str]:
    if not isinstance(tree, ast.Module):
        raise ValueError('tree must be an ast.Module')
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                local = alias.asname or name
                mapping[local] = name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            for alias in node.names:
                if alias.name == '*':
                    continue
                local = alias.asname or alias.name
                full = f'{module}.{alias.name}' if module else alias.name
                mapping[local] = full
    return mapping

def _dotted_name(node: ast.AST, mapping: dict[str, str]) -> Optional[str]:
    if isinstance(node, ast.Name):
        return mapping.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value, mapping)
        if base is None:
            return None
        return f'{base}.{node.attr}'
    return None

def classify_symbol(resolved_name: str) -> Optional[str]:
    if not isinstance(resolved_name, str):
        raise ValueError('resolved_name must be a str')
    if resolved_name in NETWORK_SYMBOLS:
        return 'NETWORK'
    if resolved_name in FILE_IO_SYMBOLS:
        return 'FILE_IO'
    if resolved_name in JSON_SYMBOLS:
        return 'JSON'
    if resolved_name in SUBPROCESS_SYMBOLS:
        return 'SUBPROCESS'
    return None

def scan_source(path: str, source: str) -> Tuple[BoundaryCall, ...]:
    if not isinstance(path, str):
        raise ValueError('path must be a str')
    if not isinstance(source, str):
        raise ValueError('source must be a str')
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return ()
    import_map = build_import_map(tree)
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
    calls = []
    ordinals: dict[tuple, int] = {}
    def visit(node, enclosing, qualname):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            new_enclosing = node.name
            qualname = node.name if qualname == '<module>' else qualname + '.' + node.name
        else:
            new_enclosing = enclosing
            if isinstance(node, ast.ClassDef):
                qualname = node.name if qualname == '<module>' else qualname + '.' + node.name
        if isinstance(node, ast.Call):
            resolved = _dotted_name(node.func, import_map)
            if resolved is not None:
                kind = classify_symbol(resolved)
                if kind is not None:
                    defined_here = False
                    if isinstance(node.func, ast.Name) and node.func.id in defined:
                        defined_here = True
                    key = (qualname, resolved)
                    ordinals[key] = ordinals.get(key, 0) + 1
                    calls.append(BoundaryCall(
                        file=path,
                        line=node.lineno,
                        column=node.col_offset,
                        kind=kind,
                        symbol=resolved,
                        enclosing_function=new_enclosing,
                        callee_defined_here=defined_here,
                        qualname=qualname,
                        ordinal=ordinals[key],
                    ))
        for child in ast.iter_child_nodes(node):
            visit(child, new_enclosing, qualname)
    visit(tree, '<module>', '<module>')
    calls.sort(key=lambda c: (c.file, c.line, c.column, c.symbol))
    return tuple(calls)

def is_test_file(filename: str) -> bool:
    """A test file, by the two names this tree uses: test_*.py and *_test.py.

    Owner decision 2026-10-02 (option A, docs/decisions/l5b-measurement-2026-10-02.json): L5b measures PRODUCT
    boundary calls. A test file's own setup calls can only fire when that same test fails, which proves nothing about
    the product, and counting them put half the denominator out of honest reach.
    """
    if not isinstance(filename, str):
        raise ValueError('filename must be a str')
    name = filename.rsplit('/', 1)[-1]
    return name.endswith('.py') and (name.startswith('test_') or name.endswith('_test.py'))


def scan_tree(root: str) -> Tuple[BoundaryCall, ...]:
    if not isinstance(root, str):
        raise ValueError('root must be a str')
    all_calls = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != '__pycache__' and not d.startswith('.'))
        for filename in sorted(filenames):
            if not filename.endswith('.py') or is_test_file(filename):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root)
            rel = rel.replace(os.sep, '/')
            if rel.startswith('../'):
                continue
            try:
                with open(full, 'r', encoding='utf-8') as fh:
                    source = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            all_calls.extend(scan_source(rel, source))
    all_calls.sort(key=lambda c: (c.file, c.line, c.column, c.symbol))
    return tuple(all_calls)
