#!/usr/bin/env python3
"""Freeze an offline runtime closure; verify it without starting the loop.

Python imports are resolved through the interpreter's import finders, including
package initializers and transitive imports. Extension modules and their Mach-O
load closure are frozen. Declared optional imports freeze their absence. No
entry point is executed; the one call into frozen code asks model_router, in a
child interpreter, which Claude and Codex executables it selects, and those files
are hashed too. Computed imports and opaque binary dependencies require
additional evidence; they are NO-DATA, never silently omitted from a successful
manifest.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.machinery as machinery
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import collections.abc  # noqa: F401  (puts the 3.13 alias in sys.modules for resolve)
import sys
import sysconfig
import tempfile
import types

SCHEMA = 'loop-freeze-v1'
REQUIRED_CONFIGS = frozenset(('cap', 'burn', 'registry', 'roles', 'intake'))
# Read by the money path when present (dispatch-limits.json, dispatch-policy.json, openrouter-models.json):
# present is hashed, absent is recorded as ABSENT, and either change is drift (B5 objection 3).
OPTIONAL_CONFIGS = frozenset(('limits', 'policy', 'catalog'))
ABSENT = {'absent': True}
# Run identity: values that necessarily differ between RB and RC and are recorded per run in each
# boundary receipt and the launch registry (B5-06, B5-11). A closed list: everything else the
# environment carries that steers the loop is frozen, including model, effort, routing and limits.
VOLATILE_ENV = frozenset(('BROTHER_RUN_DIR', 'BROTHER_PROOF_PHASE', 'BROTHER_PROOF_RUN_DIR', 'BROTHER_STOP_HOUR'))


class NoData(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_record(path):
    """Hashed a chunk at a time: a frozen model executable is a file of 200 MB and more (the claude CLI measured
    220,931,760 bytes on 2026-09-27), so it is never read into memory whole."""
    path = Path(path).absolute()
    h = hashlib.sha256(); size = 0
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk); size += len(chunk)
    return {'sha256': h.hexdigest(), 'size': size, 'resolved': str(path.resolve(strict=True))}


def inventory(root):
    root = Path(root).absolute()
    if not root.is_dir():
        raise NoData('runtime directory unavailable: %s' % root)
    result = []
    def failed(exc):
        raise exc
    for parent, dirs, files in os.walk(str(root), onerror=failed):
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                raise NoData('directory symlink requires explicit closure: %s' % (Path(parent) / name))
        for name in files:
            p = Path(parent) / name
            if not p.is_file():
                raise NoData('unreadable runtime entry: %s' % p)
            result.append(str(p.absolute()))
    if not result:
        raise NoData('empty runtime directory')
    return sorted(result)


def env_record(env):
    """Every BROTHER_ setting but the run identity, PATH (which tools a name finds) and every PYTHON
    variable (what the interpreter does before and while it runs any tool), hashed (B5-09, U4)."""
    return {k: digest(v.encode('utf-8')) for k, v in sorted(env.items())
            if (k.startswith('BROTHER_') and k not in VOLATILE_ENV) or k.startswith('PYTHON') or k == 'PATH'}


def startup_record():
    """What the interpreter runs before any loop tool (B5-09, U4): every .pth in every site directory
    and the user site, active or not, by name and sha256, and an absent directory as ABSENT.

    Executable startup is refused, not frozen, since its bytes do not freeze what it imports: a findable
    sitecustomize or usercustomize, or a .pth line site.py executes (one starting `import ` or `import` and
    a tab) in a file the running user can write or in a directory it can write. A .pth nobody running the
    loop can change (a vendor's, root owned; measured: /usr/bin/python3 3.9.6 ships distutils-precedence.pth)
    is frozen by its bytes like any other file."""
    import site
    record = {}
    for folder in list(site.getsitepackages()) + [site.getusersitepackages()]:
        folder = str(Path(folder).absolute())
        if not os.path.isdir(folder):
            record[folder] = dict(ABSENT)
            continue
        entry = {}
        for name in sorted(os.listdir(folder)):
            if not name.endswith('.pth'):
                continue
            path = os.path.join(folder, name)
            data = Path(path).read_bytes()
            lines = (data[3:] if data.startswith(b'\xef\xbb\xbf') else data).splitlines()
            if (any(line.startswith((b'import ', b'import\t')) for line in lines)
                    and (os.access(path, os.W_OK) or os.access(folder, os.W_OK))):
                raise NoData('executable interpreter startup is not frozen: %s' % path)
            entry[name] = digest(data)
        record[folder] = entry
    search = [p for p in sys.path if p]
    for name in ('sitecustomize', 'usercustomize'):
        spec = machinery.PathFinder.find_spec(name, search)
        if name in sys.modules or spec is not None:
            raise NoData('executable interpreter startup is not frozen: %s (%s)'
                         % (name, getattr(spec, 'origin', None) or 'already imported'))
    return record


_ABSENT = object()

def _path_spec(fullname, search):
    """PathFinder.find_spec for a probe that never imports. A namespace hit builds a _NamespacePath whose
    constructor reads sys.modules[parent].__path__, and the parent was only probed: an absent or None-blocked
    parent is stood in for with this search path for the call, then its entry is put back. The spec leaves
    holding a plain list, so iterating it later never reads sys.modules again."""
    parent = fullname.rpartition('.')[0]
    prior = sys.modules.get(parent, _ABSENT)
    stand_in = bool(parent) and (prior is None or prior is _ABSENT)
    if stand_in:
        module = types.ModuleType(parent); module.__path__ = list(search)
        sys.modules[parent] = module
    try:
        spec = machinery.PathFinder.find_spec(fullname, search)
        if spec is not None and spec.submodule_search_locations is not None:
            spec.submodule_search_locations = list(spec.submodule_search_locations)
        return spec
    finally:
        if stand_in:
            if prior is _ABSENT:
                sys.modules.pop(parent, None)
            else:
                sys.modules[parent] = prior

def resolve(name, roots):
    """Resolve every package level without executing its initializer."""
    parts = name.split('.'); search = roots; found = []
    for i in range(len(parts)):
        fullname = '.'.join(parts[:i + 1])
        spec = machinery.BuiltinImporter.find_spec(fullname) or machinery.FrozenImporter.find_spec(fullname)
        if spec is None:
            spec = _path_spec(fullname, search)
        if spec is None and found and is_stdlib(found[-1][1]):
            # A STANDARD LIBRARY ALIAS (2026-09-28): on Python 3.13 collections.abc has no file; the collections
            # package installs it in sys.modules as the frozen _collections_abc, so a probe that never imports
            # cannot find it and the deploy refused prediction_ledger.py. Accepted only when the parent level is
            # standard library and the alias this interpreter already holds is standard library too.
            alias_spec = getattr(sys.modules.get(fullname), '__spec__', None)
            if alias_spec is not None and is_stdlib(alias_spec):
                spec = alias_spec
        if spec is None:
            return None
        found.append((fullname, spec))
        # os.path is a platform alias, not a child package. Only the genuine
        # standard library os may supply it; a local module is not exempt.
        if fullname == 'os' and name == 'os.path' and is_stdlib(spec):
            path_spec = os.path.__spec__
            if path_spec is None or not is_stdlib(path_spec):
                return None
            return found + [('os.path', path_spec)]
        if i + 1 < len(parts):
            if spec.submodule_search_locations is None:
                return None
            search = list(spec.submodule_search_locations)
    return found


def is_stdlib(spec):
    if spec.origin in ('built-in', 'frozen'):
        return True
    if not spec.origin:
        return False
    p = Path(spec.origin).resolve()
    library = Path(sysconfig.get_path('stdlib')).resolve()
    return library in p.parents and not any(part in ('site-packages', 'dist-packages') for part in p.parts)


def dynamic_bindings(tree, path):
    """Conservatively follow loader aliases without executing inspected code."""
    kinds = {'import_module': 'module', '__import__': 'builtin'}
    opaque = {'spec_from_file_location', 'SourceFileLoader', 'ExtensionFileLoader', 'load_module'}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in kinds:
                    kinds[alias.asname or alias.name] = kinds[alias.name]
                elif alias.name in opaque:
                    kinds[alias.asname or alias.name] = 'opaque:' + alias.name

    def kind(expr):
        if isinstance(expr, ast.Name):
            return kinds.get(expr.id)
        if isinstance(expr, ast.Attribute):
            if expr.attr in opaque:
                return 'opaque:' + expr.attr
            return {'import_module': 'module', '__import__': 'builtin'}.get(expr.attr)
        return None

    assignments = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            binding = kind(node.value)
            if not binding:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    raise NoData('loader assignment has no frozen binding in %s' % path)
                previous = kinds.get(target.id)
                if previous and previous != binding:
                    raise NoData('ambiguous loader binding in %s' % path)
                if not previous:
                    kinds[target.id] = binding; changed = True
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        binding = kind(node)
        if not binding or not isinstance(getattr(node, 'ctx', None), ast.Load):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Call) and parent.func is node:
            continue
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
            continue
        raise NoData('loader reference escapes frozen resolution in %s' % path)
    for node in assignments:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Name) and t.id in kinds for t in targets) and not kind(node.value):
            raise NoData('loader was rebound without frozen resolution in %s' % path)
    return kind


def home_for(env=None):
    """The HOME a runtime-relative '~/' expansion resolves against (D-21): pinned into the manifest at
    write time rather than read implicitly, so a changed HOME at verify time is drift, never a silent
    re-resolution against whatever HOME happens to be current."""
    e = os.environ if env is None else env
    return e.get('HOME') or os.path.expanduser('~')


def _dotted_name(node):
    """The dotted attribute chain a Name/Attribute expression spells, or None past anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return base + '.' + node.attr if base else None
    return None


# Reached any way at all, each of these can write a namespace no static reading follows.
_NAMESPACE_WRITERS = frozenset(('globals', 'vars', 'locals', 'exec', 'eval', 'setattr', 'delattr', '__dict__'))
# The attributes _resolve_path_expr interprets; a file that assigns any of them resolves no path.
_PATH_ATTRS = frozenset(('parent', 'resolve', 'path', 'join', 'expanduser', 'abspath', 'dirname', '__truediv__'))


def _name_bindings(tree):
    """Every binding of every name in the file, in any scope, as name -> [binding node], plus the attribute
    names the file assigns or deletes. None when the file can write a namespace in a way no static reading
    follows: a star import, or any mention of a namespace writer, by name, attribute or import."""
    bindings = {}; attrs = set()
    def bind(name, node):
        bindings.setdefault(name, []).append(node)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            ident = node.id if isinstance(node, ast.Name) else node.attr
            if ident in _NAMESPACE_WRITERS:
                return None
            if isinstance(node, ast.Attribute) and ident == 'modules':
                # any use of a `.modules` attribute, loaded or stored, on any base: sys.modules can be bound to another
                # name and then written through it, so no spelling of the store is safe to trust (Codex check-in 4, #2)
                return None
            if isinstance(node.ctx, ast.Load):
                continue
            if isinstance(node, ast.Name):
                bind(ident, node)
            else:
                attrs.add(ident)
        elif isinstance(node, ast.Constant) and node.value == 'modules':
            # getattr(sys, 'modules') reaches sys.modules through a string (Codex check-in 5, finding 1). A name built
            # at run time ('mod' + 'ules', a decoded string) is beyond any static reading; the proof's threat model is
            # non adversarial (DESIGN-FINAL section 6, PROOF-ACCEPTANCE), so that residual is named, not defended.
            return None
        elif isinstance(node, ast.ImportFrom) and node.module == 'sys' \
                and any(alias.name in ('modules', '*') for alias in node.names):
            # `from sys import modules as cache` binds sys.modules to any name (Codex check-in 4, finding 2)
            return None
        elif isinstance(node, ast.alias):
            if node.name == '*' or node.name in _NAMESPACE_WRITERS:
                return None
            bind(node.asname or node.name.partition('.')[0], node)
        elif isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load) \
                and (_dotted_name(node.value) or '').rpartition('.')[2] == 'modules':
            # a store into sys.modules swaps what an import returns (Codex check-in 2, finding 7); matched by the
            # last name, never the spelling, so `import sys as s` and `from sys import modules` are caught too
            # (check-in 3, finding 2). Any other store into something named modules refuses as well: fail closed.
            return None
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                bind(name, node)
        elif isinstance(node, ast.arg):
            bind(node.arg, node)
        else:
            # def, class, except, match captures and type parameters hold the name they bind here.
            for field in ('name', 'rest'):
                if isinstance(getattr(node, field, None), str):
                    bind(getattr(node, field), node)
    return bindings, attrs


