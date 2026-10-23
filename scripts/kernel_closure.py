#!/usr/bin/env python3
"""kernel_closure.py: the judge kernel, the files the loop may never activate on its own judgement (unit A2, COUNCIL D2).

  python3 scripts/kernel_closure.py           print the kernel, one repository path per line
  python3 scripts/kernel_closure.py --check   exit 0 when it equals scripts/kernel_closure.txt, 1 naming every
                                               difference, 2 when it cannot be derived (NO-DATA, never a pass)
                                               (SKIP, exit 0, only in the export tree: BROTHER_EXPORT_TREE=1 and no docs/plan/specs;
                                               NO-DATA without the variable)
  python3 scripts/kernel_closure.py --write   rewrite scripts/kernel_closure.txt (generated: never edit it by hand)
  python3 -I scripts/kernel_closure.py --roots a,b   the closure of those roots over the current directory
  python3 -I scripts/kernel_closure.py --tree DIR    {"roots", "kernel", "rows"} over DIR as JSON (the deploy tool's call)

THE ROOTS are the eight judge entry points of COUNCIL D2 plus scripts/loop_certificate.py, a judge by KEEP's verifier
rule (STABILIZE.md OPEN ITEMS 3). A root is kernel before it exists: the certifier (unit LC10) is not built yet, and a
kernel that quietly lost it when it is would let the loop land the program that certifies the loop.

THE CLOSURE follows, to a fixed point: every module a Python member imports (ast, top level and inside functions; a
name resolves against the importer's own folder, then scripts/, scripts/loop/, then the repository root, the order
the estate's sys.path lines give), and every script a shell member executes on a line that is not a comment (a
scripts/, products/ or plugin/ path ending .py or .sh, or a `-m` module). For scripts/required_fast.sh those lines are
its run_check rows, so the test files every gate row runs are kernel too: generation N runs them from its frozen copy,
never from the merged tree (A2), and a test weakened in N+1 judges nothing until the stamp moves.

WHAT IT DOES NOT SEE, named: a module reached only through a computed name (importlib with a built string) or a path
a tool builds at run time. Those stay the reviewer's; the drift check makes every derived change visible in one diff.
Standard library only."""
import ast
import importlib.machinery
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROOTS = ("scripts/loop/land_batch.py", "scripts/loop/grade_build.py", "scripts/close_unit.py",
         "scripts/pre_push_gate.py", "scripts/gate_ledger.py", "scripts/loop/deploy_stamped.py",
         "scripts/required_fast.sh", "scripts/gate_pair.sh", "scripts/loop_certificate.py")
LIST = os.path.join("scripts", "kernel_closure.txt")
SEARCH = ("scripts", os.path.join("scripts", "loop"), "")
SCRIPT_REF = re.compile(r"(?<![\w./-])((?:scripts|products|plugin)/[\w./-]+\.(?:py|sh))(?![\w/-])")
MODULE_REF = re.compile(r"(?:^|\s)-m\s+([A-Za-z_][\w.]*)")


class Underivable(Exception):
    """A member the closure must read and cannot: the kernel is unknown, never a shorter list."""


#: every file a directory finder loads a module from, in the import system's own order: an extension module (.so)
#: loads before its .py, and a lone .pyc loads where no source is (security review, 2026-10-19: a closure that saw only
#: .py let a lane shadow a judge's import with a compiled file nobody diffed)
SUFFIXES = tuple(importlib.machinery.EXTENSION_SUFFIXES) + (".py", ".pyc")


def resolve(name, importer_dir, root, dirs=None):
    """The repository path a dotted module name imports from importer_dir, or None (the standard library, a package
    outside the tree, a name no folder holds). Every loadable form counts: an extension module, the source, a lone
    .pyc, or a package's __init__ in any of those forms."""
    parts = name.split(".")
    for d in ((importer_dir,) + SEARCH) if dirs is None else dirs:
        base = os.path.join(d, *parts)
        for cand in [base + s for s in SUFFIXES] + [os.path.join(base, "__init__" + s) for s in SUFFIXES]:
            if os.path.isfile(os.path.join(root, cand)):
                return os.path.normpath(cand)
    return None


def python_deps(rel, text, root):
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        raise Underivable("%s does not parse: %s" % (rel, exc))
    here, out = os.path.dirname(rel), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level:
            pkg = here
            for _ in range(node.level - 1):
                pkg = os.path.dirname(pkg)
            names = ([node.module] if node.module else []) + [
                ("%s.%s" % (node.module, a.name)) if node.module else a.name for a in node.names]
            out += [r for r in (resolve(n, pkg, root, dirs=(pkg,)) for n in names) if r]
            continue
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + ["%s.%s" % (node.module, a.name) for a in node.names]
        else:
            continue
        out += [r for r in (resolve(n, here, root) for n in names) if r]
    return out


def shell_deps(text, root):
    out = []
    for line in text.splitlines():
        code = re.sub(r"(^|\s)#.*$", "", line)
        out += SCRIPT_REF.findall(code)
        out += [r for r in (resolve(m, "", root) for m in MODULE_REF.findall(code)) if r]
    return out


def closure(roots=None, root=None):
    """The sorted kernel: the roots, present or not, and every file reachable from them. Raises Underivable when a
    member that exists cannot be read or parsed. Defaults are read at call time (ROOTS, ROOT), so a caller that
    rebinds them is obeyed."""
    root = ROOT if root is None else root
    seen, todo = set(), list(ROOTS if roots is None else roots)
    while todo:
        rel = os.path.normpath(todo.pop())
        if rel in seen:
            continue
        seen.add(rel)
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue                       # a root not built yet is still kernel; a missing reference is not followed
        if not rel.endswith((".py", ".sh")):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise Underivable("%s cannot be read: %s" % (rel, exc))
        if rel.endswith(".py"):
            todo += python_deps(rel, text, root)
        elif rel.endswith(".sh"):
            todo += shell_deps(text, root)          # a compiled module is a leaf: it is kernel, it imports nothing we read
    return sorted(seen)


