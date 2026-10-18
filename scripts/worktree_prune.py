#!/usr/bin/env python3
"""C1.a: list, and on --apply remove, the worktrees under ~/brother-wt that nobody needs any more.

usage (repo root):
  python3 -B scripts/worktree_prune.py --dry      one line per worktree: PRUNE, or KEEP with the reason
  python3 -B scripts/worktree_prune.py --apply    the same list, then plain `git worktree remove` for each PRUNE
Exit 0 listed or removed, 1 when a removal failed, 2 on a bad command line, 3 when git cannot run.

A worktree is PRUNE only when every fact below was READ and agrees: it sits under the root, git status --porcelain is
empty, its HEAD is in a hub ref (git branch -r --contains HEAD), its newest mtime is older than the idle window, git
status --ignored --porcelain names nothing outside __pycache__ and *.pyc (git worktree remove deletes ignored files
without asking), and no live lease holds it (worktree_sentry's claim file and builder_claim's records). Removal never
passes --force, and apply asks refusal again right before each removal, so a builder that started in the tree after
--dry keeps it.

FAIL DIRECTION: a git call that fails, a file that cannot be read, a porcelain line that does not parse and a lease
that cannot be matched all KEEP the worktree, never prune it."""
import argparse
import math
import os
import re
import sys
import time

import cut_ready  # noqa: E402  (sibling module: its runner seam and its result readers, reused, never copied)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import builder_claim  # noqa: E402  (scripts/loop/builder_claim.py: the builder claim records and their reader)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.expanduser("~/brother-wt")
IDLE_S = 86400.0
GIT_TIMEOUT_S = 120
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_NO_GIT = 0, 1, 2, 3
# The same claim file, and the same seam, as scripts/worktree_sentry.py (MARK). That module runs git at import time,
# so its record is read here by its documented line format (pid=, at=, start=, root=) instead of being imported.
SENTRY_CLAIM_ENV = "BROTHER_WORKTREE_CLAIM"
SENTRY_CLAIM = "~/.claude/brother-or-dispatch-state/worktree.claim"
_PORCELAIN_KEY = re.compile(r"^(HEAD|branch|detached|bare|locked|prunable)( |$)")


def _need_text(value, what):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("%s must be a non-empty string, not %r" % (what, value))
    return value


def _need_abs(value, what):
    _need_text(value, what)
    if not os.path.isabs(value):
        raise ValueError("%s must be an absolute path, not %r" % (what, value))
    return value


def _need_finite(value, what, low=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a finite number, not %r" % (what, value))
    if low is not None and value < low:
        raise ValueError("%s must be at least %r, not %r" % (what, low, value))
    return float(value)


def _need_callable(value, what):
    if value is not None and not callable(value):
        raise ValueError("%s must be callable or None, not %r" % (what, value))
    return value


def _git(runner, argv, cwd):
    """(stdout, "") for a git call that exited 0, else (None, why). Never raises: a runner that raises is a failure."""
    result = cut_ready._invoke(runner, argv, cwd, timeout=GIT_TIMEOUT_S)
    if result is None:
        return None, "%s could not run" % " ".join(argv)
    code = cut_ready._code_of(result)
    if code != 0:
        return None, "%s failed (exit %s)" % (" ".join(argv), code)
    return cut_ready._text_of(result), ""


def _cache_only(rel):
    """True for an ignored path git worktree remove may delete: inside a __pycache__ folder, or one *.pyc file."""
    rel = rel.strip()
    if len(rel) >= 2 and rel.startswith('"') and rel.endswith('"'):
        rel = rel[1:-1]
    parts = [p for p in rel.split("/") if p]
    return "__pycache__" in parts or (not rel.endswith("/") and rel.endswith(".pyc"))


def _pid_live(pid):
    """Only a positive "no such process" is death; EPERM and every other answer read as alive (worktree_sentry._alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _same_tree(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def _sentry_lease(worktree, mark):
    """"" or why worktree_sentry's claim file holds this worktree. No file is no claim; an unreadable one holds.
    The holder's start time needs ps, which this module may not run, so a live pid is a live claim (unknown is alive)."""
    try:
        with open(mark, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        return "the worktree claim %s cannot be read: %s" % (mark, exc)
    fields = dict(line.strip().split("=", 1) for line in text.splitlines() if "=" in line)
    holder = fields.get("pid", "")
    if not holder:
        return ""
    if not re.fullmatch(r"[0-9]+", holder) or int(holder) <= 0:
        return "the worktree claim %s names no readable pid (%r)" % (mark, holder)
    if not _pid_live(int(holder)):
        return ""
    root = fields.get("root", "")
    if not root or root == "None":
        return "the worktree claim %s is held by live pid %s and names no tree" % (mark, holder)
    if _same_tree(root, worktree):
        return "claimed in worktree_sentry by live pid %s" % holder
    return ""


def _branch_of(worktree):
    """The short branch name this worktree's HEAD file names, or None (detached, or unreadable)."""
    dot_git = os.path.join(worktree, ".git")
    try:
        if os.path.isdir(dot_git):
            gitdir = dot_git
        else:
            with open(dot_git, encoding="utf-8") as fh:
                line = fh.read().strip()
            if not line.startswith("gitdir: "):
                return None
            gitdir = os.path.join(worktree, line[len("gitdir: "):].strip())
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):] or None
    return None


