#!/usr/bin/env python3
"""Documentation is written AFTER a run, never inside it (owner order 2026-09-22).

His words: "Documentation happens AFTER landing and exiting the loop and final check is done to avoid issues", then
"Just remove the documentation part from inside the loop to have it outside the loop after landing and final checks".
On that date no tool on the run path asked for prose; the documentation role had been run by the orchestrator beside
the loop. This keeps it that way: a tool on the run path may not ask the router for the prose kind, and may not call
a document generator. scripts/system_doc.py is NOT documentation in this sense: it is the generated inventory that
scripts/required_fast.sh demands of every push, so the landing keeps regenerating it.
Exit 0 clean, 1 findings, 3 NO-DATA (no run path tool could be read). Run: python3 scripts/test_no_documentation_in_the_run.py
"""
import os, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_PATH = ("loop_until.sh", "loop_pass.sh", "runner_pool.py", "unit_runner.py", "grade_build.py", "probe_wave.py", "probe_build.py",
            "probe_round.py", "check_wave.py", "land_batch.py", "salvage.py", "pass_pulse.py", "stop_loop.sh")
FORBIDDEN = ((re.compile(r"""chain\(\s*["']prose["']"""), "asks the router for the prose kind"),
             (re.compile(r"""call_one\([^)]*["']prose["']"""), "calls a model for prose"),
             (re.compile(r"gen_loop_doc|gen_release_notes|release_notes\.py"), "calls a document generator"))


def findings(root, names=RUN_PATH):
    """(tools read, findings). A comment line is not a call."""
    read, out = 0, []
    for n in names:
        try:
            with open(os.path.join(root, n), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        read += 1
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            out += ["%s:%d %s" % (n, i, why) for rx, why in FORBIDDEN if rx.search(line)]
    return read, out


def main():
    d = tempfile.mkdtemp(prefix="no-docs-")

    def tree(files):
        t = tempfile.mkdtemp(dir=d)
        for n, src in files.items():
            with open(os.path.join(t, n), "w", encoding="utf-8") as f:
                f.write(src)
        return t
    cases = [("a run path tool asking for prose is found", findings(tree({"unit_runner.py": 'x = R.chain("prose", R.PUBLIC)\n'}))[1] != []),
             ("a run path tool calling a model for prose is found", findings(tree({"check_wave.py": 'a = M.call_one(n, p, "prose", R.PUBLIC)\n'}))[1] != []),
             ("a run path tool calling a document generator is found", findings(tree({"loop_pass.sh": "python3 scripts/loop/gen_loop_doc.py\n"}))[1] != []),
             ("a comment that mentions it is not a call", findings(tree({"loop_pass.sh": "# never run gen_loop_doc.py here\n"}))[1] == []),
             ("asking for a build is fine", findings(tree({"unit_runner.py": 'x = R.chain("build", R.PUBLIC)\n'})) == (1, [])),
             ("an empty tree reads nothing", findings(tree({}))[0] == 0)]
    bad = [n for n, good in cases if not good]
    read, found = findings(os.path.join(HERE, "loop"))
    for f in found:
        print("FAIL " + f)
    print("no documentation in the run: selftest %d cases, %s | %d run path tool(s) read, %d finding(s)"
          % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad), read, len(found)))
    if bad or found:
        return 1
    return 3 if read == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