def tree_report(root):
    """{"roots", "kernel", "rows"} over the tree at root (A2.b): the roots as this copy names them, the closure, and the
    files the roots' shell members execute (their rows, never followed into imports). Raises Underivable."""
    rows = set()
    for rel in ROOTS:
        path = os.path.join(root, rel)
        if rel.endswith(".sh") and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    rows.update(shell_deps(fh.read(), root))
            except (OSError, UnicodeDecodeError) as exc:
                raise Underivable("%s cannot be read: %s" % (rel, exc))
    return {"roots": list(ROOTS), "kernel": closure(root=root), "rows": sorted(rows)}


#: THE EXPORT TREE IS NAMED BY THE RUN THAT BUILT IT, never inferred from a folder a batch could delete. The public export
#: ships no docs/plan/specs, so the committed list (the hub's) cannot describe it (its closure reads one file lost,
#: dream_policy.py, 2026-10-21), and --check there reports SKIP. The three runners of required_fast.sh inside an export
#: tree set this variable to "1": scripts/export_public.py check_required_fast, scripts/cut_preflight.py check_virgin_gate,
#: and .github/workflows/required-fast.yml (both jobs). Security review 2026-10-21: deciding "export" from the absence of
#: docs/plan/specs let a batch delete that folder in the hub and silence this row, so an absent folder WITHOUT the variable
#: is NO-DATA (exit 2, a failed row in the gate), and the variable in a tree that HAS the folder is ignored (the real check
#: runs). A process environment is not a file a batch can add to the tree, and the judge's box passes no such knob.
EXPORT_ENV = "BROTHER_EXPORT_TREE"


def specs_present():
    """docs/plan/specs next to THIS FILE's own tree (not ROOT, which a caller may rebind to a fixture)."""
    return os.path.isdir(os.path.join(os.path.dirname(HERE), "docs", "plan", "specs"))


def committed(root=None):
    """The committed list, or None when there is none to read."""
    try:
        with open(os.path.join(ROOT if root is None else root, LIST), encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    except (OSError, UnicodeDecodeError):
        return None


HEADER = ("# generated by python3 scripts/kernel_closure.py --write; never edit by hand (unit A2, COUNCIL D2).\n"
          "# The judge kernel: generation N runs these from its frozen copy, and the stamp moves for them only on the\n"
          "# owner's approval batched with the cut y. A drift reads FAIL at python3 scripts/kernel_closure.py --check.\n")


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) == 2 and args[0] == "--roots":
        # the closure of the named roots over the CURRENT DIRECTORY's tree: the builder preflight runs the base's copy of
        # this file in isolated mode (python3 -I) from outside the lane, so the lane's own files decide nothing here
        roots = [r for r in args[1].split(",") if r]
        try:
            print("\n".join(closure(roots=roots, root=os.getcwd())))
        except Underivable as exc:
            print("NO-DATA: the closure cannot be derived: %s" % exc)
            return 2
        return 0 if roots else 2
    if len(args) == 2 and args[0] == "--tree":
        # A2.b: the deploy tool asks THIS generation's copy for the next revision's kernel, by subprocess (a bin tool
        # loads no module by a computed path: the freeze refuses it)
        try:
            print(json.dumps(tree_report(args[1]), sort_keys=True))
        except Underivable as exc:
            print("NO-DATA: the kernel cannot be derived: %s" % exc)
            return 2
        return 0
    if any(a not in ("--check", "--write") for a in args) or len(args) > 1:
        print("NO-DATA: usage: kernel_closure.py [--check | --write | --roots a,b,c]")
        return 2
    try:
        kernel = closure()
    except Underivable as exc:
        print("NO-DATA: the kernel cannot be derived: %s" % exc)
        return 2
    if args == ["--write"]:
        with open(os.path.join(ROOT, LIST), "w", encoding="utf-8") as fh:
            fh.write(HEADER + "".join(k + "\n" for k in kernel))
        print("WROTE %s: %d file(s)" % (LIST, len(kernel)))
        return 0
    if args == ["--check"]:
        if not specs_present():
            if os.environ.get(EXPORT_ENV) == "1":
                print("SKIP: this is the public export tree (%s=1, no docs/plan/specs), which is not the tree %s describes; "
                      "the hub's gate runs this check" % (EXPORT_ENV, LIST))
                return 0
            print("NO-DATA: docs/plan/specs is absent and %s=1 is not set, so this is a hub tree that lost it, not the export; "
                  "the check will not skip itself" % EXPORT_ENV)
            return 2
        have = committed()
        if have is None:
            print("NO-DATA: %s is not there to compare with; write it with --write" % LIST)
            return 2
        added, gone = sorted(set(kernel) - set(have)), sorted(set(have) - set(kernel))
        for p in added:
            print("FAIL kernel gained   %s (the committed list does not name it)" % p)
        for p in gone:
            print("FAIL kernel lost     %s (the committed list names it, the closure no longer reaches it)" % p)
        if added or gone:
            print("FAIL: the kernel drifted from %s by %d file(s); review the change, then --write" % (LIST, len(added) + len(gone)))
            return 1
        print("PASS: the kernel equals %s (%d file(s), %d root(s))" % (LIST, len(kernel), len(ROOTS)))
        return 0
    print("\n".join(kernel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
