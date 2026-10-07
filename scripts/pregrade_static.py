#!/usr/bin/env python3
"""NOT WIRED, AND IT SHOULD NOT BE. A measured dead end, kept so the idea is not had twice.

THE VERDICT FIRST, measured against 1,173 real build outcomes on this estate (285 that graded PASS, 888 that
graded FAIL):

    FALSE REJECTS   4 of 285 passing builds   <- disqualifying, the bar was ZERO
    TRUE REJECTS    18 of 888 failing builds (2.0 percent)
    ON TARGET       1 of 168 red-without-code (0.6 percent)
    SAVED           0.97 h across a population carrying about 25 machine hours

It catches under one percent of the class it was built for, and it still discards work that actually passed.
The idea is appealing and wrong: you cannot decide RED-WITHOUT-CODE by asking whether a test NAMES the symbol
an edit adds. A correct test frequently does not. It reaches new code through a public wrapper, a CLI, a
dispatch table, a string keyed registry, or a module that imports it. Excluding private symbols cut the false
rejects from 9 to 4 and did not remove them, because the same indirection happens with public names too.

WHAT TO DO INSTEAD, which the same measurement points at: run the REAL check cheaply rather than a proxy for
it. RED-WITHOUT-CODE is decided by applying the patch, removing the implementation, and running the build's own
done_check ONCE. That is seconds, not the 195 s of a full grade (sandbox, both Pythons, every mutation), and it
catches the whole class by construction because it IS the check. Cheapest harshest first, using the real
question in a cheap form, never a static guess at it.

ORIGINAL INTENT, kept for the record: reject a build whose tests cannot possibly fail without its code.

usage (repo root):
  python3 -B scripts/pregrade_static.py <build.json ...>
  python3 -B scripts/pregrade_static.py --selftest
Exit 0 when no build is statically rejectable, 1 when one is, 2 when a build cannot be read (NO-DATA).

WHY, measured 2026-09-21 over 25.25 machine hours and 80 consecutive grades.

  stage   in    pass  fail  yield  mean
  model   167   166   0     100%   144 s
  grade   166   61    102   37%    195 s
  probe   61    15    46    25%    576 s

21.17 of those 25.25 hours, 84 percent, went on work that was thrown away, about $17.66 of $21.07. The check
ordering is why: the probe is simultaneously the MOST EXPENSIVE check and the HARSHEST, and it runs LAST, so we
pay full model then full grade cost before the thing most likely to reject even looks.

Of 51 failing grades in that sample, 25 (49 percent) carried RED-WITHOUT-CODE: the grader deleted the builder's
implementation, re-ran the builder's own test, and it STILL PASSED. A test that cannot fail. That costs 195 s of
grading to discover, and a large share of it is decidable statically in milliseconds:

  IF a build's tests never reference ANY symbol its edits add, the test cannot fail without the code.

THE DIRECTION IS THE WHOLE SAFETY ARGUMENT. This check may only REJECT, never accept. A rejection here is
CERTAIN: a test that never mentions the new symbol provably cannot exercise it. Anything else, including every
case it cannot decide, passes through to the real grader unchanged. So the worst case is that it catches
nothing, never that it discards work that would have passed. Unreadable input is NO-DATA and passes through,
because refusing what it cannot read would be exactly that discard."""
import argparse
import ast
import json
import os
import re
import sys

DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def added_symbols(build):
    """Names the build's edits INTRODUCE: a def or class present in `replace` and absent from `find`.
    A rename or a signature change is not an addition, which is why both sides are read."""
    out = set()
    for e in (build.get("edits") or []):
        if not isinstance(e, dict):
            continue
        new = e.get("new_file_content")
        if isinstance(new, str):
            out.update(DEF_RE.findall(new))
            continue
        before = set(DEF_RE.findall(e.get("find") or ""))
        after = set(DEF_RE.findall(e.get("replace") or ""))
        out.update(after - before)
    # A PRIVATE SYMBOL IS NOT EVIDENCE, and this is the difference between a sound check and a harmful one.
    # Measured against 285 real PASSING builds: keying on private helpers produced 9 FALSE REJECTS, every one a
    # build that graded PASS. Both examined cases added an underscore helper (_parse_file_checked,
    # _validate_model) whose tests exercise it THROUGH THE PUBLIC SURFACE that calls it, never naming it. That
    # is correct design, not a bad test, so a test may legitimately never mention a private name. Only a PUBLIC
    # addition can support the conclusion "this test cannot fail without the code".
    return {s for s in out if not s.startswith("test_") and not s.startswith("_")}


def test_text(build):
    """Every character of test source the build ships, from whichever field carries it."""
    parts = []
    for t in (build.get("tests") or []):
        if not isinstance(t, dict):
            continue
        for k in ("new_file_content", "replace", "find"):
            v = t.get(k)
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts)


