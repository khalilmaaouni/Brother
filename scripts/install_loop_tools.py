#!/usr/bin/env python3
"""Install the loop's tools so the executed copy IS the reviewed copy, and say so out loud.

THE CLASS THIS EXISTS TO CLOSE, measured on 2026-09-21 in this tree:

  44 loop tools exist TWICE: a reviewed copy at scripts/loop/<name> and an executed copy at
  ~/.claude/bin/<name>, kept in step by a human remembering to copy. The bytes matched at the
  moment this was written, and 42 of the 44 installed files carry an mtime EARLIER than the last
  commit touching their repository twin, which says plainly how the estate actually works: the
  edit is made in bin, then copied back and committed. Every minute between those two acts is a
  minute the loop runs code no reviewer read.

  Four separate incidents the same night were all one bug wearing different clothes: a mutation
  landed on one copy while the reader read the other, so the mutation appeared to survive and a
  correct claim was reported as wrong (twice, in both directions), a registry path resolved to the
  repository from one copy and to HOME from the other, and an external suite read its subject by a
  path relative to itself and never saw the mutation at all.

  Byte parity, checked after the fact, cannot close that: two files that are equal NOW can be
  mutated apart in the next second, which is exactly what a mutation prover does on purpose.

THE FIX: ONE FILE, TWO NAMES. Installed as a symlink, ~/.claude/bin/<name> and scripts/loop/<name>
are the same inode. A mutation applied through either path is seen through both, so instances 1, 2
and 4 stop being possible rather than being caught later, and they stop being possible WITHOUT
editing the eight loop tools that reach their siblings through ~/.claude/bin: that line becomes
harmless once the two paths name one file.

WHAT IT COSTS, stated rather than discovered later:
  * the executed code follows the link target's checkout. A branch switch in that checkout changes
    what the loop runs. That is why --check PRINTS the target and its branch: the hazard is loud
    and attributable, where drift is silent. Point the links at a checkout that does not switch.
  * a deleted or moved checkout leaves dangling links, and the loop then fails with ENOENT instead
    of quietly running a stale copy. That is the correct failure direction: unknown blocks.
  * --apply refuses to link from a disposable agent worktree (.claude/worktrees), because that
    directory is removed when the agent finishes and would take all 44 tools with it.
  * `python3 ~/.claude/bin/x.py` keeps working, and so do the 89 references in this repository that
    spell that path, because a symlink is followed by the kernel. __file__ still reads as the link
    path, so the separate no-parent-counting rule is neither helped nor harmed by this change.

Rollback: --apply --mode copy restores plain copies from the source.

  python3 scripts/install_loop_tools.py --check          # safe from any directory, changes nothing
  python3 scripts/install_loop_tools.py --apply          # symlink every source tool into bin
  python3 scripts/install_loop_tools.py --apply --mode copy
"""
import argparse
import hashlib
import importlib.util
import marshal
import os
import types
import shutil
import stat
import struct
import subprocess
import sys

# ONE dirname, and this file is NOT mirrored: it lives in scripts/ and never in scripts/loop, so it
# genuinely knows where it is. The rule against counting parents of __file__ binds mirrored tools,
# whose two copies sit at different depths; it does not bind this one.
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.join(HERE, "loop")
EXT = (".py", ".sh")
DISPOSABLE = os.path.join(".claude", "worktrees")

NOT_APPLICABLE, PASS, FAIL, NO_DATA = 0, 0, 1, 3


def default_bin():
    # expanduser honours $HOME, so a hermetic test can put the installation anywhere.
    return os.path.expanduser("~/.claude/bin")


