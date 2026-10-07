#!/usr/bin/env python3
"""The probe cache in scripts/loop/probe_wave.py: written once per spec, re-executed per round, dropped when it dies.

probe_wave.py does its work at module scope (it is a script the runner spawns), so its cache helpers are loaded from
its source with the script body cut off, and the whole script is then driven ONCE as a subprocess on a fabricated
wave with a pre-seeded cache and a stub executor, to prove the body calls the helpers. Run: python3 scripts/test_probe_cache.py
"""
import json, os, shutil, subprocess, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "loop", "probe_wave.py")


def helpers():
    src = open(SRC, encoding="utf-8").read()
    head = src[:src.index("wave, pw = os.path.abspath")]      # everything before the script body
    m = types.ModuleType("probe_wave_helpers"); m.__file__ = SRC
    exec(compile(head, SRC, "exec"), m.__dict__)
    return m


def main():
    P = helpers(); d = tempfile.mkdtemp(prefix="probe-cache-"); env_root = os.path.join(d, "cache")
    os.environ["BROTHER_PROBE_CACHE"] = env_root
    spec = os.path.join(d, "U.md"); open(spec, "w").write("# U\nsection\n"); tool = os.path.join(d, "probe_brief.py"); open(tool, "w").write("v1\n")
    probe = os.path.join(d, "p.json"); json.dump({"probe_script": "print(1)"}, open(probe, "w"))
    out = os.path.join(d, "out.json")
    k1 = P.probe_cache_dir("U.1", spec, tool)
    cases = [("a key is formed from the spec and the brief tool", bool(k1) and k1.startswith(env_root)),
             ("an unreadable spec gives no key, so nothing is cached under a guess", P.probe_cache_dir("U.1", os.path.join(d, "absent.md"), tool) == ""),
             ("a miss on an empty cache", P.probe_cache_take(k1, "deepseek", out) is False and not os.path.exists(out))]
    P.probe_cache_put(k1, "deepseek", probe)
    cases += [("put then take is a hit and copies the probe", P.probe_cache_take(k1, "deepseek", out) is True and json.load(open(out))["probe_script"] == "print(1)")]
    open(spec, "a").write("repaired\n"); k2 = P.probe_cache_dir("U.1", spec, tool)
    cases += [("a changed spec changes the key, so a repaired spec earns fresh probes", k2 != k1 and P.probe_cache_take(k2, "deepseek", os.path.join(d, "o2.json")) is False),
              ("a changed brief tool changes the key too", (open(tool, "w").write("v2\n") or True) and P.probe_cache_dir("U.1", spec, tool) != k2)]
    open(os.path.join(k1, "muse.json"), "w").write("{not json"); open(os.path.join(k1, "b.json"), "w").close()
    cases += [("a corrupt entry is a miss", P.probe_cache_take(k1, "muse", os.path.join(d, "o3.json")) is False),
              ("an empty entry is a miss", P.probe_cache_take(k1, "b", os.path.join(d, "o4.json")) is False)]
    other = os.path.join(d, "p2.json"); json.dump({"probe_script": "print(2)"}, open(other, "w")); P.probe_cache_put(k1, "deepseek", other)
    cases += [("a second writer of the same key keeps the first entry", json.load(open(os.path.join(k1, "deepseek.json")))["probe_script"] == "print(1)")]
    P.probe_cache_drop(k1, "deepseek")
    cases += [("a dropped entry is gone", not os.path.exists(os.path.join(k1, "deepseek.json"))),
              ("a log with a counts line and exit 0 means the probe ran", P.probe_ran("PROBES   12 run: 0 CRASH, 0 WRONG-ACCEPT?, 2 REFUSED, 10 RETURNED, 0 NO-DATA\nprobe-exit=0\n")),
              ("a log whose child died did not run, whatever it printed before", not P.probe_ran("fired baseline\nTraceback\nprobe-exit=1\n")),
              ("a log with exit 0 but no counts line did not run", not P.probe_ran("nothing\nprobe-exit=0\n"))]
    # THE ENTRY POINT: a wave with one PASS lane, a seeded cache for both adversary labels, a stub executor that prints a
    # counts line, and or_fanout shadowed to a script that refuses: a hit must dispatch NOTHING and reach CLEAN.
    repo = os.path.join(d, "repo"); os.makedirs(os.path.join(repo, "docs", "plan", "specs")); bin_ = os.path.join(d, "bin"); os.makedirs(bin_)
    json.dump({"units": [{"id": "U", "spec": "docs/plan/specs/U.md", "sub_units": ["U.1"]}]}, open(os.path.join(repo, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w"))
    open(os.path.join(repo, "docs", "plan", "specs", "U.md"), "w").write("# U\n")
    home = os.path.join(d, "home"); os.makedirs(os.path.join(home, ".claude")); os.symlink(bin_, os.path.join(home, ".claude", "bin"))
    open(os.path.join(home, ".brothersbe-private-names"), "w").write("# none\n")
    open(os.path.join(bin_, "probe_brief.py"), "w").write("import sys\nopen(sys.argv[4], 'w').write('brief')\n")
    open(os.path.join(bin_, "grade_build.py"), "w").write("def private_hits(t):\n    return 0\n")
    open(os.path.join(bin_, "probe_build.py"), "w").write("print('PROBES   3 run: 0 CRASH, 0 WRONG-ACCEPT?, 1 REFUSED, 2 RETURNED, 0 NO-DATA')\n")
    wave = os.path.join(d, "wave"); os.makedirs(os.path.join(wave, "grades")); os.makedirs(os.path.join(wave, "out"))
    open(os.path.join(wave, "grades", "U.1-r0.txt"), "w").write("PASS\nexit=0\n")   # the grader's own form: the rule reads the last lines; json.dump({"edits": []}, open(os.path.join(wave, "out", "U.1-r0-build.json"), "w"))
    seeded = P.probe_cache_dir("U.1", os.path.join(repo, "docs", "plan", "specs", "U.md"), os.path.join(bin_, "probe_brief.py"))
    for label in P.ADVERSARIES: P.probe_cache_put(seeded, label, probe)
    pkg = os.path.join(repo, "plugin", "runtime", "brother", "core"); os.makedirs(pkg)
    for part in ("plugin", "plugin/runtime", "plugin/runtime/brother", "plugin/runtime/brother/core"): open(os.path.join(repo, part, "__init__.py"), "w").close()
    open(os.path.join(pkg, "or_fanout.py"), "w").write("import sys\nsys.stderr.write('DISPATCHED: the cache did not hit\\n'); sys.exit(7)\n")
    pw = os.path.join(d, "pw")
    # F2b, 2026-09-26: probe_wave loads its tools from its OWN directory, never from ~/.claude/bin, so the stubs above
    # reach it only when it runs from that directory: a copy of the real probe_wave, and the hold reader it imports
    # first, sits beside them. The entry point under test is still the real file, byte for byte.
    # U3: probe_wave asks model_router, deployed beside it, for the code root its fan out runs from; the driver names
    # that root, and here it is the repo holding the refusing or_fanout stub, so a dispatch still says DISPATCHED.
    # 2026-09-27: probe_wave reads a grade through unit_ledger.grade_passed, so that reader is deployed beside it too.
    for real in (SRC, os.path.join(os.path.dirname(SRC), "loop_hold.py"), os.path.join(os.path.dirname(SRC), "model_router.py"),
                 os.path.join(os.path.dirname(SRC), "unit_ledger.py")): shutil.copy(real, bin_)
    r = subprocess.run([sys.executable, "-B", os.path.join(bin_, "probe_wave.py"), wave, pw], cwd=repo, capture_output=True, text=True, env=dict(os.environ, HOME=home, BROTHER_PROBE_CACHE=env_root, PYTHONPATH=repo, BROTHER_CODE_ROOT=repo))
    done = os.path.join(pw, "logs", "U.1.done")
    cases += [("the script exits 0 on the fabricated wave", r.returncode == 0),
              ("a cache hit is announced with its key", "PROBES cached 2 of 2" in r.stdout),
              ("no adversary was dispatched on a hit", not os.path.exists(os.path.join(pw, "jobs-0.json")) and "DISPATCHED" not in r.stdout + r.stderr),
              ("the cached probes were executed and the lane reached CLEAN", os.path.isfile(done) and open(done).read().strip() == "CLEAN")]
    open(os.path.join(bin_, "probe_build.py"), "w").write("import sys\nprint('Traceback: the probe died'); sys.exit(1)\n")
    pw2 = os.path.join(d, "pw2")
    r2 = subprocess.run([sys.executable, "-B", os.path.join(bin_, "probe_wave.py"), wave, pw2], cwd=repo, capture_output=True, text=True, env=dict(os.environ, HOME=home, BROTHER_PROBE_CACHE=env_root, PYTHONPATH=repo, BROTHER_CODE_ROOT=repo))
    cases += [("a cached probe that dies against the build reads NO-DATA, never CLEAN", open(os.path.join(pw2, "logs", "U.1.done")).read().strip() == "NO-DATA"),
              ("and it is dropped from the cache so the next round dispatches fresh", not any(os.path.exists(os.path.join(seeded, l + ".json")) for l in P.ADVERSARIES))]
    bad = [n for n, good in cases if not good]
    # the second adversary follows the day's worker mix (2026-09-24: 3 Muse probe jobs, 0 valid, every READY build NO-DATA)
    cases += [("deepseek only mix picks deepseek second", P.second_adversary("deepseek:8") == "deepseek"),
              ("muse in the mix keeps muse second", P.second_adversary("deepseek:6,muse:2") == "muse"),
              ("no mix set keeps the 2026-09-20 ruling", P.second_adversary("") == "muse"),
              ("the adversary table is built from it", P.ADVERSARY_MODEL["deepseek-b"] == P.second_adversary())]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    if bad and r.returncode: print(r.stdout[-600:], r.stderr[-600:])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
