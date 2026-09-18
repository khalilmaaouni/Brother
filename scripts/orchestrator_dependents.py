#!/usr/bin/env python3
"""ORCH-36: before a destructive verb, name what depends on the thing.

THE GAP THIS CLOSES, found independently by both adversaries and stated
sharply: OWNERSHIP LEASES ANSWER WHO MAY ACT; THEY DO NOT ANSWER WHAT
DEPENDS ON THE THING BEING REMOVED. A lease stops a second writer and says
nothing about the first writer deleting. The reclaim tool checks whether a
worktree's WORK is preserved and whether it looks occupied, which is not a
dependency check: a clean, pushed, unoccupied tree can still hold files
another checkout imports or a scheduled job runs, so removing it preserves
every commit and breaks the consumer. Fifteen recorded incidents, explicitly
uncontrolled.

WHAT COUNTS AS A CONSUMER, each found by reading real files, never inferred:
  path        another file names this absolute path (a script, a plist, a
              settings file, a hook, a cron line)
  import      a Python file imports a module this path provides
  symlink     a symlink outside the target points into it
  worktree    git has a worktree registered under it

THE FAIL DIRECTION IS THE WHOLE POINT. A destructive verb is refused when
consumers are found AND when the scan could not complete: "I could not
enumerate" is never "nothing depends on it". Exit 0 means a completed scan
found nothing.

  0  CLEAR    scan completed, no consumer found
  1  REFUSED  consumers found, each named
  2  NO-DATA  the scan could not complete: refuse the verb

This answers what depends on the thing. It does NOT decide whether the work
inside is preserved (reclaim_worktrees.py already does that) and it never
deletes anything itself.

Usage:
  python3 scripts/orchestrator_dependents.py --path P [--root R ...] [--verb rm]
"""
import argparse
import os
import re
import sys

#: Text files worth reading. A binary is skipped and counted, never silently
#: treated as clean.
TEXT_SUFFIXES = (".py", ".sh", ".json", ".plist", ".md", ".txt", ".yml", ".yaml",
                 ".toml", ".cfg", ".conf", ".service", ".jsonl", "")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "DerivedData"}
MAX_BYTES = 2_000_000
#: The verbs the standing law names; passed through to the message only.
DESTRUCTIVE_VERBS = ("rm", "git clean", "reset --hard", "stash drop",
                     "worktree remove", "add -A")


class ScanIncomplete(Exception):
    """The scan could not be completed, so nothing may be concluded."""


def _is_text(path):
    return path.endswith(TEXT_SUFFIXES) or "." not in os.path.basename(path)


def _module_names(target):
    """Python module names this target provides, so an `import x` counts."""
    names = set()
    if os.path.isfile(target) and target.endswith(".py"):
        names.add(os.path.basename(target)[:-3])
    elif os.path.isdir(target):
        try:
            for entry in os.listdir(target):
                if entry.endswith(".py") and entry != "__init__.py":
                    names.add(entry[:-3])
        except OSError as exc:
            raise ScanIncomplete("cannot list %s: %s" % (target, exc))
    return {n for n in names if n and not n.startswith("test_")}


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield os.path.join(dirpath, name)


def _raise(err):
    raise ScanIncomplete("cannot walk %s: %s" % (getattr(err, "filename", "?"), err))


def find_consumers(target, roots, worktrees=None):
    """[(kind, path, detail)] naming everything that depends on target.

    Raises ScanIncomplete when any root cannot be read, because a partial
    sweep must not read as a clean one.
    """
    given = os.path.abspath(os.path.expanduser(target))
    target = os.path.realpath(given)
    # A consumer names the path IT knows, which on this machine is often the
    # symlinked spelling (/tmp/x) rather than the canonical one (/private/tmp/x).
    # Matching only the resolved form misses those, so both are searched.
    names = {target, given}
    found, modules = [], _module_names(target)
    import_res = [(m, re.compile(r"^\s*(?:import|from)\s+%s\b" % re.escape(m), re.M))
                  for m in sorted(modules)]
    for root in roots:
        root = os.path.realpath(os.path.expanduser(root))
        if not os.path.exists(root):
            raise ScanIncomplete("root does not exist: %s" % root)
        for path in _walk(root):
            real = os.path.realpath(path)
            if os.path.islink(path) and (real == target or real.startswith(target + os.sep)):
                found.append(("symlink", path, "points into the target"))
                continue
            if real == target or real.startswith(target + os.sep):
                continue  # inside the thing being removed: not a consumer
            if not _is_text(path):
                continue
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="strict") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, IsADirectoryError):
                continue  # binary or not a file: nothing to read, not a hit
            except OSError as exc:
                raise ScanIncomplete("cannot read %s: %s" % (path, exc))
            hit = next((n for n in sorted(names, key=len, reverse=True) if n in text), None)
            if hit:
                found.append(("path", path, "names the absolute path%s"
                              % ("" if hit == target else " (as %s)" % hit)))
            for module, rx in import_res:
                if rx.search(text):
                    found.append(("import", path, "imports %s" % module))
    for tree in (worktrees or []):
        real = os.path.realpath(os.path.expanduser(tree))
        if real == target or real.startswith(target + os.sep):
            found.append(("worktree", tree, "git has a worktree registered here"))
    return found


def git_worktrees(repo, run=None):
    """Registered worktree paths, or ScanIncomplete when git cannot answer."""
    import subprocess
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=60))
    try:
        proc = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    except (OSError, Exception) as exc:  # noqa: BLE001 - any failure is NO-DATA
        raise ScanIncomplete("git worktree list failed: %s" % exc)
    if proc.returncode != 0:
        raise ScanIncomplete("git worktree list exited %s" % proc.returncode)
    return [line.split(" ", 1)[1].strip()
            for line in proc.stdout.splitlines() if line.startswith("worktree ")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", required=True, help="what is about to be destroyed")
    ap.add_argument("--root", action="append", default=[], dest="roots",
                    help="a tree to search for consumers (repeatable)")
    ap.add_argument("--repo", help="repository whose worktrees also count")
    ap.add_argument("--verb", default="a destructive verb")
    a = ap.parse_args(argv)
    if not a.roots:
        print("dependents: NO-DATA, no root given to search; refusing %s" % a.verb)
        return 2
    try:
        trees = git_worktrees(a.repo) if a.repo else []
        found = find_consumers(a.path, a.roots, worktrees=trees)
    except ScanIncomplete as exc:
        print("dependents: NO-DATA, the scan could not complete (%s). Refusing %s: "
              "not being able to enumerate consumers is never the same as there "
              "being none." % (exc, a.verb))
        return 2
    if found:
        print("dependents: REFUSED, %d consumer(s) depend on %s:" % (len(found), a.path))
        for kind, path, detail in found[:50]:
            print("  %-8s %s  (%s)" % (kind, path, detail))
        if len(found) > 50:
            print("  ... and %d more" % (len(found) - 50))
        return 1
    print("dependents: CLEAR, scan completed over %d root(s), no consumer of %s found"
          % (len(a.roots), a.path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
