#!/usr/bin/env python3
"""ORCH-23: wire the existing reclaim tool up operationally, and cover the
17.9 GB of the measured 23.59 GB that sits outside its scope.

WHY THIS EXISTS. scripts/reclaim_worktrees.py only ever reports; nothing on
this estate calls it with --apply. Wiring it up blindly would repeat the
2026-09-10 incident this repository already suffered once: a worktree that
was clean and pushed was still removed while a process ran inside it, and
separately, a clean and pushed worktree can still be a consumer's dependency
even when nothing is running inside it (a script imports it, a plist names
its path, another worktree's files symlink into it). Whether a worktree's
OWN work is preserved is reclaim_worktrees.py's question; whether something
else DEPENDS on it is a different question, and scripts/orchestrator_dependents.py
already answers that one. This module composes the two: it never deletes a
worktree itself, it only decides, from a completed dependents scan, which
candidates reclaim_worktrees.py's own --apply may act on.

Evidence and transcript growth, and clones no worktree list ever tracks,
are the 17.9 GB neither existing tool covers. Nothing here reimplements
worktree removal for them: they are plain files (or plain directories with
their own object store), so this module's own prune functions are the first
and only implementation, gated the same way (report by default, apply
refuses anything whose owner or liveness cannot be established).

CONTROLLING THE RECURRING GENERATION. Evidence and transcript files keep
being written by the estate's normal operation; nothing here stops that.
What this module offers is an idempotent command safe to run on a schedule
(the founder's own cron or launchd entry, never added by this module, since
changing a system schedule is a settings change outside this unit's scope):
each run reports, or under --apply prunes, back to the same retention and
byte budget, so the generation is kept in check by the schedule that calls
this rather than by anything unbounded.

Report-only is the default on every surface. --apply requires:
  worktrees   a completed orchestrator_dependents.py scan that exited 0
              (CLEAR) for that exact path; a REFUSED, a NO-DATA, or a scan
              that could not even run all block, the same as each other.
  evidence    a liveness check that completed and found no owner; a check
              that could not run blocks, same as a check that found one.
  clones      report-only always; inventory is not a deletion surface here.

Standard library only. No em dash or en dash anywhere in this file.

  python3 scripts/reclaim_wiring.py worktrees --root A --root B
  python3 scripts/reclaim_wiring.py worktrees --root A --apply
  python3 scripts/reclaim_wiring.py evidence --path P --retention-days 30 --max-bytes N
  python3 scripts/reclaim_wiring.py evidence --path P --retention-days 30 --max-bytes N --apply
  python3 scripts/reclaim_wiring.py clones --root A --root B --repo R
"""
import argparse
import os
import stat
import subprocess
import sys
import time


class ScanIncomplete(Exception):
    """A boundary call (subprocess, stat, walk) did not complete. Nothing
    may be concluded from a scan that raises this: not clean, not clear,
    not empty. Every caller in this module treats it as a refusal."""