def _path_scope(tree):
    """What _resolve_path_expr may read in this file (D-21), or None when it may read no name at all. A name
    resolves only when its one binding anywhere in the file is a plain module level `NAME = value` and no
    attribute of that name is assigned; __file__ only while the file never binds it; os only while every
    binding of it is `import os`; Path only while every binding of it is `from pathlib import Path`. Any
    second binding, of any kind in any scope, makes the name unresolvable rather than guessed."""
    found = _name_bindings(tree)
    if found is None or found[1] & _PATH_ATTRS:
        return None
    bindings, attrs = found
    plain = {stmt.targets[0]: stmt.value for stmt in tree.body
             if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)}
    stdlib = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            stdlib.update(a for a in node.names if not a.asname and a.name.partition('.')[0] == 'os')
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module == 'pathlib':
            stdlib.update(a for a in node.names if not a.asname and a.name == 'Path')
    def only(name):
        sites = bindings.get(name, [])
        return bool(sites) and all(site in stdlib for site in sites)
    return {'consts': {name: plain[sites[0]] for name, sites in bindings.items()
                       if len(sites) == 1 and sites[0] in plain and name not in attrs},
            # the trusted names obey the attribute store rule too: `sys.modules[__name__].__file__ = x` rebinds
            # __file__ through the module object (Codex check-in 2, finding 7)
            '__file__': '__file__' not in bindings and '__file__' not in attrs,
            'os': only('os') and 'os' not in attrs, 'Path': only('Path') and 'Path' not in attrs}


