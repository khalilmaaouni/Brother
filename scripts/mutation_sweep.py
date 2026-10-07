#!/usr/bin/env python3
"""Measure whether a module's checks can actually FAIL, by breaking it on purpose.

WHY THIS EXISTS, and it is not a nice to have. On 2026-09-21 an adversarial review ran 90
mutations across seven modules of a unit that had just been closed green, and 39 SURVIVED: the
code was broken and every check stayed green. The worst survivor let a provider's authentication
error become the build answer with no failover, while the module's own selftest printed
"10 cases, OK".

The session that wrote those modules also wrote their tests and their closing check, and found
none of it. The lesson is NOT "be more careful". A person cannot audit their own blind spot by
trying harder, because the blind spot is what they were not thinking about. The lesson is that
the question "can this check fail?" must be asked by a MACHINE, on every module, every time.

TWO ROOT CAUSES this file exists to catch, both measured rather than theorised:

  NON ORTHOGONAL FIXTURES. Every failure fixture in model_call's selftest returned a non zero
  exit AND an empty body, so the empty body guard fired first and MASKED the exit code guard.
  Deleting the exit code guard changed nothing. A fixture that trips two guards proves neither.

  HELPERS TESTED, ENTRY POINTS NOT. land_batch's selftest covered four pure helpers and never
  main(), where the landing actually happens: eleven mutations to the bisect, both quarantine
  writers, the commit scan gate and the push parity all survived. commit_scan's selftest covered
  scan() and never main(), where the GATE lives, so the gate could be downgraded to a no op while
  the selftest printed "14 cases, OK".

usage:
  mutation_sweep.py --module scripts/loop/model_call.py --check "python3 scripts/loop/model_call.py --selftest"
  mutation_sweep.py --selftest

A mutation whose text is not in the module is NOT APPLIED: it gets a row, counts neither killed
nor survived, and stays out of the survivor total. A sweep where nothing applied is NO-DATA.
"""
import inspect, json, os, re, shutil, subprocess, sys, tempfile

#: Generic mutations. Each turns a guard into a no op or inverts a decision. They are deliberately
#: crude: a mutation that is clever is a mutation nobody will reproduce.
GENERIC = [
    ('if not ', 'if False and not '),          # a refusal that never refuses
    ('raise Refused', 'pass  # raise Refused'),
    ('return False', 'return True'),
    ('return 1', 'return 0'),                  # a nonzero verdict becomes success
    ('sys.exit(1)', 'sys.exit(0)'),
]


