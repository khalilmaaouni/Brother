#!/usr/bin/env python3
"""lane_resume: turn a dead lane's worktree into a patch a fresh lane can apply.

Row M9 of the 2026-09-07 reflection. Four lanes died on the account's
session limit with uncommitted work sitting only in their worktrees, and
recovery needed a hand-built diff each time. This tool reads a worktree
WITHOUT touching it -- branch, last commit, upstream, `git status`, `git
diff` -- and writes everything a fresh lane needs into a separate output
directory: the tracked diff as a patch, every untracked file copied out
by path, and a report naming the exact command to resume on a new branch.

Exit codes: 0 written; 2 NO-DATA, WORKTREE_PATH is not a git worktree (the
directory is missing, or git does not recognise it), named and never a
pass.

Python 3, standard library only. No network.
"""
import argparse
import os
import shutil
import subprocess
import sys


def _git(path, *args, timeout=30):
    try:
        return subprocess.run(["git", "-C", path] + list(args),
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        class _Fail:
            returncode = 1
            stdout = ""
            stderr = str(exc)
        return _Fail()


def is_worktree(path):
    if not os.path.isdir(path):
        return False
    proc = _git(path, "rev-parse", "--is-inside-work-tree")
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def branch_of(path):
    proc = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def last_commit(path):
    """(sha, subject), or (None, None) on a worktree with no commits yet."""
    proc = _git(path, "log", "-1", "--format=%H%x1f%s")
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, None
    sha, _, subject = proc.stdout.strip().partition("\x1f")
    return sha, subject


def upstream_of(path):
    proc = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def status_lines(path):
    proc = _git(path, "status", "--porcelain")
    return [l for l in proc.stdout.splitlines() if l.strip()] if proc.returncode == 0 else []


def untracked_paths(path):
    proc = _git(path, "ls-files", "--others", "--exclude-standard")
    return [l for l in proc.stdout.splitlines() if l.strip()] if proc.returncode == 0 else []


def diff_text(path):
    proc = _git(path, "diff")
    return proc.stdout if proc.returncode == 0 else ""


def write_report(out_dir, path, branch, sha, subject, upstream,
                  modified_count, untracked_count, diff_path, untracked_root):
    resume_branch = "%s-resumed" % (branch or "lane")
    base = sha or "HEAD"
    apply_cmd = (
        "git checkout -b %s %s\n"
        "git apply \"%s\"\n"
        "cp -r \"%s\"/. .   # restore the files listed in untracked.txt\n"
        % (resume_branch, base, diff_path, untracked_root)
    )
    body = (
        "# Lane resume report\n\n"
        "- source worktree: %s\n"
        "- branch: %s\n"
        "- last commit: %s (%s)\n"
        "- upstream: %s\n"
        "- modified/staged tracked file(s): %d\n"
        "- untracked file(s): %d\n\n"
        "## Command a fresh lane runs to apply this\n\n"
        "```\n%s```\n"
        % (path, branch or "(detached, no branch)", base,
           subject or "(no commits)", upstream or "(none)",
           modified_count, untracked_count, apply_cmd)
    )
    report_path = os.path.join(out_dir, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return report_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("worktree", help="path to the (possibly dead) lane's worktree")
    ap.add_argument("--out", required=True, help="directory to write the resume kit into")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    path = os.path.abspath(args.worktree)
    if not is_worktree(path):
        print("NO-DATA: %s is not a git worktree" % path, file=sys.stderr)
        return 2

    branch = branch_of(path)
    sha, subject = last_commit(path)
    upstream = upstream_of(path)
    status = status_lines(path)
    untracked = untracked_paths(path)
    diff = diff_text(path)
    modified_count = len([l for l in status if not l.startswith("??")])

    os.makedirs(args.out, exist_ok=True)

    diff_path = os.path.join(args.out, "diff.patch")
    with open(diff_path, "w", encoding="utf-8") as fh:
        fh.write(diff)

    untracked_list_path = os.path.join(args.out, "untracked.txt")
    with open(untracked_list_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(untracked) + ("\n" if untracked else ""))

    untracked_root = os.path.join(args.out, "untracked")
    for rel in untracked:
        src = os.path.join(path, rel)
        if not os.path.isfile(src):
            continue  # an ignored directory git reports as one line: skipped, not guessed at
        dst = os.path.join(untracked_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    report_path = write_report(args.out, path, branch, sha, subject, upstream,
                               modified_count, len(untracked), diff_path, untracked_root)

    print("REPORT: %s" % report_path)
    print("diff: %s (%d line(s))" % (diff_path, diff.count("\n")))
    print("untracked: %d file(s) under %s" % (len(untracked), untracked_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
