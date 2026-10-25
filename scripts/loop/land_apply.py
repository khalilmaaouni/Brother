#!/usr/bin/env python3
"""Apply a grader-PASSED build in a DISPOSABLE COPY of the tree (land_batch.make_copy: a detached worktree of HEAD plus
the tree's uncommitted landing state), run its tests plus the sibling suite of every file it touched there, then fuzz
each public function of each NEW module with hostile values, in a child process per module (bounded by FUZZ_TIMEOUT_S,
stdin closed, cwd a scratch directory), so no fuzzed call can touch the lander's output, argv, cwd or environment. Only
when every suite is green does the real tree receive the build: exactly the graded bytes, written by this file, never
by a suite. The copy is deleted either way. Prints a verdict; never commits.
ELEVENTH REVIEW 2026-10-02, reproduced: the suites ran in the landing tree under a sandbox rooted there, so a build's
code could plant an ignored .pyc that a later unsandboxed gate loaded, replace a landed file with a symlink the rollback
then wrote through, or leave a child behind that rewrote a landed file before git add. None of that reaches a copy.
usage (repo root): land_apply.py <build.json> [extra_suite ...]
RENAMED from land_build.py on 2026-09-22. A worker build landed a 116 line library named scripts/land_build.py (unit M2.1,
commit f5c218755, 2026-09-20 22:54), and on 2026-09-21 at 20:09 the tool installer copied that same named file over this
one in ~/.claude/bin: no main, exit 0, no output. land_batch read "no output" as fuzz crashes 99 and DROPPED every build
from then on; the last real landing was 20:01 that day. Found 2026-09-22 09:35 when eight clean builds dropped in one
second. This copy is byte identical to the one versioned at dde9045f3. A name the workers cannot collide with.
THE CHILD (FX-08, 2026-09-28): a fuzzed call given True as a file name closed the lander's own stdout, the lander exited
120 with no result line, and a READY build (L4.4) was dropped with no usable reason; another call given "x" created a
directory in the landing tree that changed later runs' fuzz. The child is scripts/loop/land_fuzz_child.py (2026-10-15: the computed import moved out of this file; the
BROTHER_LAND_FUZZ_INPROCESS switch, which no caller used, is retired). Switch: BROTHER_LAND_FUZZ_TIMEOUT_S a number in (0, 1800] replaces the 120 s bound
per module (default 120; anything else is ignored with a line saying so).
THE UNCHANGED COPY JUDGES EVERY RED (G10, 2026-10-16; measured 2026-10-14: a no-op build of native_worker.py was refused
with 16 failures and 36 errors that a sibling suite shows on unchanged code inside this sandbox, then parked on repeat). A
suite red on the built copy is run again, same command, interpreter, sandbox profile and environment shape, on a second
copy WITHOUT the build (made once, only when a red needs it; at most one base run per suite and interpreter per landing,
so a green landing pays nothing). Green there (or absent there): the build's red, VERDICT RED as before (exit 1). Red there
too: 'environment defect: <suite>' with both exit codes, never the build's; nothing lands; VERDICT BLOCKED-ENVIRONMENT
(exit 3) with a remedy line. The base run could not complete (no copy, sandbox refused, cannot start, timeout, a signal):
NO-DATA, nothing lands, VERDICT NO-DATA (exit 4). Any red of the build's own outranks both."""
import inspect, json, math, os, re, shutil, signal, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build  # the loop's own grade_build, never scripts/grade_build.py
import land_batch as _LB   # make_copy, drop_copy, run_group: one copy and one child discipline for the whole landing
HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
FUZZ_TIMEOUT_S = 120  # estimate, per module child: the calls are argument validation (spec FX-08 section 13 item 2)
FUZZ_TIMEOUT_MAX_S = 1800
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import loop_switches as _LS   # RUN_KNOBS lives there (2026-10-02)