def _builder_lease(worktree, runs_dir):
    """"" or why a live builder_claim record holds this worktree. A record that cannot be read holds, as in
    builder_claim.claim_hold; a live claim that cannot be matched to this worktree's branch holds too."""
    if not os.path.exists(runs_dir):
        return ""
    try:
        names = sorted(os.listdir(runs_dir))
    except OSError as exc:
        return "the builder claims in %s cannot be listed: %s" % (runs_dir, exc)
    branch = _branch_of(worktree)
    for name in names:
        folder = os.path.join(runs_dir, name)
        if not builder_claim._FOLDER_RE.match(name) or not os.path.isdir(folder):
            continue
        claim_path = os.path.join(folder, builder_claim.CLAIM_FILE)
        if os.path.exists(os.path.join(folder, "STATUS")) or not os.path.isfile(claim_path):
            continue
        record, why = builder_claim._read_claim(claim_path)
        if record is None:
            return "the builder claim in %s cannot be read (%s)" % (folder, why)
        ok, why = builder_claim._pid_agrees(os.path.join(folder, "PID"), record["pid"])
        if not ok:
            return "the builder claim in %s cannot be read (%s)" % (folder, why)
        if not builder_claim._pid_alive(record["pid"]):
            continue
        claimed = record["branch"]
        if branch is None:
            return ("a live builder claim (%s, branch %s) cannot be matched to this worktree, whose branch cannot be read"
                    % (record["sub"], claimed))
        if claimed in (branch, "refs/heads/" + branch) or (os.path.isabs(claimed) and _same_tree(claimed, worktree)):
            return "claimed by builder %s for %s (pid %d)" % (record["builder"], record["sub"], record["pid"])
    return ""


def _default_leased(worktree):
    mark = os.environ.get(SENTRY_CLAIM_ENV) or os.path.expanduser(SENTRY_CLAIM)
    return _sentry_lease(worktree, mark) or _builder_lease(worktree, builder_claim.DEFAULT_RUNS)


def _lease_reason(leased, worktree):
    """The lease answer as a reason: a non-empty str or True holds; "" or False is free; anything else holds."""
    try:
        got = leased(worktree)
    except Exception as exc:
        return "the lease reading failed: %s" % (exc,)
    if got is True:
        return "a live lease holds it"
    if got is False or got == "":
        return ""
    if isinstance(got, str):
        return got
    return "the lease reading answered %r, not a reason" % (got,)


def refusal(worktree, runner=None, leased=None):
    """"" when this worktree may be removed, else the first reason it may not, by name."""
    _need_abs(worktree, "worktree")
    _need_callable(runner, "runner")
    _need_callable(leased, "leased")
    run = cut_ready.real_runner if runner is None else runner
    if not os.path.isdir(worktree):
        return "the worktree is not a folder"
    out, why = _git(run, ["git", "status", "--porcelain"], worktree)
    if out is None:
        return why
    dirty = [line for line in out.splitlines() if line.strip()]
    if dirty:
        return "dirty: git status --porcelain names %d changed path(s)" % len(dirty)
    out, why = _git(run, ["git", "branch", "-r", "--contains", "HEAD"], worktree)
    if out is None:
        return why
    if not out.strip():
        return "unpushed: HEAD is in no hub ref"
    out, why = _git(run, ["git", "status", "--ignored", "--porcelain"], worktree)
    if out is None:
        return why
    kept = [line[3:] for line in out.splitlines() if line.startswith("!! ") and not _cache_only(line[3:])]
    if kept:
        return "ignored: %d ignored path(s) outside __pycache__ and *.pyc, first %s" % (len(kept), kept[0])
    return _lease_reason(_default_leased if leased is None else leased, worktree)


def _parse(text):
    """[{"path", "bad", ...}] for each block of git worktree list --porcelain. A line that does not parse marks its
    block bad with the line named; a line before any worktree line is a block of its own with no path."""
    blocks, cur = [], None
    for line in text.splitlines():
        if not line.strip():
            cur = None
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "bad": "", "flags": set()}
            if not os.path.isabs(cur["path"]):
                cur["bad"] = "the porcelain line %r does not parse" % line
            blocks.append(cur)
            continue
        if cur is None:
            blocks.append({"path": None, "bad": "the porcelain line %r does not parse" % line, "flags": set()})
            continue
        match = _PORCELAIN_KEY.match(line)
        if match is None:
            cur["bad"] = cur["bad"] or "the porcelain line %r does not parse" % line
        else:
            cur["flags"].add(match.group(1))
    return blocks