def run(cmd, timeout=600):
    """Real exit code, never through a pipe: an exit code after a pipe belongs to the pipe, and
    this estate misread three that way in one evening.

    NO BYTECODE CACHE. A .pyc is trusted by (mtime in whole seconds, size), and a mutation such as
    'return 1 -> return 0' keeps the size. Written in the same second, a mutant then matches the
    cache of the code before it, so the check runs the WRONG code: the previous mutant, or the
    restored module as its mutant. Measured 2026-09-26: a byte restored module failed its own
    green check because it still ran as its last mutant."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.SubprocessError as exc:
        return 125, str(exc)[:120]
    out = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode, (out[-1][:90] if out else "")


def sweep(module, check, mutations=None, limit=0):
    """Apply each mutation, run the check, restore. Returns one row per requested mutation, plus
    counts: survivors, applied, not_applied. A row's status is killed, SURVIVED or NOT APPLIED.

    RESTORES FROM A BYTE COPY, always, including on an exception, because a sweep that leaves a
    module mutated is worse than no sweep at all."""
    if not os.path.isfile(module):
        return None
    backup = tempfile.mkstemp(suffix=".bak")[1]
    shutil.copy2(module, backup)
    rows = []
    try:
        base_code, base_tail = run(check)
        if base_code != 0:
            return {"baseline": (base_code, base_tail), "rows": [], "survivors": None,
                    "applied": None, "not_applied": None}
        src = open(module, encoding="utf-8").read()
        for old, new in (mutations or GENERIC):
            n = src.count(old)
            if not n:
                # a mutation that vanished shrinks the total and reads exactly like one never
                # requested, so it keeps a row: it measured nothing, which is not a kill
                rows.append({"mutation": "%s -> %s" % (old[:40], new[:40]), "nth": None,
                             "exit": None, "survived": None, "tail": "", "status": "NOT APPLIED"})
                continue
            for i in range(min(n, limit or n)):
                parts = src.split(old)
                mutated = old.join(parts[:i + 1]) + new + old.join(parts[i + 1:])[len(old):] \
                    if False else src.replace(old, new, i + 1)
                open(module, "w", encoding="utf-8").write(mutated)
                code, tail = run(check)
                rows.append({"mutation": "%s -> %s" % (old[:40], new[:40]), "nth": i + 1,
                             "exit": code, "survived": code == 0, "tail": tail,
                             "status": "SURVIVED" if code == 0 else "killed"})
                open(module, "w", encoding="utf-8").write(src)
                break                      # one occurrence per mutation keeps the sweep honest and fast
        not_applied = sum(1 for r in rows if r["status"] == "NOT APPLIED")
        return {"baseline": (base_code, base_tail), "rows": rows,
                "survivors": sum(1 for r in rows if r["survived"]),
                "applied": len(rows) - not_applied, "not_applied": not_applied}
    finally:
        shutil.copy2(backup, module)
        os.unlink(backup)


def _cli(*args):
    """Run THIS file's main() as a caller would: the entry point is where the control lives."""
    r = subprocess.run([sys.executable, os.path.abspath(__file__)] + list(args),
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def _selftest_cases(d):
    def fixture(name, past=False):
        """A module with ONE real guard, and a check that exercises it. past=True dates the module
        far back, so no bytecode cache written this second can match it: the fixture then isolates
        its own guard from the no bytecode guard in run()."""
        mod, chk = os.path.join(d, name + ".py"), os.path.join(d, name + "_check.py")
        with open(mod, "w") as fh:
            fh.write("def judge(x):\n    if not isinstance(x, int):\n        return 1\n    return 0\n")
        with open(chk, "w") as fh:
            fh.write("import sys, importlib.util\n"
                     "s=importlib.util.spec_from_file_location('m', %r); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                     "sys.exit(0 if m.judge('x') == 1 and m.judge(3) == 0 else 1)\n" % mod)
        if past:
            os.utime(mod, (1, 1))
        return mod, "%s %s" % (sys.executable, chk)

    mod, check = fixture("m")
    good = sweep(mod, check)
    # the check imported m.py; a cache written now could outlive the byte restore as a mutant
    cached = os.path.isdir(os.path.join(d, "__pycache__"))
    # a check that cannot fail: it asserts nothing about the module
    blind = os.path.join(d, "b.py")
    open(blind, "w").write("import sys\nsys.exit(0)\n")
    bad = sweep(mod, "%s %s" % (sys.executable, blind))
    after = open(mod).read()
    # an explicit list naming one text the module holds and one it does not
    mixed = sweep(*fixture("ma", past=True),
                  mutations=[("if not ", "if False and not "), ("no such text", "x")])
    absent = [r for r in mixed["rows"] if r["mutation"].startswith("no such text")]
    # GENERIC over mb.py: 'if not ' and 'return 1' apply, the other three are not in it
    mb, mb_check = fixture("mb", past=True)
    cli_code, cli_out = _cli("--module", mb, "--check", mb_check)
    # a module holding none of the GENERIC texts, under a check that is green
    bare = os.path.join(d, "bare.py")
    with open(bare, "w") as fh:
        fh.write("def f():\n    return 2\n")
    bare_code, bare_out = _cli("--module", bare, "--check", "%s -c 'import sys'" % sys.executable)
    usage_flags = set(re.findall(r"(?<![\w-])--[a-z][\w-]*", __doc__.split("usage:", 1)[1]))
    unparsed = sorted(f for f in usage_flags if '"%s"' % f not in inspect.getsource(main))
    return [
        ("a real guard is killed by a mutation", good["survivors"] == 0 and bool(good.get("applied"))),
        ("a check that cannot fail shows EVERY applied mutation surviving",
         bool(bad.get("applied")) and bad["survivors"] == bad["applied"]),
        ("the module is restored byte exact", "if not isinstance(x, int)" in after and "False and" not in after),
        ("a red baseline is reported, never swept", sweep(mod, "%s -c 'import sys;sys.exit(1)'" % sys.executable)["survivors"] is None),
        ("a missing module is NO-DATA, never a pass", sweep("/no/such/module.py", "true") is None),
        ("a check runs with no bytecode cache, so no mutant outlives its restore", not cached),
        ("an absent mutation is a NOT APPLIED row the return value counts, never killed or survived",
         len(absent) == 1 and absent[0].get("status") == "NOT APPLIED" and absent[0]["survived"] is None
         and mixed.get("not_applied") == 1 and mixed.get("applied") == 1 and mixed["survivors"] == 0),
        ("main() totals only applied mutations and prints how many were not applied",
         cli_code == 0 and "mb.py: 0 of 2 mutation(s) SURVIVED" in cli_out
         and "3 mutation(s) NOT APPLIED" in cli_out),
        ("main() with no mutation applied is NO-DATA and exits nonzero, never a pass",
         bare_code != 0 and "NO-DATA" in bare_out),
        ("the usage text names only flags main() parses", bool(usage_flags) and not unparsed),
    ]


def selftest():
    d = tempfile.mkdtemp()
    try:
        cases = _selftest_cases(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)     # a selftest that leaks its temp dir is a footprint defect
    bad_names = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad_names else "FAILED: " + ", ".join(bad_names)))
    return 1 if bad_names else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    def arg(f, d=None):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d
    module, check = arg("--module"), arg("--check")
    if not (module and check):
        print(__doc__)
        return 2
    res = sweep(module, check)
    if res is None:
        print("NO-DATA: %s does not exist. That is not a pass." % module)
        return 1
    if res["survivors"] is None:
        print("NO-DATA: the check is already RED before any mutation (exit %d: %s). Fix it first; "
              "a sweep over a red baseline measures nothing." % res["baseline"])
        return 1
    for r in res["rows"]:
        print("  %-11s %-46s exit %-4s %s" % (r["status"], r["mutation"],
                                              "-" if r["exit"] is None else r["exit"], r["tail"][:40]))
    n, total = res["survivors"], res["applied"]
    print("\n%s: %d of %d mutation(s) SURVIVED" % (os.path.basename(module), n, total))
    if res["not_applied"]:
        print("%d mutation(s) NOT APPLIED: their text is not in the module, so they measured "
              "nothing and count neither killed nor survived." % res["not_applied"])
    if not total:
        print("NO-DATA: no mutation applied to %s, so nothing was measured. That is not a pass." % module)
        return 1
    if n:
        print("A survivor means the code was broken and the check stayed green. Each one names a "
              "property nothing tests.")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