suite_env = _LS.suite_env   # one definition, in loop_switches (2026-10-02)
def selftest():
    home = tempfile.mkdtemp(prefix="land-apply-selftest-")
    try:
        e = suite_env({"HOME": "/Users/someone", "GIT_DIR": "/x/.git", "BROTHER_RUN_DIR": "/r", "PATH": "/bin"}, home)
        cases = [("HOME is the fresh directory, never the caller's", e["HOME"] == home and e["HOME"] != "/Users/someone"),
                 ("TMPDIR lives under that HOME", e["TMPDIR"].startswith(home)),
                 ("git location variables and the run directory are dropped, the real git first on PATH (S6)",
                  "GIT_DIR" not in e and "BROTHER_RUN_DIR" not in e and e["PATH"] == os.pathsep.join([_LS.git_link_dir(), "/bin"])),
                 ("the run's routing and scope never reach a suite, its program pins do",
                  not _LS.RUN_KNOBS & set(suite_env({"BROTHER_TRANSPORTS": "claude", "BROTHER_SCOPE": "x", "BROTHER_CLAUDE_BIN": "/c"}, home))
                  and suite_env({"BROTHER_CLAUDE_BIN": "/c"}, home)["BROTHER_CLAUDE_BIN"] == "/c")]
        tree = os.path.join(home, "tree"); os.makedirs(os.path.join(tree, "scripts")); os.makedirs(os.path.join(home, "tmp"))
        with open(os.path.join(tree, "scripts", "fx08_selftest_mod.py"), "w", encoding="utf-8") as fh:
            fh.write("def echo(value):\n    return value\n")
        said = []
        got = fuzz_in_child("fx08_selftest_mod", [None], suite_env(os.environ, home), home, root=tree, say=said.append)
        cases.append(("a real child fuzzes a module file and reports one returned call", got == (0, 1, 0) and said == []))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    import types
    cli = types.ModuleType("fuzz_selftest_cli")
    exec("import argparse\ndef main(argv=None):\n    p = argparse.ArgumentParser()\n    p.add_argument('--need', required=True)\n"
         "    return p.parse_args(argv)\n", cli.__dict__)
    argv_before = sys.argv[:]
    try:
        crashed, _ = fuzz_module(cli, "fuzz_selftest_cli", [None, [], ["x"]])
        survived = True
    except SystemExit:
        crashed, survived = None, False
    cases.append(("a CLI main that exits on bad arguments is a refusal, never the lander's end",
                  survived and crashed == 0 and sys.argv == argv_before))
    bad = [n for n, v in cases if not v]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0
def _say(line):
    print(grade_build.one_line(line))