def _enclosing_function(node, parents):
    """The name of the nearest enclosing function, or None outside any function body."""
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        parent = parents.get(parent)
    return None


def _resolve_path_expr(node, path, home, scope, seen):
    """Statically evaluate a whitelisted, constant filesystem path expression: a string literal, a name
    _path_scope admits, os.path.join, os.path.expanduser of a literal starting with '~/' expanded
    against the pinned home, the scanned file's own directory via __file__ (with or without abspath),
    Path(...).resolve()/.parent chains, and Path(...) / 'literal' joins. None past anything else (D-21)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if scope is None:
        return None
    consts = scope['consts']
    if isinstance(node, ast.Name):
        if node.id == '__file__':
            return os.path.abspath(path) if scope['__file__'] else None
        if node.id in seen or node.id not in consts:
            return None
        return _resolve_path_expr(consts[node.id], path, home, scope, seen | {node.id})
    if isinstance(node, ast.Attribute):
        if node.attr == 'parent':
            inner = _resolve_path_expr(node.value, path, home, scope, seen)
            return os.path.dirname(inner) if inner is not None else None
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _resolve_path_expr(node.left, path, home, scope, seen)
        right = _resolve_path_expr(node.right, path, home, scope, seen)
        return os.path.join(left, right) if left is not None and right is not None else None
    if isinstance(node, ast.Call):
        func = node.func
        if node.keywords:
            return None
        if isinstance(func, ast.Name) and func.id == 'Path':
            if len(node.args) != 1 or not scope['Path']:
                return None
            return _resolve_path_expr(node.args[0], path, home, scope, seen)
        if isinstance(func, ast.Attribute):
            if func.attr == 'resolve' and not node.args:
                inner = _resolve_path_expr(func.value, path, home, scope, seen)
                return os.path.abspath(inner) if inner is not None else None
            dotted = _dotted_name(func)
            if dotted is not None and dotted.startswith('os.') and not scope['os']:
                return None
            if dotted == 'os.path.join':
                parts = [_resolve_path_expr(a, path, home, scope, seen) for a in node.args]
                return os.path.join(*parts) if parts and all(p is not None for p in parts) else None
            if dotted == 'os.path.expanduser' and len(node.args) == 1:
                inner = _resolve_path_expr(node.args[0], path, home, scope, seen)
                if inner is None or not inner.startswith('~/') or not home:
                    return None
                return os.path.join(home, inner[2:])
            if dotted == 'os.path.abspath' and len(node.args) == 1:
                inner = _resolve_path_expr(node.args[0], path, home, scope, seen)
                return os.path.abspath(inner) if inner is not None else None
            if dotted == 'os.path.dirname' and len(node.args) == 1:
                inner = _resolve_path_expr(node.args[0], path, home, scope, seen)
                return os.path.dirname(inner) if inner is not None else None
        return None
    return None


_LOADER_ARG_NAMES = {'spec_from_file_location': ('name', 'location'), 'SourceFileLoader': ('fullname', 'path')}


def _resolve_loader_call(callee, node, path, home, scope):
    """A constant file loader (D-21): spec_from_file_location or SourceFileLoader called with a literal
    module name and a path built only from the whitelist in _resolve_path_expr, and nothing else: an extra
    argument (a loader= or submodule_search_locations= keyword) decides how the file runs, so it refuses.
    Returns (name, absolute target) or None, which keeps today's refusal for every other opaque loader
    and every other shape."""
    arg_names = _LOADER_ARG_NAMES.get(callee)
    if arg_names is None:
        return None
    if any(isinstance(a, ast.Starred) for a in node.args) or any(kw.arg is None for kw in node.keywords):
        return None
    keywords = {kw.arg: kw.value for kw in node.keywords}
    if len(node.args) > 2 or set(keywords) - set(arg_names[len(node.args):]):
        return None
    name_arg = node.args[0] if len(node.args) >= 1 else keywords.get(arg_names[0])
    path_arg = node.args[1] if len(node.args) >= 2 else keywords.get(arg_names[1])
    if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str) and name_arg.value):
        return None
    if path_arg is None:
        return None
    target = _resolve_path_expr(path_arg, path, home, scope, frozenset())
    return None if target is None else (name_arg.value, os.path.abspath(target))


def _is_computed_loader_call(node):
    """True where a resolved import_module/__import__ call site would refuse today: unpacked or
    keyword-splat arguments, or a name argument that is not a plain, non-relative string literal."""
    keywords = {kw.arg: kw.value for kw in node.keywords}
    if None in keywords or any(isinstance(a, ast.Starred) for a in node.args):
        return True
    arg = node.args[0] if node.args else keywords.get('name')
    return not (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value and not arg.value.startswith('.'))


def imports_of(path, module, home=None, work_input_functions=None):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'), filename=str(path))
    kind_of = dynamic_bindings(tree, path)
    scope = _path_scope(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    package = module if Path(path).name == '__init__.py' else module.rpartition('.')[0]
    required = []; optional = []; loader_sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            required.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                bits = package.split('.') if package else []
                if node.level > len(bits):
                    raise NoData('unresolved relative import in %s' % path)
                base = '.'.join(bits[:len(bits) - node.level + 1] + ([base] if base else []))
            if not base:
                raise NoData('unresolved import in %s' % path)
            required.append(base)
            optional.extend(base + '.' + a.name for a in node.names if a.name != '*')
        elif isinstance(node, ast.Call):
            kind = kind_of(node.func)
            if kind is not None and kind.startswith('opaque:'):
                callee = kind[len('opaque:'):]
                resolved = _resolve_loader_call(callee, node, path, home, scope)
                if resolved is None:
                    raise NoData('dynamic loader requires a resolved import trace: %s' % path)
                name_literal, target = resolved
                loader_sites.append({'file': str(Path(path).absolute()), 'line': node.lineno,
                                      'name': name_literal, 'target': target, 'loader': callee})
                continue
            if kind not in ('module', 'builtin'):
                continue
            if _is_computed_loader_call(node):
                if work_input_functions and _enclosing_function(node, parents) in work_input_functions:
                    continue
                if any(isinstance(arg, ast.Starred) for arg in node.args) or None in {kw.arg for kw in node.keywords}:
                    raise NoData('computed loader arguments in %s' % path)
                raise NoData('computed import has no frozen resolution in %s' % path)
            keywords = {kw.arg: kw.value for kw in node.keywords}
            arg = node.args[0] if node.args else keywords.get('name')
            required.append(arg.value)
            if kind == 'builtin':
                level = node.args[4] if len(node.args) > 4 else keywords.get('level')
                if level is not None and (not isinstance(level, ast.Constant) or type(level.value) is not int or level.value != 0):
                    raise NoData('relative dynamic import requires resolved context in %s' % path)
                children = node.args[3] if len(node.args) > 3 else keywords.get('fromlist')
                if children is not None:
                    if not isinstance(children, (ast.List, ast.Tuple)) or any(
                            not isinstance(child, ast.Constant) or not isinstance(child.value, str)
                            or not child.value or child.value == '*' for child in children.elts):
                        raise NoData('computed fromlist has no frozen resolution in %s' % path)
                    optional.extend(arg.value + '.' + child.value for child in children.elts)
    return required, optional, loader_sites


def _otool_value(line, prefix):
    text = line[len(prefix):]
    marker = ' (offset'
    idx = text.rfind(marker)
    if idx != -1:
        text = text[:idx]
    return text.strip()


def _otool_load_commands(path):
    if sys.platform != 'darwin':
        raise NoData('native closure unsupported on platform %s for %s' % (sys.platform, path))
    otool = shutil.which('otool')
    if otool is None:
        raise NoData('otool unavailable for native closure of %s' % path)
    try:
        proc = subprocess.run([otool, '-l', path], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise NoData('otool timed out for %s' % path)
    except OSError as exc:
        raise NoData('otool failed for %s: %s' % (path, exc))
    if proc.returncode != 0:
        raise NoData('otool failed for %s' % path)
    lines = proc.stdout.splitlines()
    if not any(line.strip().startswith('Load command') for line in lines):
        raise NoData('otool produced no load commands for %s' % path)
    deps = []; rpaths = []; cmd = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('Load command'):
            cmd = None
            continue
        if stripped.startswith('cmd '):
            cmd = stripped[4:].strip()
            continue
        if cmd in ('LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB', 'LC_LAZY_LOAD_DYLIB', 'LC_LOAD_UPWARD_DYLIB'):
            if stripped.startswith('name '):
                deps.append(_otool_value(stripped, 'name '))
        elif cmd == 'LC_RPATH':
            if stripped.startswith('path '):
                rpaths.append(_otool_value(stripped, 'path '))
    return deps, rpaths


def _expand_special(value, binary):
    value = value.replace('@loader_path', os.path.dirname(os.path.abspath(binary)))
    return value.replace('@executable_path', os.path.dirname(os.path.realpath(sys.executable)))


def _resolve_native_dependency(dep, binary, rpaths):
    if os.path.isabs(dep):
        dep = os.path.normpath(dep)
    if dep.startswith('/usr/lib/') or dep.startswith('/System/Library/'):
        return dep
    if dep == '@loader_path':
        return os.path.dirname(os.path.abspath(binary))
    if dep.startswith('@loader_path/'):
        return os.path.abspath(os.path.join(os.path.dirname(binary), dep[len('@loader_path/'):]))
    if dep == '@executable_path':
        return os.path.dirname(os.path.realpath(sys.executable))
    if dep.startswith('@executable_path/'):
        return os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(sys.executable)), dep[len('@executable_path/'):]))
    if dep.startswith('@rpath/'):
        rest = dep[len('@rpath/'):]
        for rpath, declaring in rpaths:
            if not (os.path.isabs(rpath) or rpath.startswith('@loader_path') or rpath.startswith('@executable_path')):
                continue
            expanded = _expand_special(rpath, declaring)
            candidate = os.path.abspath(os.path.join(expanded, rest))
            if os.path.isfile(candidate):
                return candidate
        return None
    if os.path.isabs(dep):
        return os.path.abspath(dep)
    return None


def _scan_native(binary, inherited, system, linked, visited):
    binary = os.path.abspath(binary)
    if binary in visited:
        return
    visited.add(binary)
    deps, rpaths = _otool_load_commands(binary)
    own = [(rpath, binary) for rpath in rpaths]
    combined = own + inherited
    for dep in deps:
        if os.path.isabs(dep):
            dep = os.path.normpath(dep)
        if dep.startswith('/usr/lib/') or dep.startswith('/System/Library/'):
            system.add(dep)
            continue
        resolved = _resolve_native_dependency(dep, binary, combined)
        if resolved is None or not os.path.isfile(resolved):
            raise NoData('unresolved native dependency %s in %s' % (dep, binary))
        resolved = os.path.abspath(resolved)
        linked.add(resolved)
        _scan_native(resolved, combined, system, linked, visited)   # absolute (Homebrew style) libraries link on too


def native_closure(path):
    system = set(); linked = set(); visited = set()
    _scan_native(path, [], system, linked, visited)
    return {'system': sorted(system), 'linked': sorted(linked)}


def _is_extension_origin(origin):
    return any(origin.endswith(suffix) for suffix in machinery.EXTENSION_SUFFIXES)


def _freeze_extension(fullname, origin, files, imports, native):
    imports[fullname] = origin
    _freeze_native(origin, files, native)


def _freeze_native(origin, files, native):
    """Hash a Mach-O binary and every non-system library its load closure reaches."""
    files[origin] = file_record(origin)
    native[origin] = native_closure(origin)
    for linked in native[origin]['linked']:
        files[linked] = file_record(linked)


def _extension_prefix(name, roots):
    parts = name.split('.')
    for i in range(len(parts) - 1, 0, -1):
        prefix = '.'.join(parts[:i])
        found = resolve(prefix, roots)
        if found:
            fullname, spec = found[-1]
            if spec.origin:
                origin = str(Path(spec.origin).absolute())
                if _is_extension_origin(origin):
                    return prefix, origin
    return None


def _declared_optional(name, optional):
    """The declared top-level NAME covering this import, or None. optional maps NAME to CAPABILITY."""
    for declared in optional:
        if name == declared or name.startswith(declared + '.'):
            return declared
    return None


def _validate_optional(optional):
    """Each declaration is NAME=CAPABILITY: a dotted identifier and the capability its absence disables (D-21)."""
    entries = {}
    for entry in optional:
        name, sep, capability = entry.partition('=') if isinstance(entry, str) else ('', '', '')
        if (not sep or not name or any(not part.isidentifier() for part in name.split('.'))
                or not capability or any(not (c.isalnum() or c in '_-') for c in capability)):
            raise NoData('optional import must be NAME=CAPABILITY with a dotted identifier: %r' % (entry,))
        entries[name] = capability
    return entries


def _validate_work_inputs(entries):
    """Each declaration is RELPATH:FUNCTION=CONTRACT: a runtime relative file, the exact function whose
    computed import_module/__import__ call is accepted as work input, and the reason (D-21). A malformed
    entry, an absolute or escaping RELPATH, or a non-identifier FUNCTION refuses."""
    decls = []
    for entry in entries:
        left, sep, contract = entry.partition('=') if isinstance(entry, str) else ('', '', '')
        relpath, colon, function = left.rpartition(':') if left else ('', '', '')
        parts = Path(relpath).parts if relpath else ()
        if (not sep or not colon or not relpath or not function or not function.isidentifier()
                or not contract or any(not (c.isalnum() or c in '_-') for c in contract)
                or relpath.startswith('/') or relpath.startswith('~')
                or any(part in ('', '..') for part in parts)):
            raise NoData('work input must be RELPATH:FUNCTION=CONTRACT with a runtime relative path: %r' % (entry,))
        decls.append({'relpath': relpath, 'function': function, 'contract': contract})
    return decls


def _resolve_work_inputs(runtime, decls, path_set):
    """Resolve each declaration to its runtime file and prove its named function actually holds a
    computed loader call (D-21). A declaration naming a file outside the runtime, or one whose function
    has no computed loader (stale), refuses; the loader and its static dependencies stay frozen regardless."""
    by_path = {}
    for d in decls:
        abspath = str((Path(runtime) / d['relpath']).absolute())
        if abspath not in path_set:
            raise NoData('work input declares a file outside the runtime: %s' % d['relpath'])
        tree = ast.parse(Path(abspath).read_text(encoding='utf-8'), filename=abspath)
        kind_of = dynamic_bindings(tree, abspath)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        live = any(isinstance(node, ast.Call) and kind_of(node.func) in ('module', 'builtin')
                   and _enclosing_function(node, parents) == d['function'] and _is_computed_loader_call(node)
                   for node in ast.walk(tree))
        if not live:
            raise NoData('stale work input declaration, no computed loader found: %s:%s' % (d['relpath'], d['function']))
        by_path.setdefault(abspath, set()).add(d['function'])
    return by_path


def work_input_notes(manifest):
    """One line per declared work-input boundary and the contract it was accepted under (D-21)."""
    notes = []
    for entry in sorted(manifest.get('work_inputs') or []):
        left, _, contract = entry.rpartition('=')
        relpath, _, function = left.rpartition(':')
        notes.append('WORK-INPUT %s:%s (%s)' % (relpath, function, contract))
    return notes


_IMPORT_GUARDS = {'ImportError', 'ModuleNotFoundError', 'Exception', 'BaseException'}


def _catches_import_error(handler):
    kind = handler.type
    names = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return kind is None or any(isinstance(n, ast.Name) and n.id in _IMPORT_GUARDS for n in names)


def _lazy_or_guarded(path, name):
    """Proof that a module can load without NAME: every import of it sits in a function or lambda, or in the body
    of a try whose handler catches ImportError. No import site found is no proof (D-21)."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'), filename=str(path))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    tries = tuple(t for t in (getattr(ast, 'Try', None), getattr(ast, 'TryStar', None)) if t)
    sites = [n for n in ast.walk(tree)
             if (isinstance(n, ast.Import) and any(a.name == name for a in n.names))
             or (isinstance(n, ast.ImportFrom) and not n.level and n.module == name)]
    def proven(node):
        child, parent = node, parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return True
            if isinstance(parent, tries) and child in parent.body and any(_catches_import_error(h) for h in parent.handlers):
                return True
            child, parent = parent, parents.get(parent)
        return False
    return bool(sites) and all(proven(n) for n in sites)