def names_in(source):
    """Every identifier the source mentions, by PARSING rather than by substring, so a name inside a comment or
    an unrelated string never counts as a reference. Unparseable source returns None, which means UNKNOWN and
    is never treated as absent."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                # BOTH the original name and the alias. Recording only the alias loses the imported symbol, so
                # `from m import widget as w` would look like it never mentions widget and the build would be
                # FALSELY REJECTED. A false reject is the one direction this check must never fail in, and its
                # own selftest caught this before it shipped.
                out.add(a.name.split(".")[-1])
                if a.asname:
                    out.add(a.asname)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
    return out


def judge(build):
    """(rejectable, reason). Rejects ONLY when the conclusion is certain."""
    if not isinstance(build, dict):
        return False, "not a build object; passing through to the grader"
    added = added_symbols(build)
    if not added:
        return False, "the edits add no new symbol, so nothing can be concluded statically"
    src = test_text(build)
    if not src.strip():
        return False, "the build ships no test source here; the grader decides"
    referenced = names_in(src)
    if referenced is None:
        return False, "the test source does not parse; the grader decides"
    hit = added & referenced
    if hit:
        return False, "tests reference %s, so they may fail without the code" % ", ".join(sorted(hit)[:3])
    return True, ("tests reference NONE of the %d symbol(s) the edits add (%s), so they cannot fail without "
                  "the code" % (len(added), ", ".join(sorted(added)[:4])))


def selftest():
    adds = {"edits": [{"path": "m.py", "find": "", "replace": "def widget(x):\n    return x\n"}]}
    good = dict(adds, tests=[{"path": "test_m.py",
                              "new_file_content": "from m import widget\ndef test_w():\n    assert widget(1) == 1\n"}])
    bad = dict(adds, tests=[{"path": "test_m.py",
                             "new_file_content": "def test_w():\n    assert 1 == 1\n"}])
    comment = dict(adds, tests=[{"path": "test_m.py",
                                 "new_file_content": "# widget is great\ndef test_w():\n    assert 1 == 1\n"}])
    broken = dict(adds, tests=[{"path": "test_m.py", "new_file_content": "def test_w(:\n"}])
    rename = {"edits": [{"path": "m.py", "find": "def widget(x):", "replace": "def widget(x, y):"}],
              "tests": [{"path": "t.py", "new_file_content": "assert True\n"}]}
    cases = [
        ("a test referencing the new symbol is never rejected", not judge(good)[0]),
        ("a test referencing nothing it adds IS rejected", judge(bad)[0]),
        ("the reason names the symbols", "widget" in judge(bad)[1]),
        ("a NAME IN A COMMENT is not a reference, and is still rejected", judge(comment)[0]),
        ("unparseable test source passes through, never rejected", not judge(broken)[0]),
        ("a build that adds no symbol passes through", not judge(rename)[0]),
        ("a build with no tests passes through", not judge(adds)[0]),
        ("a non object passes through", not judge("nonsense")[0]),
        ("an attribute reference counts", not judge(dict(adds, tests=[{"path": "t.py",
            "new_file_content": "import m\ndef test_w():\n    assert m.widget(1)\n"}]))[0]),
        ("an import alias counts", not judge(dict(adds, tests=[{"path": "t.py",
            "new_file_content": "from m import widget as w\ndef test_w():\n    assert w(1)\n"}]))[0]),
        ("a PRIVATE addition is never evidence, because tests reach it through the public surface",
         added_symbols({"edits": [{"find": "", "replace": "def _helper(x):\n    return x\n"}]}) == set()),
        ("a private addition therefore cannot cause a rejection",
         not judge({"edits": [{"find": "", "replace": "def _helper(x):\n    return x\n"}],
                    "tests": [{"path": "t.py", "new_file_content": "def test_a():\n    assert 1\n"}]})[0]),
        ("a test helper named test_ is not counted as an added symbol",
         "test_w" not in added_symbols({"edits": [{"find": "", "replace": "def test_w():\n    pass\n"}]})),
    ]
    bad_cases = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad_cases else "FAILED: " + ", ".join(bad_cases)))
    return 1 if bad_cases else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("builds", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.builds:
        ap.error("name at least one build json")
    rejected = 0
    for b in a.builds:
        try:
            with open(b, encoding="utf-8") as fh:
                build = json.load(fh)
        except (OSError, ValueError) as exc:
            print("NO-DATA  %s could not be read (%s); passing it through to the grader" % (b, exc))
            return 2
        bad, why = judge(build)
        if bad:
            rejected += 1
            print("REJECT   %s: %s" % (os.path.basename(b), why))
        elif not a.quiet:
            print("PASS     %s: %s" % (os.path.basename(b), why))
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