def fuzz_targets(m):
    """[(name, function, number of positional parameters)] for every public function module m defines itself."""
    out = []
    for fn_name, fn in inspect.getmembers(m, inspect.isfunction):
        if fn_name.startswith("_") or fn.__module__ != m.__name__: continue
        # EVERY positional parameter, defaults included: a function whose arguments all have defaults was called with nothing and so never fuzzed (found 2026-09-20)
        req = [p for p in inspect.signature(fn).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if req: out.append((fn_name, fn, len(req)))  # nothing to pass: a call with no arguments is not a hostile call
    return out
def fuzz_module(m, name, hostile, report=None, on_call=None):
    """(crashes, returned) for every public function of module m called with each hostile value. The CLASSIFIER: in
    child mode (the default) it runs inside the child process fuzz_in_child starts, never in the lander; it runs in the
    lander only by a direct caller (the switch that did so is retired). A ValueError (a documented refusal), LookupError,
    SystemExit or a class the module defines itself is a refusal; any other exception is a crash, handed to report
    (default: printed on ONE line through grade_build.one_line). on_call(fn_name, value, n) runs before each call.

    A SystemExit is the function refusing, never the lander's own end (2026-09-28: L4.4 and L5b.6 shipped a CLI
    main(argv=None); the fuzzer passed None, argparse read the LANDER's argv, raised SystemExit, and exit 2 took the
    whole landing down). sys.argv is the module's name alone for the calls, so a parser never reads ours."""
    report = report or _say
    crashes = returned = 0
    saved_argv = sys.argv[:]
    sys.argv = [name]
    try:
        for fn_name, fn, n in fuzz_targets(m):
            for h in hostile:
                if on_call: on_call(fn_name, h, n)
                try: fn(*[h] * n); returned += 1
                except (ValueError, LookupError, SystemExit): pass
                except Exception as e:
                    if type(e).__module__ == m.__name__ or isinstance(e, ValueError): continue
                    crashes += 1; report("   CRASH %s.%s(%r x%d) -> %s: %s" % (name.split(".")[-1], fn_name, h, n, type(e).__name__, str(e)[:80]))
    finally:
        sys.argv = saved_argv
    return crashes, returned
def fuzz_timeout(raw):
    """(seconds, ignored): BROTHER_LAND_FUZZ_TIMEOUT_S read strictly. Unset keeps FUZZ_TIMEOUT_S silently; a finite
    number in (0, FUZZ_TIMEOUT_MAX_S] replaces it; anything else keeps it and is returned as ignored. Never unbounded."""
    if raw is None: return FUZZ_TIMEOUT_S, None
    try: v = float(raw)
    except ValueError: return FUZZ_TIMEOUT_S, raw
    if not 0 < v <= FUZZ_TIMEOUT_MAX_S: return FUZZ_TIMEOUT_S, raw  # nan and inf fail this comparison too
    return v, None
def _record(path, obj):
    """One JSON line appended to the child's results file, opened afresh each time: a fuzzed call that closed fd 1,
    replaced sys.stdout or closed other descriptors cannot silence the record."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try: os.write(fd, (json.dumps(obj) + "\n").encode("utf-8"))
    finally: os.close(fd)
def _tail(path, limit=2000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END); fh.seek(max(0, fh.tell() - limit)); t = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [l for l in t.splitlines() if l.strip()]
    return lines[-1][:120] if lines else ""
def _call_text(rec):
    return "%s(%s x%s)" % (rec["start"], rec["value"], rec["n"]) if rec else "no call"
_SHAPES = {"plan": {"plan": int}, "start": {"start": str, "value": str, "n": int}, "crash": {"crash": str},
           "end": {"end": bool, "crashes": int, "returned": int}, "import_failed": {"import_failed": str}}
def read_child_results(results, mod, rc, timed_out, timeout, stderr_tail=""):
    """(crashes, returned, lines, bad) from a child's results file. EVERY unreadable or unknown state is a crash or RED,
    never clean: a missing file, a line that is not JSON, a record of unknown shape, counts that disagree, a missing end
    record, a nonzero exit, a timeout. An import failure is RED (bad 1), not a crash, as in process."""
    def unreadable(why):
        return 1, 0, ["   CRASH %s unreadable child result: %s" % (mod, why)], 0
    try:
        with open(results, encoding="utf-8") as fh: text = fh.read()
    except FileNotFoundError:
        if timed_out: return 1, 0, ["   CRASH %s timed out after %g s before its first record" % (mod, timeout)], 0
        return unreadable("no results file (child exit %s)%s" % (rc, " | " + stderr_tail if stderr_tail else ""))
    except (OSError, UnicodeDecodeError) as e:
        return unreadable("%s (child exit %s)" % (type(e).__name__, rc))
    recs = []
    for i, line in enumerate(text.splitlines()):
        try: rec = json.loads(line)
        except ValueError: return unreadable("line %d is not JSON" % (i + 1))
        kind = next((k for k in _SHAPES if isinstance(rec, dict) and k in rec), None)
        if kind is None or set(rec) != set(_SHAPES[kind]) or any(type(rec[f]) is not t for f, t in _SHAPES[kind].items()):
            return unreadable("line %d has an unknown record shape" % (i + 1))
        recs.append((kind, rec))
    kinds = [k for k, _ in recs]
    if kinds[:1] == ["import_failed"]:
        if len(recs) != 1: return unreadable("records after an import failure")
        return 0, 0, ["FUZZ import failed %s: %s" % (mod, recs[0][1]["import_failed"])], 1
    if not recs:
        if timed_out: return 1, 0, ["   CRASH %s timed out after %g s before its first record" % (mod, timeout)], 0
        return unreadable("no record (child exit %s)%s" % (rc, " | " + stderr_tail if stderr_tail else ""))
    if kinds[:1] != ["plan"] or kinds.count("plan") != 1 or kinds.count("end") > 1 or ("end" in kinds and kinds[-1] != "end"):
        return unreadable("records out of order: %s" % ",".join(kinds[:6]))
    plan = recs[0][1]["plan"]; starts = [r for k, r in recs if k == "start"]; crash_lines = [r["crash"] for k, r in recs if k == "crash"]
    end = recs[-1][1] if kinds[-1] == "end" else None
    lines, crashes = list(crash_lines), len(crash_lines)
    if len(starts) > plan: return unreadable("%d calls started, %d planned" % (len(starts), plan))
    last = _call_text(starts[-1] if starts else None); not_run = plan - len(starts)
    if timed_out:
        return crashes + 1, 0, lines + ["   CRASH %s timed out after %g s during %s, calls not run %d" % (mod, timeout, last, not_run)], 0
    if end is None:
        return crashes + 1, 0, lines + ["   CRASH %s child exit %s after %s, calls not run %d%s" % (mod, rc, last, not_run, " | " + stderr_tail if stderr_tail else "")], 0
    if len(starts) != plan or end["crashes"] != crashes or end["crashes"] + end["returned"] > plan:
        return unreadable("end record says %d crashed %d returned, file holds %d starts %d crash records, plan %d"
                          % (end["crashes"], end["returned"], len(starts), crashes, plan))
    if rc != 0:
        return crashes + 1, end["returned"], lines + ["   CRASH %s child exit %s after %s%s" % (mod, rc, last, " | " + stderr_tail if stderr_tail else "")], 0
    return crashes, end["returned"], lines, 0
def fuzz_in_child(name, hostile, env, home, i=0, root=None, timeout=FUZZ_TIMEOUT_S, say=_say, write_root=None):
    """(crashes, returned, bad) for module `name`, fuzzed in a bounded child process: land_fuzz_child.py run by path,
    wrapped by the sandbox exactly as the suites are, cwd the tree (so the import resolves as in process), a fresh HOME
    and TMPDIR, stdin /dev/null, stdout and stderr to files under home, its own process group killed on timeout. The
    child reports through home/fuzz-<i>.jsonl and relative writes land in home/fuzz-<i>/. Lines go to say().
    write_root: the folder the sandbox lets the child write (default root). The build seat passes its scratch home, so
    a fuzzed call can read the seat's checkout but never write into it (what it wrote would ride into the build)."""
    root = root or os.getcwd()
    idx = []
    for h in hostile:
        at = next((j for j, v in enumerate(HOSTILE) if v is h), None)
        if at is None: raise ValueError("hostile values must be HOSTILE's own objects: %r" % (h,))
        idx.append(str(at))
    results, work = os.path.join(home, "fuzz-%d.jsonl" % i), os.path.join(home, "fuzz-%d" % i)
    if os.path.lexists(results) or os.path.lexists(work):  # never read or append to a file this call did not create
        say("   CRASH %s unreadable child result: results path already existed" % name); return 1, 0, 0
    os.mkdir(work)
    cmd = [sys.executable, "-B", os.path.join(os.path.dirname(os.path.abspath(__file__)), "land_fuzz_child.py"), name, results, work, ",".join(idx)]
    try: cmd = grade_build.sandboxed(cmd, write_root or root, home)
    except grade_build.SandboxRefused as e:
        say("FUZZ sandbox refused %s" % e); return 0, 0, 1
    out, err = results[:-6] + ".out", results[:-6] + ".err"
    try:
        with open(out, "wb") as o, open(err, "wb") as r:
            p = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=o, stderr=r, start_new_session=True)
    except OSError as e:
        say("FUZZ child could not start %s: %s" % (name, e)); return 0, 0, 1
    timed_out = False
    try: p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try: os.killpg(p.pid, signal.SIGKILL)  # the whole group: a grandchild the hung call started dies with it
        except ProcessLookupError: pass
        except OSError: p.kill()
        p.wait()
    crashes, returned, lines, bad = read_child_results(results, name, p.returncode, timed_out, timeout, _tail(err))
    for line in lines: say(line)
    return crashes, returned, bad


