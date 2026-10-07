#!/usr/bin/env python3
"""A loop tool must not read, at module scope, a name that is only ever bound inside a function.

Measured 2026-09-22 04:04 to 04:12 JST: unit_runner.py carried `import bounded as B` inside
build_models() and called B.run_bounded() at module scope. The file compiled, its selftests were
green (they call helpers, never the script body), and EVERY runner of the overnight run died with
NameError right after its paid model stage. Three passes, fifteen runners, zero graded builds.

py_compile cannot see this and pyflakes is not installed on both Pythons, so the check is stdlib
ast. It is flow insensitive on purpose: a name bound anywhere at module scope counts as bound.
# ponytail: flow insensitive, so use-before-assignment at module scope passes; add order if it bites.

Exit 0 clean, 1 findings, 3 NO-DATA (no loop tool could be read). Run: python3 scripts/test_loop_module_scope_names.py
"""
import ast, builtins, glob, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
IMPLICIT = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__package__", "__builtins__"}


def module_scope_undefined(source):
    """Sorted names read at module scope and bound nowhere at module scope. None when it cannot be known."""
    tree = ast.parse(source)
    bound, loads = set(), set()
    for node in ast.walk(tree):                      # `global x` inside a function binds x at module scope
        if isinstance(node, ast.Global):
            bound.update(node.names)

    def visit(node):
        if isinstance(node, SCOPES):
            if hasattr(node, "name"):
                bound.add(node.name)
            return                                   # its body is another scope: neither binds nor reads here
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "*":
                    raise LookupError("star import")
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Name):
            (loads if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        for child in ast.iter_child_nodes(node):
            visit(child)

    try:
        visit(tree)
    except LookupError:
        return None
    return sorted(loads - bound - IMPLICIT)


def check(root):
    """(exit code, lines) for every *.py under root: 0 clean, 1 findings, 3 NO-DATA. This is the entry logic."""
    read, findings = 0, []
    for p in sorted(glob.glob(os.path.join(root, "*.py"))):
        try:
            with open(p, encoding="utf-8") as f:
                names = module_scope_undefined(f.read())
        except (OSError, SyntaxError, ValueError) as e:
            findings.append("%s: unreadable (%s)" % (os.path.basename(p), e.__class__.__name__))
            continue
        if names is None:
            continue                                 # star import: not counted as read, never as clean
        read += 1
        findings += ["%s: %s read at module scope, bound only inside a function" % (os.path.basename(p), x) for x in names]
    if not read and not findings:
        return 3, ["NO-DATA: no loop tool could be read under %s" % root]
    return (1 if findings else 0), ["FAIL " + x for x in findings] + ["%d loop tool(s) read, %d finding(s)" % (read, len(findings))]


DEFECT = "def f():\n    import os as B\nB.getcwd()\n"
CASES = [
    ("the measured defect: bound in a function, read at module scope", DEFECT, ["B"]),
    ("module level import is bound", "import os as B\nB.getcwd()\n", []),
    ("a global declaration binds at module scope", "def f():\n    global B\n    B = 1\nf()\nprint(B)\n", []),
    ("builtins and dunders are bound", "print(len(__file__))\n", []),
    ("a read inside a function is not this check's business", "def f():\n    return nowhere\n", []),
    ("a star import is unknowable", "from os import *\ngetcwd()\n", None),
]
TREES = [  # the entry logic reaches all three verdicts, one condition each
    ("a tree with the defect is red", {"a.py": DEFECT}, 1),
    ("a clean tree is green", {"a.py": "import os\nos.getcwd()\n"}, 0),
    ("an empty tree is NO-DATA, never green", {}, 3),
    ("a tree of only unknowable files is NO-DATA", {"a.py": "from os import *\n"}, 3),
    ("an unparseable tool is red, never skipped", {"a.py": "def (:\n"}, 1),
]


def selftest():
    fails = ["%s: got %r want %r" % (n, module_scope_undefined(src), want)
             for n, src, want in CASES if module_scope_undefined(src) != want]
    for n, files, want in TREES:
        with tempfile.TemporaryDirectory() as d:
            for name, src in files.items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(src)
            got = check(d)[0]
        if got != want:
            fails.append("%s: exit %r want %r" % (n, got, want))
    return len(CASES) + len(TREES), fails


def main():
    n, fails = selftest()
    code, lines = check(os.path.join(HERE, "loop"))
    print("\n".join(["SELFTEST FAIL " + x for x in fails] + lines))
    print("module scope names: selftest %d cases, %s" % (n, "OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else code


if __name__ == "__main__":
    sys.exit(main())