def _default_run(cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScanIncomplete("cannot run %s: %s" % (cmd, exc))


def _here(script_dir=None):
    return script_dir or os.path.dirname(os.path.abspath(__file__))


def _raise_walk(err):
    raise ScanIncomplete("cannot walk %s: %s" % (getattr(err, "filename", "?"), err))


# --------------------------------------------------------------------------
# A: worktree wiring. reclaim_worktrees.py stays the only thing that ever
# calls `git worktree remove`; this only decides its --skip list.
# --------------------------------------------------------------------------

def worktree_candidates(roots, run=_default_run, script_dir=None):
    """Paths reclaim_worktrees.py's own report (no --apply) calls WOULD RELEASE.

    Parses its stdout rather than reimplementing its HELD/WOULD RELEASE
    logic. A non 0/1 exit (its own contract: 1 only means a --apply run had
    a failure, which cannot happen here since --apply is never passed) is
    treated as an incomplete listing, never as an empty one.
    """
    script = os.path.join(_here(script_dir), "reclaim_worktrees.py")
    cmd = [sys.executable, script, "--roots"] + list(roots)
    proc = run(cmd)
    if proc.returncode not in (0, 1):
        raise ScanIncomplete("reclaim_worktrees.py exited %d: %s"
                             % (proc.returncode, proc.stderr.strip()))
    return [line[len("WOULD RELEASE"):].strip()
            for line in proc.stdout.splitlines() if line.startswith("WOULD RELEASE")]


def dependents_clear(path, roots, repo=None, run=_default_run, script_dir=None):
    """(clear, detail) from a real orchestrator_dependents.py run.

    clear is True only on that script's own exit 0 (CLEAR). Its exit 1
    (REFUSED, a consumer found), exit 2 (NO-DATA, the scan itself could not
    complete) and a run that failed to even start all return False: unknown
    blocks exactly like a found consumer does, never like a clear one.

    `repo` asks orchestrator_dependents.py to also flag a worktree
    registered INSIDE `path` (a different, nested one). Never pass the repo
    that already lists `path` itself as one of ITS worktrees: that repo's
    own entry for `path` always reads real-path-equal to `path`, and the
    underlying script's own worktree check counts an exact match as a hit,
    so every worktree-reclaim candidate would self-flag as a consumer of
    itself. wire_worktrees() below never passes repo for exactly this
    reason; pass it here only for a path that is not itself one of that
    repo's own registered worktrees.
    """
    script = os.path.join(_here(script_dir), "orchestrator_dependents.py")
    cmd = [sys.executable, script, "--path", path, "--verb", "worktree remove"]
    for root in roots:
        cmd += ["--root", root]
    if repo:
        cmd += ["--repo", repo]
    try:
        proc = run(cmd)
    except ScanIncomplete as exc:
        return False, "dependents scan did not run: %s" % exc
    return proc.returncode == 0, proc.stdout.strip()


def apply_worktree_reclaim(roots, skip, run=_default_run, script_dir=None):
    """Call the existing tool's OWN --apply, skipping every non clear path.

    Never called by anything in this module except wire_worktrees(), and
    only after every path not in `skip` already read clear from
    dependents_clear(). This never removes a worktree by any means of its
    own; it is the one place --apply is passed to the existing tool.
    """
    script = os.path.join(_here(script_dir), "reclaim_worktrees.py")
    cmd = [sys.executable, script, "--roots"] + list(roots) + ["--apply"]
    if skip:
        cmd += ["--skip"] + list(skip)
    return run(cmd)


def wire_worktrees(roots, apply=False, run=_default_run, script_dir=None):
    """Report, or apply, the existing worktree reclaim tool with a dependents gate.

    Never passes a repo to dependents_clear(): every candidate here IS a
    registered worktree of one of `roots`, so checking it against its own
    repo's worktree list would always self-match (see dependents_clear's
    docstring) and REFUSE every candidate for a reason that is not real.
    The dependents scan still covers the consumer kinds that matter for the
    2026-09-10 incident this module exists to not repeat: a file naming the
    path, a Python import, a symlink into it.

    Returns {"candidates", "eligible", "blocked": [(path, reason)], "applied"}.
    "applied" stays None on a report-only call and on an apply call with
    zero eligible candidates (never calls --apply for nothing to release).
    """
    candidates = worktree_candidates(roots, run=run, script_dir=script_dir)
    eligible, blocked = [], []
    for path in candidates:
        clear, detail = dependents_clear(path, roots, run=run, script_dir=script_dir)
        if clear:
            eligible.append(path)
        else:
            blocked.append((path, detail or "dependents scan did not clear this path"))
    result = {"candidates": candidates, "eligible": eligible, "blocked": blocked, "applied": None}
    if apply and eligible:
        skip = [p for p, _ in blocked]
        proc = apply_worktree_reclaim(roots, skip, run=run, script_dir=script_dir)
        result["applied"] = {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    return result


# --------------------------------------------------------------------------
# B: evidence and transcript retention. Nothing existing covers this; it is
# plain file deletion, gated by liveness the same fail-closed way lsof
# already gates reclaim_worktrees.py's own in_use() check.
# --------------------------------------------------------------------------

def scan_prunable(path, retention_days, max_bytes, now=None):
    """Files under `path` that retention or a size budget would remove.

    Two independent reasons, either is enough, a file can carry both:
    older than retention_days, or (walking newest first) the running total
    of files already kept has passed max_bytes by the time this file is
    reached, so the newest files are always kept first. A file that cannot
    be stat'd raises ScanIncomplete rather than being silently treated as
    clean: a walk that failed partway through is not the same as one with
    nothing left to prune. Only regular files are ever a target; a symlink
    or a directory entry is neither counted nor listed.
    """
    now = now if now is not None else time.time()
    cutoff = now - retention_days * 86400.0
    if not os.path.isdir(path):
        raise ScanIncomplete("evidence path does not exist: %s" % path)
    entries = []
    for dirpath, _dirnames, filenames in os.walk(path, onerror=_raise_walk):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                st = os.lstat(fp)
            except OSError as exc:
                raise ScanIncomplete("cannot stat %s: %s" % (fp, exc))
            if not stat.S_ISREG(st.st_mode):
                continue
            entries.append((fp, st.st_size, st.st_mtime))
    entries.sort(key=lambda e: e[2], reverse=True)  # newest first
    running = 0
    prunable = []
    for fp, size, mtime in entries:
        reasons = []
        if mtime < cutoff:
            reasons.append("older than %g days" % retention_days)
        if running > max_bytes:
            reasons.append("past the %d byte budget" % max_bytes)
        if reasons:
            prunable.append({"path": fp, "size": size, "mtime": mtime, "reasons": reasons})
        running += size
    return prunable


def file_in_use(path, run=_default_run):
    """True when a live process holds `path` open, or the check could not
    run. Mirrors reclaim_worktrees.py's own in_use(): lsof exit 1 with no
    output means free and is trusted, exit 0 with output means in use,
    anything else including a failed subprocess call reads as in use, the
    fail closed direction, because deleting a file a process still has
    open is not recoverable by re-running anything.
    """
    try:
        proc = run(["lsof", "--", path])
    except ScanIncomplete:
        return True
    if proc.returncode not in (0, 1):
        return True
    return bool(proc.stdout.strip())


def apply_prune(entries, in_use=file_in_use):
    """Delete each entry's file, refusing any whose owner is not known clear.

    `in_use` decides ownership; its default is the real lsof check above.
    A file it reports in use, or that raises while being checked or
    removed, is blocked, never deleted: unknown blocks. Only a regular
    file still at the path scan_prunable() found it at is ever removed.
    """
    deleted, blocked = [], []
    for entry in entries:
        path = entry["path"]
        try:
            st = os.lstat(path)
        except OSError as exc:
            blocked.append((path, "cannot stat: %s" % exc))
            continue
        if not stat.S_ISREG(st.st_mode):
            blocked.append((path, "no longer a regular file"))
            continue
        try:
            busy = in_use(path)
        except Exception as exc:  # noqa: BLE001 - a failed liveness check blocks, never deletes
            blocked.append((path, "liveness check failed: %s" % exc))
            continue
        if busy:
            blocked.append((path, "owner unknown or file in use"))
            continue
        try:
            os.remove(path)
        except OSError as exc:
            blocked.append((path, "remove failed: %s" % exc))
            continue
        deleted.append(path)
    return deleted, blocked


# --------------------------------------------------------------------------
# C: unregistered clone inventory. Report only; nothing here deletes a
# clone, since a full clone's disk cost is real but so is not knowing yet
# why it exists.
# --------------------------------------------------------------------------

def registered_worktrees(repo, run=_default_run):
    """Every path `git -C repo worktree list` already knows, realpath'd."""
    proc = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        raise ScanIncomplete("git worktree list on %s exited %d: %s"
                             % (repo, proc.returncode, proc.stderr.strip()))
    return {os.path.realpath(line[len("worktree "):])
            for line in proc.stdout.splitlines() if line.startswith("worktree ")}


def _dir_size(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=_raise_walk):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                total += os.lstat(fp).st_size
            except OSError as exc:
                raise ScanIncomplete("cannot stat %s: %s" % (fp, exc))
    return total


def unregistered_clones(scan_roots, known_repos, run=_default_run):
    """Directories holding a full clone (a .git SUBDIRECTORY, its own object
    store) that no known repository's worktree list already accounts for.

    A .git that is a FILE is a worktree's gitdir pointer, not a clone by
    itself, and the walk never descends into either kind: a clone's own
    tree is never searched for a nested clone, and a worktree's tree is
    already counted at its own linked path by the tool this composes with.
    A missing scan root, or a git command that fails, raises ScanIncomplete
    rather than silently reporting fewer clones than exist.
    """
    registered = set()
    for repo in known_repos:
        registered |= registered_worktrees(repo, run=run)
    found = []
    for root in scan_roots:
        real_root = os.path.realpath(os.path.expanduser(root))
        if not os.path.isdir(real_root):
            raise ScanIncomplete("scan root does not exist: %s" % root)
        for dirpath, dirnames, _filenames in os.walk(real_root, onerror=_raise_walk):
            gitpath = os.path.join(dirpath, ".git")
            if os.path.isdir(gitpath):
                real = os.path.realpath(dirpath)
                if real not in registered:
                    found.append({"path": dirpath, "bytes": _dir_size(dirpath)})
                dirnames[:] = []  # never descend into a clone's own tree
            elif os.path.isfile(gitpath):
                dirnames[:] = []  # a worktree pointer, counted at its own linked path
    return found


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_worktrees(args):
    result = wire_worktrees(args.root, apply=args.apply)
    print("%d candidate(s), %d eligible, %d blocked"
          % (len(result["candidates"]), len(result["eligible"]), len(result["blocked"])))
    for path in result["eligible"]:
        print("  ELIGIBLE  %s" % path)
    for path, reason in result["blocked"]:
        print("  BLOCKED   %s  (%s)" % (path, reason))
    if result["applied"] is not None:
        print(result["applied"]["stdout"])
        if result["applied"]["exit"] != 0:
            print("apply exited %d: %s" % (result["applied"]["exit"], result["applied"]["stderr"]))
            return 1
    return 0


def _cmd_evidence(args):
    prunable = scan_prunable(args.path, args.retention_days, args.max_bytes)
    total = sum(e["size"] for e in prunable)
    print("%d file(s) prunable under %s, %d bytes"
          % (len(prunable), args.path, total))
    for e in prunable:
        print("  %s  %d bytes  (%s)" % (e["path"], e["size"], ", ".join(e["reasons"])))
    if args.apply:
        deleted, blocked = apply_prune(prunable)
        print("deleted %d, blocked %d" % (len(deleted), len(blocked)))
        for path, reason in blocked:
            print("  BLOCKED   %s  (%s)" % (path, reason))
    return 0


def _cmd_clones(args):
    found = unregistered_clones(args.root, args.repo)
    total = sum(c["bytes"] for c in found)
    print("%d unregistered clone(s), %d bytes" % (len(found), total))
    for c in found:
        print("  %s  %d bytes" % (c["path"], c["bytes"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    wt = sub.add_parser("worktrees", help="wire the existing worktree reclaim tool")
    wt.add_argument("--root", action="append", required=True, dest="root")
    wt.add_argument("--apply", action="store_true")
    wt.set_defaults(func=_cmd_worktrees)

    ev = sub.add_parser("evidence", help="prune evidence and transcript growth")
    ev.add_argument("--path", required=True)
    ev.add_argument("--retention-days", type=float, required=True)
    ev.add_argument("--max-bytes", type=int, required=True)
    ev.add_argument("--apply", action="store_true")
    ev.set_defaults(func=_cmd_evidence)

    cl = sub.add_parser("clones", help="inventory clones no worktree list tracks")
    cl.add_argument("--root", action="append", required=True, dest="root")
    cl.add_argument("--repo", action="append", required=True, dest="repo")
    cl.set_defaults(func=_cmd_clones)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ScanIncomplete as exc:
        print("reclaim_wiring: NO-DATA, %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