def new_modules(build):
    """The paths the landing fuzzes: every NEW .py file among a build's edits (tests are not fuzzed). One reader for
    the landing (main) and the build seat (native_worker.seat_fuzz), so both fuzz exactly the same files."""
    return [e["path"] for e in (build.get("edits") or []) if "new_file_content" in e and e["path"].endswith(".py")]
def fuzz_new_modules(root, new_mods, env, home, timeout=FUZZ_TIMEOUT_S, say=_say, write_root=None):
    """(crashes, returned, bad) over new_mods (paths relative to root), each in its own bounded child: THE landing fuzz,
    one definition. The landing calls it on its disposable copy; the build seat (native_worker.seat_fuzz) on its own
    checkout before a build is handed back. The in-process switch stays in main (the deploy freeze accepts a computed
    import only there)."""
    crashes = returned = bad = 0
    for i, rel in enumerate(new_mods):
        name = module_name(root, rel)
        if name is None: say("FUZZ import failed %s: no importable module name" % rel); bad += 1; continue
        c, r, x = fuzz_in_child(name, HOSTILE, env, home, i, root=root, timeout=timeout, say=say, write_root=write_root)
        crashes += c; returned += r; bad += x
    return crashes, returned, bad
def _shown(v):
    return "object()" if type(v) is object else repr(v)
