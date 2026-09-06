#!/usr/bin/env python3
"""bm_repomap: a compact symbol and import map of a repository (F5).

WHY THIS EXISTS. A large repository does not fit in a context window, so a caller must pick a
handful of relevant files instead of reading the tree. This map is the compact structure that
selection reads: every .py file's own function/class/method names (symbols) and the modules it
imports, keyed by path. Borrowed from Aider's repository map, adapted with a second duty: the same
map doubles as the anchor source for tools/bm_freshness.py's revalidation (resolve_anchor_via_map
there), so a note's citation can be checked against a structured symbol table instead of only a
live grep. One structure, two callers; this file owns only the map itself, never either caller.

Symbols are qualified as "ClassName.method_name" for a function or async function defined directly
inside a class body, and left bare otherwise. A file this tool cannot parse (a real SyntaxError, or
one the running interpreter's own ast module raises for a construct it does not support) still gets
an entry -- {"symbols": [], "imports": [], "parse_error": True} -- so one bad file never drops the
rest of the map silently.

No subprocess: the no-network-claim gate in tools/test_bm.py bans a bare `import subprocess` in
every tools/*.py file unless it is named on that test's own allow-list, and finding a repo's git
top-level by walking upward for a .git entry answers the same question without joining that list.

Python 3.9, standard library only (ast, os, json, argparse), no network.
"""
import argparse
import ast
import json
import os
import sys

# Same convention tools/bm_freshness.py's NOISE_DIRS uses (its module docstring's "JOB 2" note);
# dot-dirs (.git, .venv, ...) are skipped separately because they start with ".".
NOISE_DIRS = {"node_modules", "__pycache__", "dist", "build"}


def _collect_symbols(tree):
    """Function/async-function/class names out of one module's AST, class methods qualified as
    "ClassName.method_name". A NodeVisitor rather than a flat ast.walk: only a def's direct class
    parent (tracked via a stack) tells a method apart from a same-named module-level function."""
    symbols = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self._class_stack = []

        def visit_ClassDef(self, node):
            symbols.append(node.name)
            self._class_stack.append(node.name)
            self.generic_visit(node)
            self._class_stack.pop()

        def _visit_func(self, node):
            if self._class_stack:
                symbols.append("%s.%s" % (self._class_stack[-1], node.name))
            else:
                symbols.append(node.name)
            self.generic_visit(node)

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

    _Visitor().visit(tree)
    return symbols


def _collect_imports(tree):
    """Module names out of `import x` and `from x import y` statements, `from . import y`
    (relative, module=None) skipped since there is no name to record."""
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def build_map(roots):
    """roots: an iterable of directory paths. Walks each, skipping NOISE_DIRS and dot-dirs, and
    ast-parses every .py file found. Returns {file_path: {"symbols": [...], "imports": [...],
    "parse_error": bool}}, keys inserted in sorted path order (dict order is insertion order in
    Python 3.7+, so the caller sees a deterministic map without having to sort it again)."""
    files = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in NOISE_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    files.append(os.path.join(dirpath, fn))

    result = {}
    for path in sorted(files):
        with open(path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            result[path] = {"symbols": [], "imports": [], "parse_error": True}
            continue
        result[path] = {
            "symbols": sorted(_collect_symbols(tree)),
            "imports": sorted(_collect_imports(tree)),
            "parse_error": False,
        }
    return result


def _git_top_level(start=None):
    """Walk upward from start (default cwd) looking for a .git entry. Stdlib only, no subprocess
    -- see the module docstring for why this file never shells out to `git rev-parse`."""
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="bm_repomap.py")
    parser.add_argument("--root", action="append", default=None,
                        help="directory to map; repeatable. Default: this repo's git top-level, "
                             "or the current directory when no .git is found.")
    parser.add_argument("--out", default="-", help="output path, or - for stdout (default -)")
    ns = parser.parse_args(argv)

    roots = ns.root or [_git_top_level() or os.getcwd()]
    roots = [r for r in roots if os.path.isdir(r)]
    if not roots:
        print("NO-DATA: no --root resolves to an existing directory", file=sys.stderr)
        return 2

    result = build_map(roots)
    text = json.dumps(result, sort_keys=True, indent=2)
    if ns.out == "-":
        print(text)
    else:
        with open(ns.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
