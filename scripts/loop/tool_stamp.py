#!/usr/bin/env python3
"""Versioned executed tools with a pass boundary switch (A2 of the 2026-09-24 diagnosis, issue I6).

WHY. A fix reached a running loop only through a stop, deploy, restart: four restarts on 2026-09-24 cost the pool's ten
minute cooldown each and the rounds in flight. The executed directory (~/.claude/bin) was a plain folder every tool read
by path at process start, so a deploy under a live driver was refused by design (a half copied directory is a mixed tree).

HOW. The executed directory becomes a SYMLINK to a stamped copy under ~/.claude/brother-tools/<stamp>/. A deploy STAGES a
new stamp (the current stamp's files, then the versioned loop tools over them), PROVES it (the canary run from the stage,
so the staged tools judge the fixtures), and SWAPS the symlink in one rename. A driver started through its realpath keeps
its own stamp for its whole run; every child spawned after the swap (runner_pool, unit_runner, the graders) resolves the
symlink at spawn and gets the new stamp, whole. Nothing is ever half copied. The previous stamp stays for rollback.

usage (launch worktree root):
  tool_stamp.py migrate                 once: move the plain ~/.claude/bin into a first stamp and leave the symlink
  tool_stamp.py stage <source dir>      stage a new stamp from the symlink's target plus <source dir> (scripts/loop); prints its path
  tool_stamp.py prove <stamp>           run the canary FROM the stamp; exit 0 WOULD LAND, else the canary's exit
  tool_stamp.py swap <stamp>            record PREVIOUS, then repoint the symlink (rename, atomic); prints old and new
  tool_stamp.py rollback                repoint to the previous stamp; NO-DATA when PREVIOUS names the active one
  tool_stamp.py status                  where the symlink points, the stamps present
  tool_stamp.py --selftest
Every failure path refuses by name and leaves the symlink where it was (or, if putting it back fails too, says FAIL and
where it points); an unreadable state is NO-DATA, never a swap."""
import os, shutil, subprocess, sys, tempfile, time

BIN = os.path.expanduser("~/.claude/bin")
STAMPS = os.path.expanduser("~/.claude/brother-tools")
SKIP_SUFFIXES = (".pyc",)
SKIP_NAMES = ("__pycache__",)


def _skip(name):
    """True for a name that must never reach the deployed bin: caches, backups, and every
    test_*.py (D-21, decision D-21: a test-only file breaks freeze_manifest.py's static import
    closure, and neither the driver nor the canary requires one, proven in
    scripts/test_deploy_excludes_tests.py)."""
    return (name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or ".bak" in name
            or (name.startswith("test_") and name.endswith(".py")))


def is_package(path):
    """A directory the deployed bin must carry whole: a Python package (it holds __init__.py). Any other directory
    under scripts/loop (abc/, the experiment arms) is not a tool the bin runs and stays out."""
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py"))


def copy_tools(src_dir, dest_dir, skip=()):
    """Copy every deployable entry of src_dir into dest_dir, newer over older: each file _skip admits, and each
    package directory (is_package) recursively under the same rule. Returns the number of files copied; a copy
    that fails raises, so a caller's partial stage is never silently short.
    WHY, 2026-10-04: scripts/loop/adapters/ (FX-31.5) is imported by model_call, and every copy site (the deploy,
    the stamp, the proof pair fixtures) copied files only, so the deployed bin could not import model_call and
    loop_intake status died with ModuleNotFoundError before any proof could start. ONE copier for every site,
    so a future package cannot repeat it."""
    copied = 0
    for name in sorted(os.listdir(src_dir)):
        if _skip(name) or name in skip: continue
        p = os.path.join(src_dir, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest_dir, name)); copied += 1
        elif is_package(p):
            os.makedirs(os.path.join(dest_dir, name), exist_ok=True)
            copied += copy_tools(p, os.path.join(dest_dir, name))
    return copied


def current(bin_path=BIN):
    """The stamp the symlink points at, or None when the executed directory is a plain folder or absent."""
    return os.path.realpath(bin_path) if os.path.islink(bin_path) else None