def digest(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def tools(source):
    return sorted(n for n in os.listdir(source)
                  if n.endswith(EXT) and os.path.isfile(os.path.join(source, n)))


def code_signature(code):
    """What "the same bytecode" means here, recursively, over the fields that decide behaviour.

    NOT marshal.dumps. Measured 2026-09-21 while building this: comparing marshal.dumps of a
    freshly compiled module against marshal.dumps of the same module loaded from its own .pyc
    differs on seven clean files in this very installation, although co_filename, co_code and the
    constants all match, because marshal encodes object sharing and interning that survive a round
    trip differently. That version of the check would have reported seven false failures on its
    first real run, and a check that cries wolf gets switched off."""
    return (code.co_name, code.co_argcount, code.co_kwonlyargcount, code.co_nlocals,
            code.co_flags, code.co_code, code.co_names, code.co_varnames,
            tuple(_const_signature(k) for k in code.co_consts))


def _const_signature(k):
    """A constant, canonicalised. The order inside a set or frozenset constant follows the hash
    seed, which differs between the process that wrote the .pyc and the one comparing it now:
    measured 2026-09-21 on probe_build.py, whose `except` clause compiles to a frozenset constant
    that reprs in a different order every run, and which the first version of this check therefore
    reported as changed bytecode on a file whose co_code was identical. Sorted, it is stable."""
    if isinstance(k, types.CodeType):
        return code_signature(k)
    if isinstance(k, (set, frozenset)):
        return ("set", tuple(sorted(repr(x) for x in k)))
    if isinstance(k, tuple):
        return tuple(_const_signature(x) for x in k)
    return repr(k)


def stale_pyc(binned, name):
    """A fourth drift channel the byte comparison cannot see, VERIFIED present on 2026-09-21:
    ~/.claude/bin/__pycache__ held 27 cached modules, because the loop tools import each other as
    siblings out of that directory (commit_scan.py inserts ~/.claude/bin on sys.path and imports
    grade_build). Two identical .py files say nothing about which BYTECODE actually executes.

    CPython accepts a timestamp based .pyc when the source mtime and size embedded in its header
    match the source. A mtime preserving install (shutil.copy2, `cp -p`) can therefore change the
    source bytes while the cache still validates, and the loop then runs the OLD module with the
    NEW file on disk: the same class as the two copy bug, one level down.

    So this does not trust the header. Where the header claims validity, the cached bytecode is
    compared against a fresh compile of the source it claims to come from. Returns a reason when
    they disagree, else None. A hash based .pyc validates itself against content and cannot go
    stale this way; a .pyc from another interpreter version is ignored by CPython anyway."""
    if not name.endswith(".py"):
        return None
    src = os.path.join(binned, name)
    cache = os.path.join(binned, "__pycache__")
    if not (os.path.isfile(src) and os.path.isdir(cache)):
        return None
    prefix = name[:-3] + ".cpython-"
    try:
        entries = sorted(os.listdir(cache))
    except OSError as e:
        return "cannot read %s (%s)" % (cache, e)
    for entry in entries:
        if not (entry.startswith(prefix) and entry.endswith(".pyc")):
            continue
        if ".opt-" in entry:
            continue                          # compiled at another optimize level: not comparable
        path = os.path.join(cache, entry)
        try:
            with open(path, "rb") as f:
                blob = f.read()
            if len(blob) < 16:
                return "%s is truncated" % entry
            if blob[:4] != importlib.util.MAGIC_NUMBER:
                continue                      # another interpreter: CPython will not load it
            flags, mtime, size = struct.unpack("<III", blob[4:16])
            if flags & 0b1:
                continue                      # hash based, self validating
            st = os.stat(src)
            if mtime != (int(st.st_mtime) & 0xFFFFFFFF) or size != (st.st_size & 0xFFFFFFFF):
                continue                      # header disagrees, so CPython recompiles: safe
            with open(src, "rb") as f:
                fresh = compile(f.read(), src, "exec", dont_inherit=True)
            if code_signature(marshal.loads(blob[16:])) != code_signature(fresh):
                return ("%s is cached bytecode that CPython will accept as valid and that was "
                        "NOT compiled from the file now on disk" % entry)
        except (OSError, ValueError, EOFError, SyntaxError) as e:
            return "%s unusable (%s)" % (entry, e)
    return None


def mode_mismatch(source, binned, name):
    """Mode parity is enforced for .sh and DELIBERATELY NOT for .py.

    Measured 2026-09-21: exactly one mirrored file disagrees on mode, commit_scan.py, 644 in the
    repository and 755 installed. It is a .py, and every call site in this repository spells
    `python3 <path>`, so its executable bit changes nothing. The shell tools are different:
    grade_lane.sh and grade_all_par.sh invoke ~/.claude/bin/grade_one.sh DIRECTLY, with no
    interpreter in front, so a shell tool installed without the executable bit fails to run. Mode
    is part of parity exactly where it is load bearing, and that is stated rather than left
    implied."""
    if not name.endswith(".sh"):
        return None
    dst = os.path.join(binned, name)
    if not os.path.exists(dst):
        return None
    if not os.access(dst, os.X_OK):
        return "installed without the executable bit, and it is invoked directly"
    return None


def state(source, binned, name):
    """What the installation says about one tool. The states are the whole contract."""
    src = os.path.join(source, name)
    dst = os.path.join(binned, name)
    if os.path.islink(dst):
        target = os.path.realpath(dst)
        if not os.path.exists(dst):
            return "LINK-BROKEN", target
        if target == os.path.realpath(src):
            return "LINK", target
        return "LINK-ELSEWHERE", target
    if not os.path.exists(dst):
        return "NOT-INSTALLED", None
    if not os.path.isfile(dst):
        return "NOT-A-FILE", None
    a, b = digest(src), digest(dst)
    if a is None or b is None:
        return "UNREADABLE", None
    return ("COPY" if a == b else "DRIFT"), None


def branch_of(path):
    """Named in --check output because with links the executed code follows this checkout."""
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def check(source, binned, require_link):
    if not os.path.isdir(source):
        # Cannot tell anything: never a pass. The caller asked about tools that are not here.
        print("NO-DATA: no source directory at %s, so nothing can be said about the installation"
              % source)
        return NO_DATA
    try:
        names = tools(source)
    except OSError as e:
        print("NO-DATA: cannot read %s (%s)" % (source, e))
        return NO_DATA
    if not names:
        print("NO-DATA: %s holds no .py or .sh tool, so there is nothing to install or compare"
              % source)
        return NO_DATA
    if not os.path.isdir(binned):
        # NOT APPLICABLE, chosen deliberately over both a pass and a failure, and matching the
        # wording the parity gate already uses. With no installation under this HOME there is no
        # executed copy that could drift and no stale loop that could run, so the property is not
        # unknown, it is vacuously safe. NO-DATA is reserved for "I could not look", which is a
        # different thing and stays a nonzero exit above.
        print("NOT APPLICABLE: nothing is installed at %s, so no executed copy can differ from "
              "the %d tool(s) in %s" % (binned, len(names), source))
        return NOT_APPLICABLE
    rows = []
    for n in names:
        st, target = state(source, binned, n)
        if st in ("COPY", "LINK"):
            why = stale_pyc(binned, n) or mode_mismatch(source, binned, n)
            if why:
                st, target = "BAD-INSTALL", why
        rows.append((n, st, target))
    bad = [r for r in rows if r[1] in ("DRIFT", "LINK-BROKEN", "LINK-ELSEWHERE", "NOT-A-FILE",
                                       "UNREADABLE", "BAD-INSTALL")]
    copies = [r for r in rows if r[1] == "COPY"]
    links = [r for r in rows if r[1] == "LINK"]
    absent = [r for r in rows if r[1] == "NOT-INSTALLED"]
    for name, st, target in rows:
        if st in ("COPY", "LINK", "NOT-INSTALLED"):
            continue
        print("FAIL %-12s %-26s %s" % (st, name, target or os.path.join(binned, name)))
    if require_link and copies:
        for name, _, _ in copies:
            print("FAIL COPY         %-26s installed as a copy while --require-link was asked: "
                  "a copy can be mutated apart from its source" % name)
    print("source %s (branch %s)" % (source, branch_of(source)))
    print("installed at %s: %d linked, %d copied, %d not installed, %d broken"
          % (binned, len(links), len(copies), len(absent), len(bad)))
    if bad or (require_link and copies):
        print("The executed loop does not match the reviewed source. Fix with: python3 %s --apply"
              % os.path.abspath(__file__))
        return FAIL
    if copies and not links:
        print("PASS with a standing hazard: every tool is a COPY, equal today and free to drift "
              "tomorrow. --apply replaces each copy with a link to the source, after which the "
              "two paths are one file and drift is impossible.")
    else:
        print("PASS: every installed loop tool is the reviewed source, %d of them as a link to it"
              % len(links))
    return PASS


def apply(source, binned, mode, force, force_worktree):
    if not os.path.isdir(source):
        print("NO-DATA: no source directory at %s" % source)
        return NO_DATA
    if mode == "link" and DISPOSABLE in os.path.abspath(source) and not force_worktree:
        # Measured hazard, not a hypothetical: this very file was written inside
        # .claude/worktrees/brother-unify-1.1, a directory that is deleted when the agent finishes.
        # Linking from it would leave every loop tool dangling the moment that happens.
        print("REFUSED: %s is inside %s, a disposable worktree that is removed when its agent "
              "finishes. Every link would dangle. Run this from a durable checkout, or pass "
              "--force-worktree if you truly mean this one." % (source, DISPOSABLE))
        return FAIL
    try:
        names = tools(source)
    except OSError as e:
        print("NO-DATA: cannot read %s (%s)" % (source, e))
        return NO_DATA
    try:
        os.makedirs(binned, exist_ok=True)
    except OSError as e:
        print("FAIL: cannot create %s (%s)" % (binned, e))
        return FAIL
    changed = refused = kept = 0
    for name in names:
        src = os.path.join(source, name)
        dst = os.path.join(binned, name)
        st, _ = state(source, binned, name)
        if (st == "LINK" and mode == "link") or (st == "COPY" and mode == "copy"):
            kept += 1
            continue
        if st == "DRIFT" and not force:
            # The installed copy is where edits are actually made on this estate (42 of 44 bin
            # mtimes precede their twin's last commit). Overwriting it unasked would destroy the
            # newer edit, so a drift is refused by name and the operator decides which way it goes.
            print("REFUSED %-26s installed copy differs from the source. Copy it back to %s "
                  "first if bin holds the newer edit, then rerun; or pass --force to overwrite it."
                  % (name, source))
            refused += 1
            continue
        if name.endswith(".sh") and not os.access(src, os.X_OK):
            # A link inherits its target's mode, so a shell tool that is not executable IN THE
            # REPOSITORY installs as a shell tool that cannot run. The fix belongs at the source
            # (git mode 100755), not in a chmod here that would hide it until the next clone.
            print("REFUSED %-26s the source is not executable, and this tool is invoked directly. "
                  "Fix the mode in the repository: chmod +x %s" % (name, src))
            refused += 1
            continue
        if st == "NOT-A-FILE":
            print("REFUSED %-26s %s exists and is not a regular file" % (name, dst))
            refused += 1
            continue
        try:
            if os.path.islink(dst) or os.path.exists(dst):
                os.remove(dst)
            if mode == "link":
                os.symlink(os.path.abspath(src), dst)
            else:
                # copyfile, NEVER copy2 or `cp -p`. copy2 preserves the source mtime, and CPython
                # validates a cached .pyc by comparing the mtime and size embedded in its header
                # against the source. A mtime preserving install can therefore leave a stale .pyc
                # looking valid, which is the very defect this tool removes, rebuilt by the tool
                # itself. copyfile gives the destination a fresh mtime.
                shutil.copyfile(src, dst)
                if name.endswith(".sh"):
                    # mode is load bearing for shell tools: two call sites run them directly.
                    os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            drop_cached_bytecode(binned, name)
        except OSError as e:
            print("FAIL    %-26s %s" % (name, e))
            refused += 1
            continue
        changed += 1
    print("%s: %d installed, %d already correct, %d refused, into %s"
          % (mode, changed, kept, refused, binned))
    # Anything in bin that this source does not know about is left exactly as it was: bin holds
    # unrelated tools, binaries and .bak files that are not ours to touch.
    return FAIL if refused else PASS


def drop_cached_bytecode(binned, name):
    """Belt and braces beside the fresh mtime: remove the module's cached bytecode outright, so
    nothing depends on CPython's invalidation rule being what we think it is."""
    if not name.endswith(".py"):
        return
    cache = os.path.join(binned, "__pycache__")
    prefix = name[:-3] + ".cpython-"
    if not os.path.isdir(cache):
        return
    for entry in os.listdir(cache):
        if entry.startswith(prefix) and entry.endswith(".pyc"):
            try:
                os.remove(os.path.join(cache, entry))
            except OSError:
                pass


def main(argv=None):
    p = argparse.ArgumentParser(description="install the loop's tools, or verify the installation")
    p.add_argument("--check", action="store_true", help="verify only, change nothing")
    p.add_argument("--apply", action="store_true", help="make the installation correct")
    p.add_argument("--mode", choices=("link", "copy"), default="link",
                   help="link (default, one file two names) or copy (the rollback)")
    p.add_argument("--source", default=DEFAULT_SOURCE)
    p.add_argument("--bin", dest="binned", default=None)
    p.add_argument("--require-link", action="store_true",
                   help="--check fails on a plain copy, for an estate that has migrated")
    p.add_argument("--force", action="store_true", help="overwrite an installed copy that differs")
    p.add_argument("--force-worktree", action="store_true")
    a = p.parse_args(argv)
    binned = a.binned or default_bin()
    source = os.path.abspath(os.path.expanduser(a.source))
    if a.apply:
        return apply(source, binned, a.mode, a.force, a.force_worktree)
    return check(source, binned, a.require_link)


if __name__ == "__main__":
    sys.exit(main())