def host_identity():
    """OS build, architecture and interpreter the closure was resolved on (D-21). Unreadable is NO-DATA."""
    ident = {'system': platform.system(), 'release': platform.release(), 'mac_ver': platform.mac_ver()[0],
             'machine': platform.machine(), 'python_version': sys.version,
             'implementation': sys.implementation.cache_tag or sys.implementation.name}
    if sys.platform == 'darwin':
        try:
            r = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.osversion'], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NoData('OS build unreadable: %s' % exc)
        build = r.stdout.strip() if r.returncode == 0 else ''
    else:
        build = platform.version()
    if not build or not ident['machine']:
        raise NoData('OS build or architecture unreadable')
    ident['os_build'] = build
    return ident


def capability_notes(manifest):
    """The honest answer for a declared optional capability whose module is absent: NO-DATA, never PASS."""
    declared = {}
    for entry in manifest.get('optional_imports') or []:
        name, _, capability = entry.partition('=')
        declared[name] = capability
    notes = []
    for name in manifest.get('absent') or []:
        top = _declared_optional(name, declared)
        notes.append('NO-DATA optional capability %s unavailable: %s is absent under the frozen interpreter'
                     % (declared.get(top, '?'), name))
    return notes


def _flat_fallback(path, name):
    """True when every import of the bare NAME in this file is the flat install fallback, a direct statement of
    an ImportError handler whose try body holds `from . import NAME`. That import runs only where the package
    import failed, which is a module run beside its sibling, so NAME resolves in the file's own directory."""
    if '.' in name:
        return False
    tree = ast.parse(Path(path).read_text(encoding='utf-8'), filename=str(path))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    tries = tuple(t for t in (getattr(ast, 'Try', None), getattr(ast, 'TryStar', None)) if t)
    def fallback(node):
        handler = parents.get(node)
        if not isinstance(handler, ast.ExceptHandler) or node not in handler.body or not _catches_import_error(handler):
            return False
        owner = parents.get(handler)
        return isinstance(owner, tries) and any(
            isinstance(s, ast.ImportFrom) and s.level == 1 and not s.module and any(a.name == name for a in s.names)
            for s in owner.body)
    sites = [n for n in ast.walk(tree) if isinstance(n, ast.Import) and any(a.name == name for a in n.names)]
    return bool(sites) and all(fallback(n) for n in sites)


