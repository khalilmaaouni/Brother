#!/usr/bin/env python3
"""Prune the shared scratch root, including the lanes named by hand.

WHY THIS EXISTS, measured rather than assumed. The resource footprint law
(2026-09-23) says every child's scratch lands under ~/.claude/brother-scratch
and is "pruned by age at every pass". The pruner that implemented it was one
line in loop_until.sh:

    find "$BROTHER_SCRATCH" -mindepth 1 -maxdepth 1 -name 'run-*' -mmin +1440 \\
        -exec rm -rf {} + 2>/dev/null

It has two defects, both seen on 2026-09-30 with 14.0 GB free on a 94 percent
full volume. First, 'run-*' matches only the date stamped run directories, so
every lane named by hand (a grade sandbox, a spec lane, an attack lane) was
never pruned at all: 98 entries holding 6.7 GB, 55 of them registered git
worktrees. Second, it deletes by age alone, which is survivable for a run
directory whose own test owns it and is NOT survivable once named lanes are
included, because a lane an hour old may be a live session's working tree.

So widening the pattern without adding a gate would trade a disk leak for
lost work. This module does both halves together, and adds the third thing
the one liner could not do: a registered worktree is removed with git, not
with rm, because rm leaves the registration behind. That had already happened
nine times by 2026-09-30, each one a "MISSING" row in git worktree list.

FAIL DIRECTION, stated because an unknown must never read as the safe case:
every unreadable, unresolvable or uncertain entry is KEPT. A lane is removed
only when it is provably past the age window, provably unheld, and provably
without uncommitted changes. NO-DATA is never a pass.

ROLLBACK: this module only deletes; it writes nothing else. Reverting the
loop_until.sh call restores the previous one line behaviour immediately, and
a removed clean worktree costs only its checkout, since its branch and every
commit on it survive by design.

usage:
  scratch_prune.py [--root DIR] [--repo DIR] [--max-age-hours N] [--dry-run]
  scratch_prune.py --selftest
exit: 0 pruned or nothing to prune, 2 the root could not be read
"""
import os
import shutil
import subprocess
import sys
import time

DEFAULT_ROOT = os.environ.get("BROTHER_SCRATCH") or os.path.expanduser(
    "~/.claude/brother-scratch")
DEFAULT_MAX_AGE = 24 * 60 * 60

KEEP_YOUNG = "keep: inside the age window"
KEEP_HELD = "keep: open file handles"
KEEP_DIRTY = "keep: uncommitted changes"
KEEP_UNREADABLE = "keep: state could not be read"
REMOVED_WORKTREE = "removed: registered worktree"
REMOVED_DIR = "removed: plain directory"


def _run(cmd, cwd=None):
    """Run a command, return (exit code, stdout), or (None, '') when it could
    not run at all. A missing tool must not read as a clean answer."""
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, p.stdout.decode("utf-8", "replace")


def open_handles(path, runner=None):
    """Count open file handles anywhere under path, or None when lsof could
    not answer. None is KEPT by the caller, never read as zero: a gate that
    reads absence from a tool which did not run is the recorded shape of
    trusting a check that measured nothing."""
    code, out = (runner or _run)(["lsof", "-nP", "+D", path])
    if code is None:
        return None
    return len([ln for ln in out.splitlines()[1:] if ln.strip()])


def registered_worktrees(repo, runner=None):
    """Absolute paths git itself reports as worktrees, or None when git could
    not answer. A folder's location and name prove nothing about whether it is
    a worktree: reading that from the path once produced a commit against the
    wrong repository, so this asks git and nothing else."""
    code, out = (runner or _run)(
        ["git", "-C", repo, "worktree", "list", "--porcelain"])
    if code is None or code != 0:
        return None
    found = set()
    for line in out.splitlines():
        if line.startswith("worktree "):
            found.add(os.path.realpath(line[len("worktree "):].strip()))
    return found


def dirty_count(path, runner=None):
    """Uncommitted path count in a worktree, or None when unreadable."""
    # --ignored (attack R1 F5): a gitignored output is somebody's work as much as an untracked file is; clean means
    # nothing at all outside the commits. Untracked files are listed by --porcelain already.
    code, out = (runner or _run)(["git", "-C", path, "status", "--porcelain", "--ignored"])
    if code is None or code != 0:
        return None
    # A regenerable cache is nobody's work (attack R2-3, 2026-10-01): counting a lone __pycache__ kept an abandoned
    # lane forever, 5 of 58 scratch entries that night. Only untracked or ignored cache paths are skipped; a tracked
    # change, or any other ignored file, still counts.
    return len([ln for ln in out.splitlines() if ln.strip() and not regenerable(ln, path)])