def hostile_contract():
    """THE HOSTILE INPUT CONTRACT every build brief carries, blind and native (build_brief.py, native_worker.contract_text):
    generated from HOSTILE and fuzz_module's classifier, so the brief and the fuzz that enforces it cannot drift apart.
    Its fixed regressions are the crash classes that dropped grader PASS builds at landing (FX-31.8 and MG1.a, 2026-10-04)."""
    return ("THE HOSTILE INPUT CONTRACT (executed, not advice: the landing fuzz runs it on every NEW .py module of your build, "
            "and a native build seat runs the same fuzz before your build is handed back; ONE crash drops the WHOLE build). "
            "Every public function (name not starting with _) that a NEW module defines is called once per value below, that SAME "
            "value in EVERY positional parameter, defaults included: %s. "
            "A call may return, or refuse by raising ValueError, LookupError (KeyError, IndexError), SystemExit, or an exception "
            "class your module defines. ANY other exception is a crash: TypeError, AttributeError, OSError, RecursionError and the rest. "
            "VALIDATION ORDER: check each argument's type FIRST, before iterating, indexing, converting (int(), float(), str or "
            "bytes methods), comparing, hashing or opening a file; bool is an int subclass, so test it before int; NaN fails every "
            "comparison, so test math.isfinite before a range check. A command line main(argv=None): None reads sys.argv; anything "
            "else that is not a list of str refuses with SystemExit(2) BEFORE list(argv) or argv[0]. "
            "FIXED REGRESSIONS (each dropped a grader PASS build at landing): main(0) -> TypeError: 'int' object is not iterable; "
            "main(b'x') -> TypeError: 'int' object is not subscriptable; main(object()) -> TypeError: 'object' object is not iterable. "
            "Write one test that calls every public function of every new module with each value above and asserts it returns or "
            "refuses as listed.\n" % ", ".join(_shown(v) for v in HOSTILE))
def tree_paths(root):
    """Where BOTH fuzz paths look for a new module and its imports, in the order the repository's tests put them on
    sys.path (scripts/, scripts/loop/, the root). The fuzz names a scripts/ or scripts/loop/ file by its stem, so
    scripts/loop/ must be here: without it (until 2026-09-29) every NEW loop module died at "FUZZ import failed
    plan_lint" (run_ledger, admit_rules, admit) and a build whose suites were green was dropped."""
    return [os.path.join(root, "scripts"), os.path.join(root, "scripts", "loop"), root]