def closure(runtime, module_roots, optional=(), work_input=(), home=None, seeds=None):
    """The frozen closure. SEEDS, when given, are (path, module) pairs scanned instead of every .py under
    RUNTIME, and the runtime walk is skipped; RUNTIME still leads the resolution roots (U3 item 1)."""
    roots = list(dict.fromkeys([str(Path(runtime).absolute())] + [str(Path(p).absolute()) for p in module_roots] + [p for p in sys.path if p]))
    root_set = {os.path.abspath(r) for r in roots}
    if seeds is None:
        paths = inventory(runtime)
        queue = [(p, '.'.join(Path(p).relative_to(runtime).with_suffix('').parts)) for p in paths if p.endswith('.py')]
    else:
        queue = [(str(Path(p).absolute()), m) for p, m in seeds]
        paths = sorted({p for p, _ in queue})
    path_set = set(paths)
    by_path = _resolve_work_inputs(runtime, work_input, path_set)
    files = {p: file_record(p) for p in paths}
    imports = {}; native = {}; provided = {}; absent = set(); scanned = set(); loader_sites = []
    while queue:
        path, module = queue.pop()
        if path in scanned:
            continue
        scanned.add(path)
        if module.endswith('.__init__'):
            module = module[:-9]
        required, optional_names, sites = imports_of(path, module, home=home, work_input_functions=by_path.get(path))
        for site in sites:
            loader_sites.append(site)
            target = site['target']
            files[target] = file_record(target)
            # What runs follows the loader, never the name: SourceFileLoader compiles any file as source;
            # spec_from_file_location picks by importlib's own suffix order (extension, source, bytecode).
            if site['loader'] == 'spec_from_file_location' and _is_extension_origin(target):
                _freeze_native(target, files, native)
            elif site['loader'] == 'SourceFileLoader' or target.endswith(tuple(machinery.SOURCE_SUFFIXES)):
                if target not in scanned:
                    queue.append((target, site['name']))
            else:
                raise NoData('loader target runs neither as source nor as an extension: %s' % target)
        # THE IMPORTER'S OWN DIRECTORY FIRST, WHEN IT IS A ROOT (review 15, 2026-10-03): a tool that is run as a script, or
        # that puts its own directory at sys.path[0] before importing (every scripts/loop tool does), finds a sibling before
        # any other root's module of the same name. Six names exist in both scripts/ and scripts/loop/ (grade_build.py among
        # them), and a candidate staged with the roots in declared order carried scripts/grade_build.py for land_batch's
        # `import grade_build`, so the closer's done check under a proof's code root imported the wrong module. A directory
        # that is not a root at all is still not searched here: a bare sibling import from inside a package stays unresolved.
        own = os.path.dirname(os.path.abspath(path))
        for name, mandatory in [(n, True) for n in required] + [(n, False) for n in optional_names]:
            found = resolve(name, ([own] if own in root_set else []) + roots)
            if found is None and mandatory and _flat_fallback(path, name):
                found = resolve(name, [os.path.dirname(path)])
            if found is None:
                if mandatory:
                    prefix = _extension_prefix(name, roots)
                    if prefix is not None:
                        provided[name], origin = prefix
                        _freeze_extension(provided[name], origin, files, imports, native)
                    elif not _declared_optional(name, optional):
                        raise NoData('unresolved import %s in %s' % (name, path))
                    elif resolve(_declared_optional(name, optional), roots) is not None:
                        raise NoData('unresolved import %s in %s: its declared optional package is present' % (name, path))
                    elif not _lazy_or_guarded(path, name):
                        raise NoData('unresolved import %s in %s: declared optional, but imported where its absence '
                                     'stops the module (not in a function and not under an ImportError guard)' % (name, path))
                    else:
                        absent.add(name)
                continue
            for fullname, spec in found:
                if is_stdlib(spec):
                    continue
                if spec.origin is None and spec.submodule_search_locations is not None:
                    continue
                if not spec.origin:
                    raise NoData('import has no readable origin: %s' % fullname)
                origin = str(Path(spec.origin).absolute())
                imports[fullname] = origin
                files[origin] = file_record(origin)
                if origin.endswith('.py'):
                    queue.append((origin, fullname))
                elif _is_extension_origin(origin):
                    _freeze_extension(fullname, origin, files, imports, native)
                else:
                    raise NoData('binary import dependency closure unavailable: %s' % fullname)
    loader_sites.sort(key=lambda s: (s['file'], s['line'], s['name'], s['target']))
    return files, imports, paths, native, provided, sorted(absent), loader_sites


