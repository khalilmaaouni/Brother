#!/usr/bin/env python3
"""TOKEN-04: a cheap draft is only cheap if its claims are checked.

THE RECORDED FAILURE THIS ANSWERS: DeepSeek hallucinates exact function
signatures, and a bigger budget does not fix it. The draft costs little; the
verification is deterministic and costs almost nothing, so it is mandatory
rather than advisory. Nothing here decides whether the draft is GOOD; it
decides whether every name the draft calls actually exists.

WHAT IT CHECKS, by parsing the draft with ast (never by regex over prose):
  import x / from x import y    the module must exist in the tree, and the
                                named attribute must exist in that module
  mod.func(...)                 for an imported module, the attribute must
                                exist, and when the call passes arguments the
                                signature must accept them
Unknown names (a local variable, a standard-library module, anything not
imported from the tree) are NOT judged: this refuses invented calls into OUR
code, and says plainly what it did not check.

FAIL DIRECTION: a draft that cannot be parsed, or a tree that cannot be read,
is REFUSED. An unverifiable draft is never a verified one.

  0  VERIFIED   every checked reference resolves
  1  REFUSED    at least one invented or mis-called reference, each named
  2  NO-DATA    the draft or the tree could not be read

Usage: python3 scripts/deepseek_draft.py --draft FILE --tree DIR
"""
import argparse
import ast
import importlib.util
import inspect
import os
import sys


class Unverifiable(Exception):
    """The draft or the tree could not be read, so nothing may be concluded."""


def _load_module(name, tree):
    path = os.path.join(tree, name + ".py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("drafted_" + name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a module that cannot load is NO-DATA
        raise Unverifiable("module %s in the tree could not be imported: %s: %s"
                           % (name, type(exc).__name__, exc))
    return module


def check_draft(source, tree):
    """(problems, checked): problems found, and HOW MANY references into the
    tree were actually examined.

    The count is not decoration. A draft that references nothing would
    otherwise print VERIFIED while nothing was verified, which is this
    estate's recorded trap of a population of NO-DATA composing into a PASS.
    main() reports zero checked as NO-DATA, never as verified."""
    try:
        parsed = ast.parse(source)
    except SyntaxError as exc:
        raise Unverifiable("the draft does not parse: %s" % exc)
    if not os.path.isdir(tree):
        raise Unverifiable("tree is not a directory: %s" % tree)

    problems, modules, aliases, checked = [], {}, {}, 0
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = _load_module(alias.name, tree)
                if mod is not None:
                    modules[alias.asname or alias.name] = (alias.name, mod)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mod = _load_module(node.module, tree)
            if mod is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                checked += 1
                if not hasattr(mod, alias.name):
                    problems.append(("invented", "%s has no attribute %s (imported by the draft)"
                                     % (node.module, alias.name)))
                else:
                    aliases[alias.asname or alias.name] = (node.module, getattr(mod, alias.name))

    for node in ast.walk(parsed):
        if not isinstance(node, ast.Call):
            continue
        target, label = None, None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            entry = modules.get(node.func.value.id)
            if entry is None:
                continue
            real_name, mod = entry
            label = "%s.%s" % (real_name, node.func.attr)
            checked += 1
            if not hasattr(mod, node.func.attr):
                problems.append(("invented", "%s does not exist in the tree" % label))
                continue
            target = getattr(mod, node.func.attr)
        elif isinstance(node.func, ast.Name) and node.func.id in aliases:
            real_module, target = aliases[node.func.id]
            label = "%s.%s" % (real_module, node.func.id)
            checked += 1
        if target is None or not callable(target):
            continue
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            continue  # a builtin or C callable: signature unknown, not judged
        args = [ast.literal_eval(a) if isinstance(a, ast.Constant) else None
                for a in node.args]
        kwargs = {kw.arg: None for kw in node.keywords if kw.arg}
        if any(isinstance(a, ast.Starred) for a in node.args) or \
                any(kw.arg is None for kw in node.keywords):
            continue  # *args / **kwargs: cannot be judged without running it
        try:
            sig.bind(*args, **kwargs)
        except TypeError as exc:
            problems.append(("mis-called", "%s%s rejects the draft's call: %s"
                             % (label, sig, exc)))
    return problems, checked


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    ap.add_argument("--tree", required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.draft, encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        print("deepseek-draft: NO-DATA, cannot read the draft (%s)" % exc)
        return 2
    sys.path.insert(0, os.path.abspath(a.tree))
    try:
        problems, checked = check_draft(source, a.tree)
    except Unverifiable as exc:
        print("deepseek-draft: NO-DATA, %s. An unverifiable draft is not a verified one." % exc)
        return 2
    if problems:
        print("deepseek-draft: REFUSED, %d problem(s) in %s:" % (len(problems), a.draft))
        for kind, detail in problems:
            print("  %-10s %s" % (kind, detail))
        return 1
    if not checked:
        print("deepseek-draft: NO-DATA, the draft makes no reference into %s, so nothing "
              "was verified. Nothing checked is not the same as everything correct." % a.tree)
        return 2
    print("deepseek-draft: VERIFIED, all %d reference(s) the draft makes into %s resolve. "
          "Not checked: whether the draft is correct, only that what it calls exists."
          % (checked, a.tree))
    return 0


if __name__ == "__main__":
    sys.exit(main())