def migrate(bin_path=BIN, stamps=STAMPS, now=None):
    """Turn a plain executed directory into stamp 0 plus a symlink. Refuses when it is already a link or absent."""
    if os.path.islink(bin_path): return "NO-DATA: %s is already a symlink to %s" % (bin_path, os.path.realpath(bin_path)), 3
    if not os.path.isdir(bin_path): return "NO-DATA: %s is not a directory" % bin_path, 3
    os.makedirs(stamps, exist_ok=True)
    stamp = os.path.join(stamps, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-migrated")
    if os.path.exists(stamp): return "REFUSED: stamp %s exists" % stamp, 1
    os.rename(bin_path, stamp)
    os.symlink(stamp, bin_path)
    return "MIGRATED %s -> %s" % (bin_path, stamp), 0


def stage(source_dir, bin_path=BIN, stamps=STAMPS, now=None, label=""):
    """A new stamp: every file of the current stamp (minus caches, backups and test_*.py files), then every file of source_dir over it, the same way."""
    cur = current(bin_path)
    if cur is None: return "NO-DATA: %s is not a symlink; run migrate first" % bin_path, 3, None
    if not os.path.isdir(source_dir): return "NO-DATA: source %s is not a directory" % source_dir, 3, None
    stamp = os.path.join(stamps, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + (("-" + label) if label else ""))
    if os.path.exists(stamp): return "REFUSED: stamp %s exists" % stamp, 1, None
    os.makedirs(stamp)
    copied = 0
    for src in (cur, source_dir):
        copied += copy_tools(src, stamp)
    return "STAGED %s: %d files (current stamp %s, then %s)" % (stamp, copied, os.path.basename(cur), source_dir), 0, stamp


def prove(stamp, run=None, cwd=None):
    """The canary FROM the stamp: its tools judge the fixtures. Exit 0 is WOULD LAND."""
    tool = os.path.join(stamp, "loop_canary.py")
    if not os.path.isfile(tool): return "NO-DATA: %s has no loop_canary.py" % stamp, 3
    try:
        r = (run or subprocess.run)([sys.executable, "-B", tool], capture_output=True, text=True, timeout=1800, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "NO-DATA: the canary could not run from %s: %s" % (stamp, str(exc)[:100]), 3
    last = (r.stdout.strip().splitlines() or [""])[-1][:140]
    return ("PROVEN %s: %s" if r.returncode == 0 else "REFUSED %s: %s") % (os.path.basename(stamp), last), r.returncode


def _record(path, text):
    """Write PATH through a temp file beside it and one rename: a reader sees the old bytes or the new, never half."""
    fd, tmp = tempfile.mkstemp(prefix=".PREVIOUS-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp): os.remove(tmp)


def _point(link, dest):
    """Repoint LINK in one rename: a new link is made beside it and renamed over it, so no reader ever sees a gap."""
    tmp = link + ".next"
    if os.path.lexists(tmp): os.remove(tmp)
    os.symlink(dest, tmp); os.rename(tmp, link)


def swap(stamp, bin_path=BIN):
    """Record the way back in PREVIOUS, then repoint the symlink. PREVIOUS is on disk before the switch, so a crash at
    any point leaves rollback a stamp to return to; a switch that fails leaves or puts back the old stamp and says which."""
    if not os.path.isdir(stamp): return "NO-DATA: %s is not a directory" % stamp, 3
    if not os.path.islink(bin_path): return "NO-DATA: %s is not a symlink; run migrate first" % bin_path, 3
    new = os.path.realpath(stamp); old = os.path.realpath(bin_path); was = os.path.basename(old)
    try:
        _record(os.path.join(os.path.dirname(new), "PREVIOUS"), old + "\n")
    except OSError as exc:
        return "REFUSED: PREVIOUS not recorded (%s); %s unchanged at %s" % (exc, bin_path, was), 1
    try:
        _point(bin_path, new)
    except OSError as exc:
        # A rename can take effect and still report an error, so where bin points is read, never assumed.
        if os.path.realpath(bin_path) == old:
            return "REFUSED: swap failed (%s); %s unchanged at %s" % (exc, bin_path, was), 1
        try:
            _point(bin_path, old)
        except OSError as undo:
            return "FAIL: swap failed (%s) and restoring %s failed (%s); %s now points at %s" % (
                exc, was, undo, bin_path, os.path.realpath(bin_path)), 1
        return "REFUSED: swap failed (%s); %s restored to %s" % (exc, bin_path, was), 1
    return "SWAPPED %s: %s -> %s" % (bin_path, was, os.path.basename(stamp)), 0


def rollback(bin_path=BIN, stamps=STAMPS):
    try:
        prev = open(os.path.join(stamps, "PREVIOUS"), encoding="utf-8").read().strip()
    except OSError:
        return "NO-DATA: no PREVIOUS stamp recorded", 3
    if prev == current(bin_path):  # a crash between recording PREVIOUS and the switch leaves exactly this
        return "NO-DATA: PREVIOUS names the active stamp %s; no earlier stamp recorded" % prev, 3
    return swap(prev, bin_path)


def status(bin_path=BIN, stamps=STAMPS):
    cur = current(bin_path)
    present = sorted(n for n in os.listdir(stamps)) if os.path.isdir(stamps) else []
    return "%s -> %s | stamps: %s" % (bin_path, cur or "(plain directory, not migrated)", ", ".join(present) or "none"), 0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["--selftest"]: return selftest()
    if not a: print(__doc__.split("usage")[1][:600]); return 2
    v = a[0]
    if v == "migrate": line, code = migrate()
    elif v == "stage" and len(a) > 1: line, code, _ = stage(a[1], label="deploy")
    elif v == "prove" and len(a) > 1: line, code = prove(a[1])
    elif v == "swap" and len(a) > 1: line, code = swap(a[1])
    elif v == "rollback": line, code = rollback()
    elif v == "status": line, code = status()
    else: line, code = "NO-DATA: unknown verb or missing argument: %s" % " ".join(a), 3
    print(line); return code


def selftest():
    d = tempfile.mkdtemp(prefix="stamp-"); binp = os.path.join(d, "bin"); stamps = os.path.join(d, "stamps"); src = os.path.join(d, "loop")
    os.makedirs(binp); os.makedirs(src); os.makedirs(os.path.join(binp, "__pycache__"))
    open(os.path.join(binp, "a.py"), "w").write("old a\n"); open(os.path.join(binp, "or_ask.py"), "w").write("bridge\n"); open(os.path.join(binp, "a.py.bak-x"), "w").write("bak\n")
    open(os.path.join(src, "a.py"), "w").write("new a\n"); open(os.path.join(src, "loop_canary.py"), "w").write("import sys; sys.exit(0)\n")
    # a package (adapters/, 2026-10-04) is carried whole minus caches and test files; a plain directory (abc/) is not a tool
    os.makedirs(os.path.join(src, "pkg", "__pycache__")); os.makedirs(os.path.join(src, "arms"))
    for n, body in (("pkg/__init__.py", "pkg\n"), ("pkg/m.py", "m\n"), ("pkg/__pycache__/m.pyc", "c"), ("pkg/test_m.py", "t\n"), ("arms/run.sh", "sh\n")):
        open(os.path.join(src, n), "w").write(body)
    m, mc = migrate(binp, stamps, now=0)
    s, sc, stamp = stage(src, binp, stamps, now=60, label="t")
    files = sorted(os.listdir(stamp)) if stamp else []
    class R: pass
    def fake(rc):
        def run(argv, **k):
            r = R(); r.returncode = rc; r.stdout = "CANARY WOULD LAND\n" if rc == 0 else "CANARY REFUSED at grade\n"; return r
        return run
    p_ok = prove(stamp, run=fake(0)); p_red = prove(stamp, run=fake(1))
    before = os.path.realpath(binp); w, wc = swap(stamp, binp); after = os.path.realpath(binp)
    prev_recorded = open(os.path.join(stamps, "PREVIOUS")).read().strip()
    rb, rbc = rollback(binp, stamps)
    cases = [("a plain directory migrates into stamp 0 behind a symlink", mc == 0 and os.path.islink(binp) and os.path.isfile(os.path.join(os.path.realpath(binp), "or_ask.py"))),
             ("migrating twice is NO-DATA", migrate(binp, stamps, now=1)[1] == 3),
             ("a stage carries the current stamp's files plus the source's, newer over older, no caches or backups", sc == 0 and files == ["a.py", "loop_canary.py", "or_ask.py", "pkg"] and open(os.path.join(stamp, "a.py")).read() == "new a\n"),
             ("a stage carries a package whole, minus its caches and test files, and no plain directory", stamp is not None and sorted(os.listdir(os.path.join(stamp, "pkg"))) == ["__init__.py", "m.py"] and not os.path.exists(os.path.join(stamp, "arms"))),
             ("a stage from a plain directory is NO-DATA", stage(src, os.path.join(d, "plain"), stamps, now=2)[1] == 3),
             ("the canary from the stamp decides PROVEN or REFUSED", p_ok[1] == 0 and p_ok[0].startswith("PROVEN") and p_red[1] == 1 and p_red[0].startswith("REFUSED")),
             ("a stamp with no canary is NO-DATA", prove(src)[1] == 3 or prove(os.path.join(d, "nowhere"))[1] == 3),
             ("a swap repoints the symlink in one rename and records the previous stamp", wc == 0 and after == os.path.realpath(stamp) and before != after and prev_recorded == before),
             ("a rollback repoints to the previous stamp", rbc == 0 and os.path.realpath(binp) == before),
             ("a swap to a missing stamp is NO-DATA and moves nothing", swap(os.path.join(d, "absent"), binp)[1] == 3 and os.path.realpath(binp) == before),
             ("status names the target and the stamps", "stamps:" in status(binp, stamps)[0] and "migrated" in status(binp, stamps)[0])]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