_ASK_ROUTER = ('import json, sys\nsys.path.insert(0, sys.argv[1])\nimport model_router as R\n'
               'print(json.dumps({"claude": R.claude_bin(), "codex": R.codex_bin()}))\n')


def model_executables(imports, env=None):
    """{'claude': path, 'codex': path}: the executables the frozen model_router selects (claude_bin, codex_bin), asked
    of that router in a child interpreter under the environment being frozen, never re-derived here: a second copy of
    the resolution is what went stale once already (ACC5, see model_router.codex_bin). The loop runs these files by
    path, so their bytes are code the proof runs (Codex audit D1, 2026-09-27). A closure that imports no model_router
    selects none. A router that fails, or answers anything but an absolute path to an executable file, is NO-DATA:
    never omitted, never guessed. -B keeps the child from writing bytecode into the frozen runtime."""
    router = imports.get('model_router')
    if router is None:
        return {}
    try:
        r = subprocess.run([sys.executable, '-B', '-c', _ASK_ROUTER, os.path.dirname(router)], capture_output=True,
                           text=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NoData('model router %s gave no answer: %s' % (router, exc))
    try:
        chosen = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        chosen = None
    if chosen is None:
        raise NoData('model router %s gave no answer: %s' % (router, (r.stderr or r.stdout).strip()[-300:]))
    for name, path in sorted(chosen.items()):
        if not (os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.X_OK)):
            raise NoData('model executable %s cannot be frozen: %r is not an absolute path to an executable file'
                         % (name, path))
    return chosen


