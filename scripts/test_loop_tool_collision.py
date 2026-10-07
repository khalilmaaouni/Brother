#!/usr/bin/env python3
"""test_loop_tool_parity.wrong_copy_installed: an installed tool whose bytes are a same named file OUTSIDE scripts/loop FAILS.

Replays the 2026-09-21 20:09 defect on temp directories: scripts/land_build.py (a worker's library) won the name and
became the executed landing tool. Run: python3 scripts/test_loop_tool_collision.py
"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_loop_tool_parity as P  # noqa: E402


def main():
    d = tempfile.mkdtemp(prefix="collision-"); b, l, o = [os.path.join(d, x) for x in ("bin", "loop", "other")]
    for x in (b, l, o): os.makedirs(x)
    w = lambda dr, n, t: open(os.path.join(dr, n), "w").write(t)
    w(l, "burn.py", "loop copy\n"); w(o, "burn.py", "other copy\n"); w(b, "burn.py", "loop copy\n")          # the loop copy won: fine
    w(l, "land.py", "real tool\n"); w(o, "land.py", "library\n"); w(b, "land.py", "library\n")               # the OTHER copy won: the defect
    w(o, "gone.py", "library\n"); w(b, "gone.py", "library\n")                                               # loop copy deleted, other won: the defect as it really happened
    w(o, "only.py", "worker deliverable\n")                                                                  # not installed at all: nobody's business
    w(l, "solo.py", "loop only\n"); w(b, "solo.py", "loop only\n")
    w(l, "same.py", "x\n"); w(o, "same.py", "x\n"); w(b, "same.py", "x\n")                                    # identical everywhere: no finding
    w(l, "quiet.py", "loop\n"); w(o, "quiet.py", "other\n"); w(b, "quiet.py", "other\n")                       # nobody in the loop names it: not its business
    w(l, "caller.py", 'run("land.py"); run("gone.py"); run("burn.py"); run("same.py"); run("solo.py")\n')       # what the loop RUNS, by quoted name
    refs = P.referenced_by_loop(l)
    got = sorted(n for n, _ in P.wrong_copy_installed(b, l, o, refs))
    cases = [("the loop copy winning is not a finding", "burn.py" not in got),
             ("the other copy winning over a live loop copy is a finding", "land.py" in got),
             ("the other copy winning after the loop copy was deleted is a finding, the real 2026-09-21 shape", "gone.py" in got),
             ("a worker file that is not installed is nobody's business", "only.py" not in got),
             ("a loop only tool is not a finding", "solo.py" not in got),
             ("exactly two findings, nothing invented", got == ["gone.py", "land.py"]),
             ("a name identical in all three places is not a finding", "same.py" not in got),
             ("a wrong copy of a tool NO loop tool names is not a finding (council_verify.py, 2026-09-22)", "quiet.py" not in got),
             ("referenced names are read from the loop's own quoted strings", {"land.py", "gone.py", "burn.py"} <= refs),
             ("the finding names the file that won", any(p_.endswith("other/land.py") for n, p_ in P.wrong_copy_installed(b, l, o, refs) if n == "land.py")),
             ("a missing other directory is no finding and no crash", P.wrong_copy_installed(b, l, os.path.join(d, "absent"), refs) == []),
             # declared_dir reads a bare name through its assignment (2026-09-23, probe_brief.py's `_HERE` refused every deploy)
             ("an alias of this file's directory binds to the reader's directory",
              P.declared_dir("_HERE", o, "_HERE = os.path.dirname(os.path.abspath(__file__))\n") == o),
             ("an alias of the installed directory binds to BIN", P.declared_dir("TOOLS", o, "TOOLS = '~/.claude/bin'\n") == P.BIN),
             ("a name assigned to something unreadable stays undetermined", P.declared_dir("X", o, "X = somewhere()\n") is None),
             ("a name with no assignment stays undetermined", P.declared_dir("_HERE", o, "") is None),
             ("a self assignment does not loop and stays undetermined", P.declared_dir("A", o, "A = A\n") is None),
             ("a chain of aliases longer than three reads stays undetermined, never a guess",
              P.declared_dir("A", o, "A = B\nB = C\nC = D\nD = __file__\n") is None),
             # imports are read by parsing: a docstring example is not an import (2026-09-23, provenance.py)
             ("an import inside a docstring is not an import", [m for m, _ in P.imports_of('"""usage:\n    import provenance as P\n"""\nimport json\n')] == ["json"]),
             ("a real import inside a function counts, at the statement's own column", P.imports_of("x = 1\ndef f():\n    import grade_build\n") == [("grade_build", 19)]),
             ("a pin earlier on the same line binds the import (diag_brief.py: sys.path.insert(0, BIN); import grade_build)", P.imports_of("sys.path.insert(0, BIN); import grade_build as G\n") == [("grade_build", 25)]),
             ("a file that does not parse falls back to the line regex, never to no imports", [m for m, _ in P.imports_of("import grade_build\ndef (:\n")] == ["grade_build"]),
             # sys.path[:0] = [...] is a prepend too; four lanes shipped it on 2026-09-26 and each broke the deploy canary
             ("a slice prepend is read in search order", getattr(P, "search_order", lambda t: None)("sys.path[:0] = [str(HERE), str(LOOP)]\n") == ["str(HERE)", "str(LOOP)"]),
             ("prepends of both forms: the latest first, a slice left to right",
              getattr(P, "search_order", lambda t: None)("sys.path.insert(0, A)\nsys.path[:0] = [B, f(C, [1, 2])]\nsys.path.insert(0, D)\n") == ["D", "B", "f(C, [1, 2])", "A"])]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
