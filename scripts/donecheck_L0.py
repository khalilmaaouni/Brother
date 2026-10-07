#!/usr/bin/env python3
"""L0 done check: the unification epic delivered what it promised.

L0's objective was PR 803 (refactor/brother-unified-1.1) merged into main and the unify worktree cleaned. The PR
merged on 2026-09-20 with head cef532fa2; the old done check compared that vanished worktree's HEAD with a moving
main and could never pass, and the closer only runs python3 files, so the check is this file.

PASS (exit 0): the PR head is an ancestor of the hub's main and no worktree named brother-unify-1.1 is registered.
FAIL (exit 1): either fact is false. NO-DATA (exit 2): git could not answer (no hub/main ref, not a repository).
usage: python3 scripts/donecheck_L0.py [--head SHA] [--main REF]"""
import argparse
import subprocess
import sys

PR_HEAD = "cef532fa24cb3214f61cac1f5aa2e9ec70dcb198"
WORKTREE = "worktrees/brother-unify-1.1"


def git(*args):
    try:
        return subprocess.run(["git"] + list(args), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--head", default=PR_HEAD)
    ap.add_argument("--main", default="hub/main")
    a = ap.parse_args(argv)
    ref = git("rev-parse", "--verify", "--quiet", a.main)
    head = git("cat-file", "-e", a.head + "^{commit}")
    listing = git("worktree", "list", "--porcelain")
    if ref is None or ref.returncode != 0 or head is None or head.returncode != 0 or listing is None or listing.returncode != 0:
        print("NO-DATA: git could not resolve %s, the PR head, or the worktree list" % a.main)
        return 2
    anc = git("merge-base", "--is-ancestor", a.head, a.main)
    if anc is None or anc.returncode not in (0, 1):
        print("NO-DATA: git merge-base could not answer")
        return 2
    lingering = [l for l in listing.stdout.splitlines() if l.startswith("worktree ") and l.rstrip("/").endswith(WORKTREE)]
    problems = []
    if anc.returncode != 0:
        problems.append("the PR head %s is not an ancestor of %s" % (a.head[:9], a.main))
    if lingering:
        problems.append("the unify worktree is still registered: %s" % lingering[0][9:])
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print("PASS: PR 803's head %s is on %s and the unify worktree is gone" % (a.head[:9], a.main))
    return 0


if __name__ == "__main__":
    sys.exit(main())
