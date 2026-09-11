#!/usr/bin/env python3
"""Preserve uncommitted work in every git worktree by pushing it to an archive ref.

Dry run by default. Nothing is deleted, ever: this script only commits and pushes.
Reclaiming disk is a separate, deliberate act after this reports every tree PUSHED.

  python3 scripts/preserve_wip.py                  # report what would happen
  python3 scripts/preserve_wip.py --apply          # commit and push archive branches
  python3 scripts/preserve_wip.py --roots ~/Brother ~/brother-hub
"""
import argparse
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pre_push_gate as PPG  # noqa: E402

DEFAULT_ROOTS = [os.path.expanduser("~/Brother"), os.path.expanduser("~/brother-hub")]


PUBLIC_EXPORT = "khalilmaaouni/Brother"


def git(args, cwd=None):
    # stdin is DEVNULL on purpose: a pre-push hook that prompts would otherwise
    # inherit this process's stdin and read as hung across 100+ worktrees.
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def repo_path(url):
    """owner/repo, lowercased, from either an SSH or an HTTPS remote URL.

    A substring test is wrong here: "khalilmaaouni/brother" is a prefix of
    "khalilmaaouni/brother-hub", so it refuses the private hub as if it were
    the public export target.
    """
    tail = url.rstrip("/").rsplit(":", 1)[-1]
    parts = [x for x in tail.replace(".git", "").split("/") if x]
    return "/".join(parts[-2:]).lower()


def private_remote(wt):
    """The remote that accepts a push, never the public export target.

    ~/Brother calls the private hub "hub" and the PUBLIC export repo "origin";
    ~/brother-hub calls the private hub "origin". Picking by name alone would
    push private work to a public repository, so the URL decides.
    """
    code, out, _ = git(["-C", wt, "remote"])
    if code != 0:
        return None, "no remote"
    for name in ("hub", "origin"):
        if name not in out.split():
            continue
        _, url, _ = git(["-C", wt, "remote", "get-url", "--push", name])
        if repo_path(url) == PUBLIC_EXPORT.lower():
            continue
        if not url or url.startswith("no_push"):
            continue
        return name, url
    return None, "no private remote (only the public export target)"


def worktrees(root):
    code, out, _ = git(["-C", root, "worktree", "list", "--porcelain"])
    if code != 0:
        return []
    return [line[len("worktree "):] for line in out.splitlines() if line.startswith("worktree ")]


def slug(path, branch):
    base = branch if branch and branch != "HEAD" else os.path.basename(path)
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in base]
    return "".join(keep).strip("-").lower()[:60] or "unnamed"


def secret_hit_files(real):
    """File names (never values) whose STAGED diff matches a SECRET_SHAPE.
    Reuses pre_push_gate.py's own patterns and public-example allowlist by
    name, never a second, drifting copy of them. Scanned per file so a hit
    can be reported by name without ever printing the matched value."""
    _, names, _ = git(["-C", real, "diff", "--cached", "--name-only"])
    hits = []
    for name in names.splitlines():
        if not name:
            continue
        _, diff_text, _ = git(["-C", real, "diff", "--cached", "--", name])
        text = PPG.strip_public_examples(diff_text)
        if any(p.search(text) for p in PPG.SECRET_SHAPES):
            hits.append(name)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    stamp = datetime.date.today().isoformat()
    seen, dirty, pushed, failed = set(), 0, 0, 0

    for root in args.roots:
        if not os.path.isdir(os.path.join(root, ".git")) and not os.path.exists(os.path.join(root, ".git")):
            print(f"NO-DATA: {root} is not a git checkout")
            continue
        for wt in worktrees(root):
            real = os.path.realpath(wt)
            if real in seen or not os.path.isdir(real):
                continue
            seen.add(real)
            code, status, _ = git(["-C", real, "status", "--porcelain"])
            if code != 0 or not status:
                continue
            dirty += 1
            _, branch, _ = git(["-C", real, "rev-parse", "--abbrev-ref", "HEAD"])
            ref = f"archive/wip-{stamp}-{slug(real, branch)}"
            n = len(status.splitlines())
            remote, why = private_remote(real)
            if remote is None:
                print(f"REFUSED     {real}: {why}")
                failed += 1
                continue
            if not args.apply:
                print(f"WOULD PUSH  {n:>3} file(s)  {branch:<40} -> {remote}/{ref}")
                continue
            git(["-C", real, "add", "-A"])
            hit_files = secret_hit_files(real)
            if hit_files:
                print(f"REFUSED     {real}: secret-shaped value staged in "
                      f"{', '.join(hit_files)}")
                git(["-C", real, "reset"])  # unstage; leave the worktree as found
                failed += 1
                continue
            steps = [
                ["-C", real, "commit", "-m", f"wip: archive {branch} at {stamp}"],
                ["-C", real, "push", remote, f"HEAD:refs/heads/{ref}"],
            ]
            for step in steps:
                code, out, err = git(step)
                if code != 0:
                    print(f"FAILED      {real}: git {' '.join(step[2:4])}: {err or out}")
                    failed += 1
                    break
            else:
                print(f"PUSHED      {n:>3} file(s)  {branch:<40} -> {remote}/{ref}")
                pushed += 1

    if dirty == 0:
        print("NO-DATA: no worktree carried uncommitted work; nothing to preserve")
        return 0
    verb = "pushed" if args.apply else "would push"
    print(f"\n{dirty} dirty worktree(s), {pushed if args.apply else dirty} {verb}, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