def module_name(root, rel):
    """The name BOTH fuzz paths import a NEW file rel under: its dotted path from the deepest tree_paths entry holding
    it, a trailing __init__ naming the package itself. scripts/loop/adapters/__init__.py is "adapters",
    scripts/loop/adapters/claude.py "adapters.claude" (so its relative imports resolve), scripts/loop/x.py "x",
    plugin/a/b/__init__.py "plugin.a.b". None when no entry yields a name (a root __init__.py, a path outside the tree):
    the caller reports it RED. Until 2026-09-29 a scripts/ file was named by its bare stem, so every NEW package
    __init__.py was fuzzed as "__init__" and its landing dropped at "FUZZ import failed __init__"."""
    full = os.path.normpath(os.path.join(root, rel))
    for base in sorted(tree_paths(root), key=len, reverse=True):
        r = os.path.relpath(full, base)
        if r == os.pardir or r.startswith(os.pardir + os.sep): continue
        parts = r[:-3].split(os.sep)
        if parts[-1] == "__init__": parts.pop()
        if parts: return ".".join(parts)
    return None
def load(p):
    t = open(p, encoding="utf-8").read().strip(); t = re.sub(r"^```[a-z]*\n|\n```$", "", t)
    return json.loads(t[t.find("{"):t.rfind("}") + 1])
SHELLS = {"sh": "/bin/sh", "bash": "/bin/bash"}   # NOTE: /bin/bash on macOS is bash 3.2 (no declare -A, no mapfile)


def cmd_for(rel, root="."):
    """The command a suite runs under. A shell suite runs under the shell its first line names, ARGUMENTS KEPT
    ("#!/bin/bash -e" runs as /bin/bash -e), never under Python (2026-10-04: MG1.a's scripts/test_merge_verified.sh was
    run as Python on both interpreters and read 'SyntaxError', four landings in a row). Named shells: /bin/sh, /bin/bash,
    and /usr/bin/env [-S] sh|bash. Any other first line (no shebang, zsh, python, an unreadable file) is RED with its
    reason, never a silent /bin/sh (review round 13: a fallback dropped -e and could read GREEN). A Python suite runs
    as before."""
    if rel.endswith(".sh"):
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                first = fh.readline().strip()
        except OSError as exc:
            first, why = "", "unreadable (%s)" % exc
        else:
            why = "first line %r is not a shebang this lander runs (/bin/sh, /bin/bash, /usr/bin/env [-S] sh|bash)" % first[:80]
        words = first[2:].split() if first.startswith("#!") else []
        if words[:1] == ["/usr/bin/env"]:
            words = words[1:]
            if words[:1] == ["-S"]:
                words = words[1:]
            name = words[0] if words else ""
        else:
            name = {"/bin/sh": "sh", "/bin/bash": "bash"}.get(words[0] if words else "", "")
        args = words[1:]
        # only option flags that make a suite STRICTER (review round 14: '#!/bin/bash -c true' would run 'true', not the body)
        ok_args = all(re.fullmatch(r"-[eux]+", a) or (a == "-o" and i + 1 < len(args) and args[i + 1] == "pipefail")
                      or (a == "pipefail" and i > 0 and args[i - 1] == "-o") for i, a in enumerate(args))
        if name in SHELLS and not ok_args:
            why = "shebang arguments %r are not -e, -u, -x or -o pipefail" % " ".join(args)[:60]
        elif name in SHELLS:
            return [SHELLS[name]] + args + [rel]
        return ["/bin/sh", "-c", "echo 'REFUSED: shell suite %s: %s' >&2; exit 2" % (rel.replace("'", ""), why.replace("'", ""))]
    return [sys.executable, "-B", rel] if rel.startswith("scripts/") else [sys.executable, "-B", "-m", "unittest", rel[:-3].replace("/", ".")]
SUITE_TIMEOUT_S = 1200
#: exits that mean the run never finished: 124 timeout (run_group), 125 sandbox refused, 126 cannot execute, 127 not found
INCOMPLETE_EXITS = (124, 125, 126, 127)
def suite_cmd(rel, py, root):
    """The one command a suite runs under on EITHER copy: cmd_for, a Python suite under interpreter py."""
    c = cmd_for(rel, root)
    if not rel.endswith(".sh"): c[0] = py
    return c