def build(runtime, module_roots, configs, env=None, optional=(), work_input=()):
    if not REQUIRED_CONFIGS.issubset(configs):
        raise NoData('configuration categories missing: %s' % sorted(REQUIRED_CONFIGS - set(configs)))
    optional_names = _validate_optional(optional)
    work_input_decls = _validate_work_inputs(work_input)
    runtime = str(Path(runtime).absolute())
    home = home_for(env)
    files, imports, paths, native, provided, absent, loader_sites = closure(
        runtime, module_roots, optional_names, work_input_decls, home)
    for kind, p in configs.items():
        path = str(Path(p).absolute())
        files[path] = dict(ABSENT) if kind in OPTIONAL_CONFIGS and not os.path.lexists(path) else file_record(p)
    executable = str(Path(sys.executable).resolve())
    files[executable] = file_record(executable)
    models = model_executables(imports, env)
    for path in models.values():
        files[path] = file_record(path)
    return {'schema': SCHEMA, 'runtime': runtime, 'runtime_files': paths, 'model_executables': models,
            'module_roots': [str(Path(p).absolute()) for p in module_roots],
            'configs': {k: str(Path(v).absolute()) for k, v in configs.items()},
            'files': files, 'imports': imports, 'python': executable,
            'native': native, 'provided': provided, 'absent': absent,
            'optional_imports': sorted('%s=%s' % kv for kv in optional_names.items()),
            'resolved_loaders': loader_sites,
            'work_inputs': sorted('%s:%s=%s' % (d['relpath'], d['function'], d['contract']) for d in work_input_decls),
            'home': home,
            'startup': startup_record(),
            'platform': host_identity(),
            'environment': env_record(os.environ if env is None else env),
            'coverage': 'all runtime files, statically resolvable transitive Python import origins, extension modules and their Mach-O load closure; system libraries named as OS-provided under the recorded OS build, architecture and interpreter; a declared optional import freezes its absence only when its module is absent and every import of it is lazy or ImportError-guarded, and that capability reports NO-DATA; a constant file loader (spec_from_file_location, SourceFileLoader) whose every name has exactly one binding in its file, resolved against frozen roots and the pinned home, is frozen and hashed like any other import; a SourceFileLoader target is scanned as Python source whatever its suffix, a spec_from_file_location target follows the importlib suffix lists (source scanned, extension through the native closure, bytecode or anything else refused); a declared work-input boundary leaves one named computed import_module/__import__ call frozen open inside its exact function; unresolved dynamic loads and every other computed import refuse; the Claude and Codex executables the frozen model_router selects are hashed by their bytes, asked of that router in a child interpreter under the frozen environment, and a router that fails or an answer that is not an absolute path to an executable file refuses'}