def clear_regenerable(path, runner=None):
    """Delete the regenerable cache paths git reports in a worktree about to be removed, so a plain
    `git worktree remove` (never --force) can proceed. Only paths regenerable() accepts, only inside the worktree after
    resolving, never through a symlink. An unreadable status deletes nothing, and the removal then refuses as before."""
    code, out = (runner or _run)(["git", "-C", path, "status", "--porcelain", "--ignored"])
    if code is None or code != 0:
        return 0
    base, n = os.path.realpath(path), 0
    for ln in out.splitlines():
        if not regenerable(ln, path):
            continue
        target = os.path.join(path, ln[3:].strip().strip('"').rstrip("/"))
        real = os.path.realpath(target)
        if os.path.islink(target) or not real.startswith(base + os.sep):
            continue
        if os.path.isdir(real):
            shutil.rmtree(real, ignore_errors=True)
        elif os.path.isfile(real):
            try:
                os.remove(real)
            except OSError:
                continue   # sbe: allow-silent a cache that cannot be removed leaves the worktree, and the removal refuses
        n += 1
    return n

REGENERABLE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache")


def regenerable(status_line, root=None):
    """True for a porcelain '??' or '!!' line whose path is only a Python or test cache (a directory named in
    REGENERABLE_DIRS anywhere in it, or a .pyc or .pyo file). Anything else, including a line this cannot parse, is
    work: False."""
    if not isinstance(status_line, str) or status_line[:3] not in ("?? ", "!! "):
        return False
    rel = status_line[3:].strip().strip('"').rstrip("/")
    if not rel:
        return False
    parts = rel.split("/")
    if not (any(d in parts for d in REGENERABLE_DIRS) or rel.endswith((".pyc", ".pyo"))):
        return False
    if root is None:
        return True
    # With the worktree known, a directory line qualifies only when every file under it is compiled bytecode or a
    # tool cache file (attack R3 L1: git reports a whole __pycache__/ holding real .py sources as one line).
    target = os.path.join(root, rel)
    if os.path.islink(target) or not os.path.isdir(target):
        return True
    seen = 0
    for top, dirs, files in os.walk(target, followlinks=False):
        for f in files:
            seen += 1
            if seen > 10000 or not (f.endswith((".pyc", ".pyo")) or any(d in top.split(os.sep) for d in (".pytest_cache", ".mypy_cache"))):
                return False
    return True


SCAN_CAP = 200000   # entries walked per lane before the age is called unreadable (and the lane KEPT)


def newest_mtime(path, cap=None):
    """The newest modification time ANYWHERE under path, the directory itself included, symlinks not followed; None
    when any part cannot be read or the walk passes the cap. WHY (attack R1 F5, 2026-10-01): the age came from the top
    directory's own mtime, which an edit to a file inside never moves, so a worktree with a file written seconds ago
    read 72 hours old and was deleted. A lane's age is its youngest file."""
    cap = SCAN_CAP if cap is None else cap
    def fail(exc):
        raise exc
    try:
        newest, seen = os.lstat(path).st_mtime, 0
        for top, dirs, files in os.walk(path, onerror=fail, followlinks=False):
            for name in dirs + files:
                seen += 1
                if seen > cap:
                    return None
                newest = max(newest, os.lstat(os.path.join(top, name)).st_mtime)
        return newest
    except (OSError, ValueError):
        return None


def classify(path, now, max_age, worktrees, handle_reader=None,
             dirty_reader=None):
    """Decide one entry's fate. Returns (verdict, detail).

    The order is deliberate: age first because it is the cheapest and most
    common keep, then handles, then uncommitted work. Every check that cannot
    answer returns a keep, so no single unreadable reading can cause a delete.
    """
    handle_reader = handle_reader or open_handles
    dirty_reader = dirty_reader or dirty_count
    newest = newest_mtime(path)
    if newest is None:
        return KEEP_UNREADABLE, "newest mtime unreadable, or more than %d entries" % SCAN_CAP
    age = now - newest
    if age < max_age:
        return KEEP_YOUNG, "%.1f h old" % (age / 3600.0)
    handles = handle_reader(path)
    if handles is None:
        return KEEP_UNREADABLE, "open handles unreadable"
    if handles > 0:
        return KEEP_HELD, "%d handle(s)" % handles
    if worktrees is not None and os.path.realpath(path) in worktrees:
        dirty = dirty_reader(path)
        if dirty is None:
            return KEEP_UNREADABLE, "git status unreadable"
        if dirty > 0:
            return KEEP_DIRTY, "%d uncommitted path(s)" % dirty
        return REMOVED_WORKTREE, "%.1f h old, 0 handles, clean" % (age / 3600.0)
    return REMOVED_DIR, "%.1f h old, 0 handles" % (age / 3600.0)