def base_red(root, rel, py, home, env, run=None):
    """(red, exit) for suite rel on the UNCHANGED copy at root, run exactly as the built copy's run is (suite_cmd, the
    sandbox, env). red is True or False, or None when the run could not complete: no copy (root None), sandbox refused,
    cannot start, an exit in INCOMPLETE_EXITS, a signal (negative, or above 128 through a shell). A suite the copy does
    not hold is (False, None): it is the build's own, so its red can only be the build's."""
    if root is None: return None, None
    if not os.path.isfile(os.path.join(root, rel)): return False, None
    try:
        r = (run or _LB.run_group)(grade_build.sandboxed(suite_cmd(rel, py, root), root, home), SUITE_TIMEOUT_S, env, cwd=root)
    except (grade_build.SandboxRefused, OSError, subprocess.SubprocessError, ValueError):
        return None, None
    rc = r.returncode
    if isinstance(rc, bool) or not isinstance(rc, int) or rc < 0 or rc > 128 or rc in INCOMPLETE_EXITS: return None, rc
    return rc != 0, rc
def apply_build(root, items):
    """Verify every item against the tree at root (the grader's one path rule, the find exactly once), THEN write them
    there: exactly the graded bytes. Exits REFUSED before writing anything when one cannot apply. Returns the paths
    written. The same function writes the copy and, once the suites are green there, the landing tree."""
    for it in items:   # verify everything applies BEFORE writing anything
        rel = it["path"]
        full, why = grade_build.dest(root, rel)   # the grader's one path rule
        if why: sys.exit("REFUSED: " + why)
        if "new_file_content" in it:
            if os.path.exists(full): sys.exit("REFUSED: NEW file exists: " + rel)
        else:
            with open(full, encoding="utf-8", newline="") as fh:   # exact bytes, overlaps counted: grade_build's one test
                if len(grade_build.occurrences(fh.read(), it["find"])) != 1: sys.exit("REFUSED: find not unique in " + rel)
    touched = []
    for it in items:
        rel = it["path"]; full = os.path.join(root, rel)
        if "new_file_content" in it:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(it["new_file_content"])   # exactly the graded bytes (sixth review 2026-10-02: an added newline landed what was never graded)
        else:
            with open(full, encoding="utf-8", newline="") as fh:
                s = fh.read()
            with open(full, "w", encoding="utf-8", newline="") as fh:   # the one occurrence verified above, never every one
                fh.write(grade_build.replace_once(s, it["find"], it["replace"]))
        touched.append(rel)
    return touched