def verify(manifest, env=None):
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or not isinstance(manifest.get('files'), dict) or not manifest['files']:
        return 2, ['NO-DATA malformed or empty manifest']
    if 'optional_imports' not in manifest:
        return 2, ['NO-DATA missing optional_imports']
    if 'work_inputs' not in manifest:
        return 2, ['NO-DATA missing work_inputs']
    if 'home' not in manifest:
        return 2, ['NO-DATA missing home']
    if 'startup' not in manifest:
        return 2, ['NO-DATA missing startup']
    configs = manifest.get('configs')
    optional_paths = {str(Path(v).absolute()) for k, v in configs.items()
                      if k in OPTIONAL_CONFIGS} if isinstance(configs, dict) else set()
    missing = []; drift = []
    for path, expected in manifest['files'].items():
        try:
            if path in optional_paths and (expected == ABSENT or not os.path.lexists(path)):
                if expected != ABSENT or os.path.lexists(path):
                    drift.append('FAIL changed: ' + path)
                continue
            if not isinstance(expected, dict) or set(expected) != {'sha256','size','resolved'}:
                raise NoData('invalid file record')
            actual = file_record(path)
            if actual != expected:
                drift.append('FAIL changed: ' + path)
        except (OSError, ValueError, TypeError) as exc:
            missing.append('NO-DATA unreadable %s (%s)' % (path, type(exc).__name__))
    try:
        current = build(manifest['runtime'], manifest['module_roots'], manifest['configs'], env,
                         optional=manifest['optional_imports'], work_input=manifest['work_inputs'])
        for key in ('runtime_files', 'imports', 'environment', 'python', 'native', 'provided', 'absent',
                    'optional_imports', 'platform', 'resolved_loaders', 'work_inputs', 'home', 'startup'):
            if current[key] != manifest[key]:
                drift.append('FAIL drift in ' + key)
        if set(current['files']) != set(manifest['files']):
            drift.append('FAIL file closure changed')
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as exc:
        missing.append('NO-DATA closure unavailable: %s' % exc)
    notes = work_input_notes(manifest) + (capability_notes(manifest) if not missing and not drift else [])
    return (2 if missing else 1 if drift else 0), missing + drift + notes


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, sort_keys=True, indent=2, allow_nan=False); fh.write('\n')
            fh.flush(); os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb', choices=('write','verify'))
    parser.add_argument('path')
    parser.add_argument('--bin', default=os.path.expanduser('~/.claude/bin'))
    parser.add_argument('--module-root', action='append', default=[])
    parser.add_argument('--config', action='append', default=[], metavar='KIND=PATH')
    parser.add_argument('--optional-import', action='append', default=[], metavar='NAME=CAPABILITY')
    parser.add_argument('--work-input', action='append', default=[], metavar='RELPATH:FUNCTION=CONTRACT')
    parser.add_argument('--receipt'); parser.add_argument('--run-id')
    parser.add_argument('--phase', choices=('start','end'))
    args = parser.parse_args(argv)
    try:
        if args.verb == 'write':
            state = Path(os.environ.get('BROTHER_OR_STATE_ROOT') or os.path.expanduser('~/.claude/brother-or-dispatch-state'))
            parent = Path(args.bin).parent
            configs = dict(x.split('=',1) for x in args.config) if args.config else {
                'cap':str(state/'cap-grant.json'), 'burn':str(state/'cap-grant.json'),
                'registry':str(parent/'model-registry.json'), 'roles':str(parent/'loop-roles.json'),
                'intake':os.path.expanduser('~/.claude/evidence/loop-intake/CURRENT.json'),
                'launch':os.path.expanduser('~/.claude/evidence/loop-intake/launch-env.sh'),
                'canary':str(parent/'loop-canary.json'),
                'limits':str(state/'dispatch-limits.json'), 'policy':str(state/'dispatch-policy.json'),
                'catalog':str(state/'openrouter-models.json')}
            out = Path(args.path).absolute()
            if Path(args.bin).absolute() in out.parents:
                raise NoData('manifest output must be outside frozen runtime')
            manifest = build(args.bin, args.module_root, configs, optional=args.optional_import, work_input=args.work_input)
            atomic_json(args.path, manifest)
            for line in work_input_notes(manifest):
                print(line)
            for line in capability_notes(manifest):
                print(line)
            print('PASS manifest ' + str(out)); return 0
        raw = Path(args.path).read_bytes(); manifest = json.loads(raw)
        code, messages = verify(manifest)
        for line in messages:
            print(line)
        if not code:
            print('PASS frozen runtime unchanged')
        if args.receipt:
            if not args.run_id or not args.phase:
                raise NoData('verification receipt needs run-id and phase')
            atomic_json(args.receipt, {'schema':'loop-freeze-check-v1', 'run_id':args.run_id,
                'phase':args.phase, 'observed_at':datetime.now(timezone.utc).isoformat(),
                'manifest_sha256':digest(raw), 'verdict':('PASS','FAIL','NO-DATA')[code],
                'checked_files':len(manifest.get('files',{})), 'findings':messages})
        return code
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as exc:
        print('NO-DATA: %s' % exc); return 2


if __name__ == '__main__':
    sys.exit(main())
