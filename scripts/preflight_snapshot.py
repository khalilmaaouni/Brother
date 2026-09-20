#!/usr/bin/env python3
"""preflight_snapshot: MD-1 of the MULTIDAY-SESSIONS epic (2026-09-19).

Tags the current HEAD of one or more repos/worktrees before an unattended or
overnight stretch, so a bad autonomous change has a named, real revert point.
Named as the one gap the field itself has not solved either (an open
Claude Code GitHub issue, #79103, cited in the epic's own intake artifact).

WHAT THIS COVERS: committed history only. A lightweight tag on HEAD is a
revert point for commits made during the stretch (`git reset --hard <tag>`);
it is NOT a snapshot of uncommitted or untracked changes present when this
runs -- those are a different, unsolved problem (checkpointing that covers
bash-command side effects is the exact gap the cited GitHub issue names as
still open industry-wide). Say this plainly rather than implying more
coverage than a git tag actually gives.

Never touches the working tree: read-only against it, one `git tag` per repo.
"""
import argparse
import subprocess
import sys
import time


def snapshot(repo, label=None):
    """Tags repo's current HEAD as preflight/<label-or-timestamp>. Returns
    (tag_name, head_sha) on success, raises RuntimeError with git's own
    stderr on failure (e.g. repo path is not a git repo, or the tag name
    already exists)."""
    stamp = label or time.strftime("%Y%m%d-%H%M%S")
    tag = "preflight/%s" % stamp
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode != 0:
        raise RuntimeError("not a git repo or no commits yet: %s (%s)"
                           % (repo, head.stderr.strip()))
    # -a -m always, not a bare `git tag`: this machine's global git config
    # sets tag.forceSignAnnotated, so a lightweight tag attempt fails with
    # "fatal: no tag message?" -- found by this script's own first test run,
    # 2026-09-19. An annotated tag also carries an audit message, which is
    # strictly more useful for a preflight snapshot than a bare ref.
    tagged = subprocess.run(
        ["git", "-C", repo, "tag", "-a", tag, "-m",
         "brother preflight_snapshot before an unattended stretch", "HEAD"],
        capture_output=True, text=True)
    if tagged.returncode != 0:
        raise RuntimeError("tag failed for %s: %s" % (repo, tagged.stderr.strip()))
    return tag, head.stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repos", nargs="+", help="one or more repo/worktree paths")
    ap.add_argument("--label", help="tag suffix (default: a timestamp)")
    a = ap.parse_args(argv)
    label = a.label or time.strftime("%Y%m%d-%H%M%S")
    ok = True
    for repo in a.repos:
        try:
            tag, sha = snapshot(repo, label)
            print("preflight-snapshot: %s -> %s (%s)" % (repo, tag, sha[:12]))
        except RuntimeError as exc:
            print("preflight-snapshot: FAILED for %s: %s" % (repo, exc), file=sys.stderr)
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
