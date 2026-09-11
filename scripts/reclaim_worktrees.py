#!/usr/bin/env python3
"""Release worktrees whose work is already safe on the remote.

Dry run by default. A worktree is a candidate ONLY when all three hold:
  1. it has no uncommitted files,
  2. its HEAD commit is contained in at least one remote branch, so removing
     the directory loses no commit,
  3. it is not the checkout this script was invoked from.

Everything else is reported and left alone. Removal goes through
`git worktree remove`, never rm -rf: a hand-deleted directory leaves a stale
registration that makes the next `git worktree add` fail into the main checkout.

  python3 scripts/reclaim_worktrees.py                  # report candidates
  python3 scripts/reclaim_worktrees.py --apply          # release them
"""
import argparse
import os
import subprocess
import sys

DEFAULT_ROOTS = [os.path.expanduser("~/Brother"), os.path.expanduser("~/brother-hub")]


def git(args):
    p = subprocess.run(["git"] + args, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def in_use(path):
    """True when any live process has its working directory inside `path`.

    lsof is the only portable-enough way to ask this on macOS. Its exit 1
    means "nobody is here" and is trusted; any OTHER failure (lsof missing,
    a timeout, an unexpected code) is treated as IN USE, never as free,
    because deleting a directory out from under a running process is not
    recoverable by re-running anything. Callers check the path exists first.
    """
    try:
        p = subprocess.run(["lsof", "-a", "-d", "cwd", "--", path],
                           capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return True                       # cannot tell, so do not touch it
    if p.returncode not in (0, 1):        # 1 is lsof's ordinary "no match"
        return True
    return bool(p.stdout.strip())


def worktrees(root):
    code, out, _ = git(["-C", root, "worktree", "list", "--porcelain"])
    if code != 0:
        return []
    return [l[len("worktree "):] for l in out.splitlines() if l.startswith("worktree ")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--skip", nargs="*", default=[],
                    help="paths to leave alone even when they qualify "
                         "(a gate or a build running inside one)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    roots = [os.path.realpath(r) for r in args.roots]
    skip = {os.path.realpath(os.path.expanduser(x)) for x in args.skip}
    seen, held, freed, failed = set(), 0, 0, 0

    for root in roots:
        for wt in worktrees(root):
            real = os.path.realpath(wt)
            if real in seen or not os.path.isdir(real):
                continue
            seen.add(real)
            if real in roots:
                continue                       # never the main checkout itself
            if real in skip:
                print(f"HELD    in use        {real}")
                held += 1
                continue
            _, status, _ = git(["-C", real, "status", "--porcelain"])
            if status:
                print(f"HELD    uncommitted   {real}")
                held += 1
                continue
            # IN USE BEATS CLEAN AND PUSHED. On 2026-09-10 this script removed
            # two worktrees that were clean and pushed and also had a gate
            # running inside them; one session's next cd failed silently and its
            # cherry-picks landed in a different repository. git's own lock is
            # checked by `worktree remove`, but a process merely running with its
            # working directory inside an unlocked worktree is invisible to git.
            if in_use(real):
                print(f"HELD    a process is inside {real}")
                held += 1
                continue
            code, remotes, _ = git(["-C", real, "branch", "-r", "--contains", "HEAD"])
            if code != 0 or not remotes.strip():
                print(f"HELD    unpushed HEAD {real}")
                held += 1
                continue
            if not args.apply:
                print(f"WOULD RELEASE          {real}")
                freed += 1
                continue
            code, _, err = git(["-C", root, "worktree", "remove", real])
            if code != 0:
                print(f"FAILED  {real}: {err}")
                failed += 1
            else:
                print(f"RELEASED               {real}")
                freed += 1
    for root in roots:
        if args.apply:
            git(["-C", root, "worktree", "prune"])

    verb = "released" if args.apply else "would release"
    print(f"\n{freed} {verb}, {held} held (work not yet safe), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