def prune(root=None, repo=None, max_age=None, now=None, dry_run=False,
          runner=None, handle_reader=None, dirty_reader=None):
    """Prune root's top level. Returns (rows, error); error is a string when
    the root itself could not be read, and then rows is empty and nothing was
    touched. Defaults resolve at CALL time, not at definition time, so a test
    that reassigns a module constant is actually honoured."""
    root = DEFAULT_ROOT if root is None else root
    max_age = DEFAULT_MAX_AGE if max_age is None else max_age
    runner = runner or _run
    if not isinstance(max_age, (int, float)) or isinstance(max_age, bool):
        raise ValueError("max_age must be a number of seconds")
    if max_age <= 0:
        raise ValueError("max_age must be positive")
    if now is None:
        now = time.time()
    elif not isinstance(now, (int, float)) or isinstance(now, bool):
        raise ValueError("now must be a number of seconds")
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        return [], "the scratch root could not be read: %s" % exc
    worktrees = registered_worktrees(repo, runner=runner) if repo else None
    rows = []
    for name in names:
        path = os.path.join(root, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        verdict, detail = classify(path, now, max_age, worktrees,
                                   handle_reader=handle_reader,
                                   dirty_reader=dirty_reader)
        if not dry_run and verdict in (REMOVED_WORKTREE, REMOVED_DIR):
            # A live agent may have written since the first look (attack R3 L3): look again immediately before
            # touching anything, for a worktree and a plain directory alike; clearing moves mtimes, so this comes first.
            verdict, detail = classify(path, time.time() if now is None else now, max_age, worktrees,
                                       handle_reader=handle_reader, dirty_reader=dirty_reader)
        if not dry_run and verdict == REMOVED_WORKTREE:
            clear_regenerable(path, runner)   # git worktree remove refuses untracked caches; never --force
            code, _ = runner(["git", "-C", repo, "worktree", "remove", path])
            if code != 0:
                verdict, detail = KEEP_UNREADABLE, "git worktree remove refused"
        elif not dry_run and verdict == REMOVED_DIR:
            shutil.rmtree(path, ignore_errors=True)
            if os.path.isdir(path):
                verdict, detail = KEEP_UNREADABLE, "removal did not take effect"
        rows.append((name, verdict, detail))
    if not dry_run and repo and worktrees is not None:
        runner(["git", "-C", repo, "worktree", "prune"])
    return rows, None


def main(argv):
    if "--selftest" in argv:
        return selftest()
    root, repo, hours = DEFAULT_ROOT, None, DEFAULT_MAX_AGE / 3600.0
    dry = "--dry-run" in argv
    for flag in ("--root", "--repo", "--max-age-hours"):
        if flag not in argv:
            continue
        i = argv.index(flag) + 1
        if i >= len(argv):
            print("usage: scratch_prune.py [--root DIR] [--repo DIR] "
                  "[--max-age-hours N] [--dry-run]", file=sys.stderr)
            return 2
        if flag == "--root":
            root = argv[i]
        elif flag == "--repo":
            repo = argv[i]
        else:
            try:
                hours = float(argv[i])
            except ValueError:
                print("NO-DATA: --max-age-hours must be a number",
                      file=sys.stderr)
                return 2
    if not hours > 0:
        print("NO-DATA: --max-age-hours must be positive", file=sys.stderr)
        return 2
    rows, err = prune(root=root, repo=repo, max_age=hours * 3600, dry_run=dry)
    if err:
        print("NO-DATA: %s" % err, file=sys.stderr)
        return 2
    removed = [r for r in rows if r[1].startswith("removed")]
    kept = [r for r in rows if r[1].startswith("keep")]
    for name, verdict, detail in removed:
        print("%-40s %s (%s)" % (name, verdict, detail))
    for name, verdict, detail in kept[:3]:
        print("%-40s %s (%s)" % (name, verdict, detail))
    print("scratch_prune: %d removed, %d kept, of %d entr%s%s"
          % (len(removed), len(kept), len(rows),
             "y" if len(rows) == 1 else "ies",
             " (dry run, nothing touched)" if dry else ""))
    return 0


def _verdict(fails):
    """The exit code for a selftest run: nonzero whenever any case failed.

    This exists as its own function because it is the one line a mutation can
    flip to turn a failing run into a reported pass, and a selftest cannot
    observe its own return value. Measured 2026-09-30: the generic mutation
    sweep flipped this to return 0 and all 22 cases still printed OK.
    """
    return 1 if fails else 0


def selftest():
    import tempfile
    fails = []

    def case(name, got, want):
        if got != want:
            fails.append("%s: got %r want %r" % (name, got, want))

    def reader(n):
        return lambda path, runner=None: n

    OLD = DEFAULT_MAX_AGE + 3600
    base = tempfile.mkdtemp(prefix="scratch-prune-selftest-")
    try:
        def entry(name, age):
            p = os.path.join(base, name)
            os.makedirs(p, exist_ok=True)
            stamp = time.time() - age
            os.utime(p, (stamp, stamp))
            return p

        now = time.time()
        # ONE CONDITION PER CASE: each fixture can only trip the guard it
        # names, so a guard cannot be deleted while another masks the loss.
        case("young kept",
             classify(entry("a", 10), now, DEFAULT_MAX_AGE, set(),
                      reader(0), reader(0))[0], KEEP_YOUNG)
        case("held kept",
             classify(entry("b", OLD), now, DEFAULT_MAX_AGE, set(),
                      reader(3), reader(0))[0], KEEP_HELD)
        case("unreadable handles kept",
             classify(entry("c", OLD), now, DEFAULT_MAX_AGE, set(),
                      reader(None), reader(0))[0], KEEP_UNREADABLE)
        p_dirty = entry("d", OLD)
        case("dirty worktree kept",
             classify(p_dirty, now, DEFAULT_MAX_AGE,
                      {os.path.realpath(p_dirty)}, reader(0), reader(2))[0],
             KEEP_DIRTY)
        case("unreadable git status kept",
             classify(p_dirty, now, DEFAULT_MAX_AGE,
                      {os.path.realpath(p_dirty)}, reader(0), reader(None))[0],
             KEEP_UNREADABLE)
        p_clean = entry("e", OLD)
        case("clean old worktree removed",
             classify(p_clean, now, DEFAULT_MAX_AGE,
                      {os.path.realpath(p_clean)}, reader(0), reader(0))[0],
             REMOVED_WORKTREE)
        # The defect this module exists to fix: a named lane, not run-*.
        case("named lane removed, not only run-*",
             classify(entry("attack9-A", OLD), now, DEFAULT_MAX_AGE, set(),
                      reader(0), reader(0))[0], REMOVED_DIR)
        case("run dir still removed",
             classify(entry("run-20260930-012130-90073", OLD), now,
                      DEFAULT_MAX_AGE, set(), reader(0), reader(0))[0],
             REMOVED_DIR)
        case("missing entry kept",
             classify(os.path.join(base, "no-such-entry"), now,
                      DEFAULT_MAX_AGE, set(), reader(0), reader(0))[0],
             KEEP_UNREADABLE)
        # An unreadable root is NO-DATA, never a silent clean pass.
        rows, err = prune(root=os.path.join(base, "absent"), max_age=60)
        case("unreadable root reports an error", err is None, False)
        case("unreadable root touches nothing", rows, [])
        entry("f", OLD)
        rows, err = prune(root=base, max_age=DEFAULT_MAX_AGE, dry_run=True,
                          handle_reader=reader(0), dirty_reader=reader(0))
        case("dry run removes nothing",
             os.path.isdir(os.path.join(base, "f")), True)
        case("dry run still reports rows", len(rows) > 0, True)
        for bad in (0, -1, "6h", True):
            try:
                prune(root=base, max_age=bad)
                fails.append("max_age %r accepted" % (bad,))
            except ValueError:   # sbe: allow-silent the selftest expects this refusal; an acceptance is recorded as a failure above
                pass
        for bad in ("yesterday", True):
            try:
                prune(root=base, max_age=60, now=bad)
                fails.append("now %r accepted" % (bad,))
            except ValueError:   # sbe: allow-silent the selftest expects this refusal; an acceptance is recorded as a failure above
                pass
        # A tool that could not run reads as unknown, never as clean.
        case("missing lsof reads as unknown",
             open_handles(base, runner=lambda c, cwd=None: (None, "")), None)
        case("missing git reads as unknown",
             registered_worktrees(base, runner=lambda c, cwd=None: (None, "")),
             None)
        case("git failure reads as unknown",
             registered_worktrees(base, runner=lambda c, cwd=None: (1, "")),
             None)
        # The exit code is itself a guard: a run with failures must never
        # report success. A mutation flipped this and every case still said OK.
        case("a failure exits nonzero", _verdict(["one failed case"]), 1)
        case("no failure exits zero", _verdict([]), 0)
        case("worktree paths are parsed",
             registered_worktrees(base, runner=lambda c, cwd=None: (
                 0, "worktree %s\nHEAD abc\n" % base)),
             {os.path.realpath(base)})
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if fails:
        for f in fails:
            print("FAIL %s" % f, file=sys.stderr)
        print("scratch_prune selftest: %d failure(s)" % len(fails),
              file=sys.stderr)
        return _verdict(fails)
    print("scratch_prune selftest: OK over 24 cases")
    return _verdict(fails)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
