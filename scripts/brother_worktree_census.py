#!/usr/bin/env python3
"""Read-only worktree and disk census: one row per `git worktree`, per repo.

Phase 7 (repository/worktree hygiene) task P7-1. REPORTING ONLY: no
deletion, move, or modification of anything it inspects, and no purge
logic -- that is the separate P7-4 task. This estate lost a live worktree
once already to a cleanup script that ignored a running session (vault
failure note: a-post-tag-worktree-cleanup-that-drops-the-activity-guard...);
the fix here is simpler than a guard: this script never calls anything but
read-only git/du commands.

Per worktree, reports:
  path, branch, head (sha), dirty (bool), unpushed_commits (bool or the
  string "NO-DATA" when no upstream is configured -- never guessed),
  last_touch_utc (mtime of the most recently modified TRACKED file, not
  the worktree directory's own mtime: a freshly checked-out worktree's
  directory mtime is checkout time, per this estate's own recorded
  finding: a-fresh-worktrees-mtime-is-checkout-time), disk_usage (`du -sh`).

Output is JSON Lines (one JSON object per line), matching the task's own
"one object per worktree row" wording literally and letting a caller grep
or parse rows one at a time without buffering a whole array.

  python3 scripts/brother_worktree_census.py                    # census cwd's repo, to stdout
  python3 scripts/brother_worktree_census.py --repo A --repo B  # census multiple repos
  python3 scripts/brother_worktree_census.py --out census.jsonl # write instead of print
"""
import argparse
import datetime
import json
import os
import subprocess
import sys


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout, p.stderr


def parse_worktrees(repo_root):
    """[{"path", "head", "branch" (None if detached), "bare"}] from porcelain."""
    code, out, _ = run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    if code != 0:
        return []
    entries, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            b = line[len("branch "):]
            cur["branch"] = b[len("refs/heads/"):] if b.startswith("refs/heads/") else b
        elif line == "detached":
            cur["branch"] = None
        elif line == "bare":
            cur["bare"] = True
    if cur:
        entries.append(cur)
    return entries


def is_dirty(path):
    _, out, _ = run(["git", "-C", path, "status", "--porcelain"])
    return bool(out.strip())


def unpushed_commits(path):
    """True/False, or "NO-DATA" when no upstream is configured. Never guessed."""
    code, upstream, _ = run(["git", "-C", path, "rev-parse", "--abbrev-ref",
                              "--symbolic-full-name", "@{u}"])
    if code != 0 or not upstream.strip():
        return "NO-DATA"
    code, count, _ = run(["git", "-C", path, "rev-list", "--count",
                          "%s..HEAD" % upstream.strip()])
    if code != 0:
        return "NO-DATA"
    try:
        return int(count.strip()) > 0
    except ValueError:
        return "NO-DATA"


def last_touch_utc(path):
    """ISO timestamp of the newest TRACKED file's mtime, or None if untracked/empty."""
    code, out, _ = run(["git", "-C", path, "ls-files"])
    if code != 0 or not out.strip():
        return None
    latest = None
    for rel in out.splitlines():
        try:
            mtime = os.path.getmtime(os.path.join(path, rel))
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    if latest is None:
        return None
    return datetime.datetime.fromtimestamp(latest, tz=datetime.timezone.utc).isoformat()


def disk_usage(path):
    code, out, _ = run(["du", "-sh", path])
    if code != 0 or not out.strip():
        return "NO-DATA"
    return out.strip().split(None, 1)[0]


def census(repo_root):
    rows = []
    for entry in parse_worktrees(repo_root):
        path = entry.get("path")
        row = {"repo": repo_root, "path": path, "branch": entry.get("branch"),
               "head": entry.get("head")}
        if entry.get("bare") or not path or not os.path.isdir(path):
            row["note"] = "bare or missing worktree, not inspected"
        else:
            row["dirty"] = is_dirty(path)
            row["unpushed_commits"] = unpushed_commits(path)
            row["last_touch_utc"] = last_touch_utc(path)
            row["disk_usage"] = disk_usage(path)
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", action="append", dest="repos",
                     help="repository root to census (repeatable); default: cwd's repo")
    ap.add_argument("--out", help="write JSON Lines here instead of stdout")
    args = ap.parse_args()

    repos = args.repos or [os.getcwd()]
    lines = []
    for repo in repos:
        real = os.path.realpath(repo)
        for row in census(real):
            lines.append(json.dumps(row))
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