def main():
    b = load(sys.argv[1]); extra = sys.argv[2:]
    refused = grade_build.sandbox_ready()
    if refused: sys.exit("REFUSED: " + refused)
    items = (b.get("edits") or []) + (b.get("tests") or [])
    new_mods = new_modules(b)
    try:
        copy = _LB.make_copy()   # HEAD plus the landing tree's uncommitted state, outside it; the build's code runs here only
    except _LB.TreeUnreadable as exc:
        sys.exit("REFUSED: no disposable copy of the tree: %s" % exc)
    home = tempfile.mkdtemp(prefix="land-apply-home-"); os.makedirs(os.path.join(home, "tmp"), exist_ok=True)
    base = base_home = None   # the unchanged copy and its HOME, made only when a red needs judging
    try:
        touched = apply_build(copy, items)
        suites = [t["path"] for t in (b.get("tests") or [])]
        for rel in [e["path"] for e in (b.get("edits") or [])]:
            d, n = os.path.split(rel); sib = os.path.join(d, "test_" + n)
            if os.path.isfile(os.path.join(copy, sib)) and sib not in suites: suites.append(sib)
        suites += [s for s in extra if s not in suites]
        env = suite_env(os.environ, home); bad = 0
        judged, envdef, nodata = {}, [], []   # judged: {(suite, interpreter): (red, exit)}, one base run each per landing
        for py in (sys.executable, "/usr/bin/python3"):
            for s in suites:
                if s.endswith(".sh") and py != sys.executable: continue   # a shell suite has no Python version: it runs once
                c = suite_cmd(s, py, copy)   # a Python suite runs under BOTH interpreters
                c = grade_build.sandboxed(c, copy, home)
                r = _LB.run_group(c, SUITE_TIMEOUT_S, env, cwd=copy)   # its own process group, killed when it answers: nothing it started outlives it
                ran = [l for l in (r.stderr + r.stdout).splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
                label = "sh" if s.endswith(".sh") else ("py3.9" if py != sys.executable else "py3")
                print("%-7s %-62s exit=%d %s" % (label, s[-62:], r.returncode, " ".join(ran[-2:])[:60]))
                if not r.returncode: continue
                print("   " + " | ".join(l[:120] for l in (r.stderr + r.stdout).splitlines() if l.startswith(("FAIL:", "ERROR:", "ImportError", "SyntaxError", "TypeError")))[:500])
                if base_home is None:   # the first red: the unchanged copy, the same tree the built copy started from
                    base_home = tempfile.mkdtemp(prefix="land-apply-base-home-"); os.makedirs(os.path.join(base_home, "tmp"), exist_ok=True)
                    try: base = _LB.make_copy()
                    except _LB.TreeUnreadable as exc: print("BASE    no unchanged copy, every red reads NO-DATA: %s" % str(exc)[:160])
                if (s, py) not in judged: judged[(s, py)] = base_red(base, s, py, base_home, suite_env(os.environ, base_home))
                was, brc = judged[(s, py)]; what = "%s on %s" % (s, label)
                word = _LB.neighbour_verdict(True, was)   # land_batch's one rule: RED, INHERITED or NO-DATA for a red
                if word == "INHERITED":
                    envdef.append("%s (base exit %d, build exit %d)" % (what, brc, r.returncode))
                    print("ENVDEF  environment defect: %s: red on the unchanged copy too, never the build's" % envdef[-1])
                elif word == "RED":
                    bad += 1
                    print("BASE    %s %s on the unchanged copy: the build's red" % (what, "green" if brc is not None else "absent"))
                else:
                    nodata.append("%s (base exit %s, build exit %d)" % (what, brc, r.returncode))
                    print("NO-DATA %s: the unchanged copy's run did not complete, so this red is no one's yet" % nodata[-1])
        timeout, ignored = fuzz_timeout(os.environ.get("BROTHER_LAND_FUZZ_TIMEOUT_S"))
        if ignored is not None: _say("FUZZ    timeout setting ignored: %s" % ignored[:60])
        print("FUZZ    mode child (timeout %g s)" % timeout)
        crashes, returned, x = fuzz_new_modules(copy, new_mods, env, home, timeout); bad += x
        if not bad and not envdef and not nodata:
            apply_build(".", items)   # the landing tree receives exactly the graded bytes, only now, and only from this file
        print("FUZZ    new modules %d | crashes %d | calls that returned instead of refusing %d" % (len(new_mods), crashes, returned))
        if bad: verdict = "RED: %d" % bad
        elif nodata:
            verdict = "NO-DATA | base run did not complete: " + "; ".join(nodata)
            print("REMEDY  land again; a base run that keeps failing to complete is the landing host's to repair, never the build's")
        elif envdef:
            verdict = "BLOCKED-ENVIRONMENT | environment defect: " + "; ".join(envdef)
            print("REMEDY  repair the named suite (or what it needs) on the landing tree under the landing sandbox, then land again; the build is not charged")
        else: verdict = "SUITES GREEN"
        print("VERDICT %s | touched: %s" % (verdict, ", ".join(touched)))
    finally:
        _LB.drop_copy(copy); shutil.rmtree(home, ignore_errors=True)
        if base: _LB.drop_copy(base)
        if base_home: shutil.rmtree(base_home, ignore_errors=True)
    return 1 if bad else 4 if nodata else 3 if envdef else 0
if __name__ == "__main__": sys.exit(selftest() if "--selftest" in sys.argv else main())