def _newest_mtime(worktree):
    """The newest mtime of the tree and every path in it, links not followed, or None when any part cannot be read."""
    failed = []
    try:
        newest = os.lstat(worktree).st_mtime
        for top, dirs, files in os.walk(worktree, onerror=failed.append):
            for name in dirs + files:
                newest = max(newest, os.lstat(os.path.join(top, name)).st_mtime)
    except OSError:
        return None
    return None if failed else newest


def _judge(root, now, idle_s, runner, leased, repo):
    """(0, [(path or None, reason)]) for every worktree under root plus every unparsed block, or (3, why)."""
    run = cut_ready.real_runner if runner is None else runner
    out, why = _git(run, ["git", "worktree", "list", "--porcelain"], repo)
    if out is None:
        return EXIT_NO_GIT, why
    top = os.path.realpath(root)
    rows = []
    for index, block in enumerate(_parse(out)):
        path = block["path"]
        if path is None or not os.path.isabs(path):
            rows.append((path, block["bad"]))
            continue
        if not os.path.realpath(path).startswith(top + os.sep):
            continue                      # outside the root: not this tool's business, never listed
        if block["bad"]:
            rows.append((path, block["bad"]))
        elif index == 0 or "bare" in block["flags"]:
            rows.append((path, "the main worktree is never pruned"))
        elif "locked" in block["flags"]:
            rows.append((path, "git reports it locked"))
        elif "prunable" in block["flags"]:
            rows.append((path, "git reports it prunable: its folder is missing"))
        else:
            newest = _newest_mtime(path)
            if newest is None:
                rows.append((path, "its files cannot all be read for their mtime"))
            elif now - newest <= idle_s:
                rows.append((path, "active: newest mtime %.0f s ago, inside the %.0f s idle window" % (now - newest, idle_s)))
            else:
                rows.append((path, refusal(path, run, leased)))
    return EXIT_OK, sorted(rows, key=lambda row: (row[0] is None, row[0] or ""))


def candidates(root, now, idle_s=IDLE_S, runner=None, leased=None, repo=None):
    """The worktrees under root that are idle over idle_s and carry no refusal, sorted. Git that cannot list raises."""
    _need_abs(root, "root")
    now = _need_finite(now, "now")
    idle_s = _need_finite(idle_s, "idle_s", 0)
    _need_callable(runner, "runner")
    _need_callable(leased, "leased")
    repo = REPO if repo is None else _need_abs(repo, "repo")
    code, rows = _judge(root, now, idle_s, runner, leased, repo)
    if code != EXIT_OK:
        raise LookupError(rows)
    return sorted(path for path, reason in rows if path is not None and not reason)


def _remove(path, runner, leased, repo):
    """"" when the worktree was removed, else why not. Refusal is asked again first: a builder may have started."""
    why = refusal(path, runner, leased)
    if why:
        return why
    out, why = _git(cut_ready.real_runner if runner is None else runner, ["git", "worktree", "remove", path], repo)
    return why


def apply(paths, runner=None, leased=None, repo=None):
    """Remove each worktree with plain git worktree remove (never --force) and answer the count of failures."""
    if not isinstance(paths, list):
        raise ValueError("paths must be a list of absolute paths, not %r" % (paths,))
    for path in paths:
        _need_abs(path, "each path")
    _need_callable(runner, "runner")
    _need_callable(leased, "leased")
    repo = REPO if repo is None else _need_abs(repo, "repo")
    return sum(1 for path in paths if _remove(path, runner, leased, repo))


def main(argv=None, runner=None, leased=None, now=None, repo=None):
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SystemExit(EXIT_USAGE)
    _need_callable(runner, "runner")
    _need_callable(leased, "leased")
    if now is not None:
        _need_finite(now, "now")
    if repo is not None:
        _need_abs(repo, "repo")
    parser = argparse.ArgumentParser(prog="worktree_prune.py", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry", action="store_true", help="list only")
    mode.add_argument("--apply", action="store_true", help="list, then remove each PRUNE worktree")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="the folder whose worktrees are judged")
    parser.add_argument("--repo", default=repo or REPO, help="a checkout of the repository the worktrees belong to")
    parser.add_argument("--idle-hours", type=float, default=IDLE_S / 3600.0)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.root) or not os.path.isabs(args.repo):
        parser.error("--root and --repo must be absolute paths")
    if not math.isfinite(args.idle_hours) or args.idle_hours < 0:
        parser.error("--idle-hours must be a finite number, at least 0")
    code, rows = _judge(args.root, time.time() if now is None else float(now), args.idle_hours * 3600.0,
                        runner, leased, args.repo)
    if code != EXIT_OK:
        print("NO-DATA git cannot run: %s" % rows)
        return code
    failed = 0
    for path, reason in rows:
        if reason:
            print("KEEP %s: %s" % (path, reason))
            continue
        print("PRUNE %s" % path)
        if args.apply:
            why = _remove(path, runner, leased, args.repo)
            if why:
                failed += 1
                print("FAILED %s: %s" % (path, why))
            else:
                print("REMOVED %s" % path)
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
