#!/usr/bin/env python3
"""MG1.e: after each merge, prove reachability from main, never the state field.

`is_reachable` answers `git merge-base --is-ancestor <sha> <ref>`: exit 0 is True, exit 1 is False, anything else
(another exit, a runner that cannot run) is None, which is NO-DATA and never assumed. `prove_merged` fetches main of
the remote first, and only main (a failed fetch makes every row NO-DATA), then proves each pinned sha reachable from <remote>/main and
compares the tree of the merge commit on main (the first commit of main that has the pinned sha as an ancestor) with
the gated tree of the entry. It never reads `state_of(pr)` to decide PROVEN: the state only names STRANDED (GitHub
says MERGED, the sha is not on main: merged into a base other than main).

Row status: PROVEN, STRANDED, NOT-MERGED, NO-DATA, TREE-DRIFT. STRANDED, NO-DATA and TREE-DRIFT stop the batch: the
rows after them are NO-DATA (not examined).

STDLIB ONLY, Python 3.9 floor. A git runner is injectable: runner(argv, cwd=None) returns an object with
`returncode` and `stdout`.
"""
from __future__ import annotations

import re
import subprocess
import sys

SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
STATUSES = ("PROVEN", "STRANDED", "NOT-MERGED", "NO-DATA", "TREE-DRIFT")
USAGE = "usage: merge_reach.py check <clone> <remote> <pr> <sha> <tree> <state>"


class MergeReachError(ValueError):
    """Every deliberate refusal of this module."""


def _default_runner(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")


def _str(value, what):
    if isinstance(value, bool) or not isinstance(value, str) or not value or "\x00" in value:
        raise MergeReachError("%s must be a non empty string" % what)
    return value


def _word(value, what):
    value = _str(value, what)
    if value.startswith("-") or any(c.isspace() for c in value):
        raise MergeReachError("%s is not a plain name: %r" % (what, value[:60]))
    return value


def _sha(value, what):
    if isinstance(value, bool) or not isinstance(value, str) or not SHA_RE.match(value):
        raise MergeReachError("%s must be a 40 hex sha" % what)
    return value


def _runner(runner):
    if runner is None:
        return _default_runner
    if not callable(runner):
        raise MergeReachError("runner must be callable")
    return runner


def _git(runner, argv, clone):
    """(returncode, stdout) of one git call, or (None, '') when the runner cannot run it."""
    try:
        done = runner(argv, cwd=clone)
        rc, out = done.returncode, done.stdout
    except Exception:  # a runner that fails is NO-DATA, never a verdict
        return None, ""
    if isinstance(rc, bool) or not isinstance(rc, int) or not isinstance(out, str):
        return None, ""
    return rc, out


def is_reachable(clone, sha, ref="refs/remotes/hub/main", runner=None):
    """True when sha is an ancestor of ref (exit 0), False on exit 1, None for anything else."""
    _str(clone, "clone")
    _sha(sha, "sha")
    _word(ref, "ref")
    rc, _out = _git(_runner(runner), ["git", "merge-base", "--is-ancestor", sha, ref], clone)
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def _merge_commit(runner, clone, sha, ref):
    """The first commit of ref (first parent line) that has sha as an ancestor, or None."""
    rc, out = _git(runner, ["git", "rev-list", "--first-parent", ref], clone)
    if rc != 0:
        return None
    if sha in out.split():
        return sha
    rc, out = _git(runner, ["git", "rev-list", "--first-parent", "--ancestry-path", sha + ".." + ref], clone)
    lines = out.split()
    if rc != 0 or not lines or not SHA_RE.match(lines[-1]):
        return None
    return lines[-1]


def _tree_of(runner, clone, commit):
    rc, out = _git(runner, ["git", "show", "-s", "--format=%T", commit], clone)
    out = out.strip()
    if rc != 0 or not SHA_RE.match(out):
        return None
    return out


def _row(entry, status, merge_commit, detail):
    return {"pr": entry["pr"], "sha": entry["sha"], "status": status, "merge_commit": merge_commit, "detail": detail}


def prove_merged(clone, entries, state_of, remote="hub", runner=None):
    """One row per entry: {pr, sha, status, merge_commit, detail}. Entries are dicts with pr, sha and tree (the gated
    tree)."""
    _str(clone, "clone")
    if not isinstance(entries, list):
        raise MergeReachError("entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise MergeReachError("every entry must be a dict")
        pr = entry.get("pr")
        if isinstance(pr, bool) or not isinstance(pr, (int, str)) or pr == "":
            raise MergeReachError("entry pr must be a number or string")
        _sha(entry.get("sha"), "entry sha")
        _sha(entry.get("tree"), "entry tree")
    if not callable(state_of):
        raise MergeReachError("state_of must be callable")
    remote = _word(remote, "remote")
    run = _runner(runner)
    if not entries:
        return []
    rows = []
    ref = "refs/remotes/%s/main" % remote   # never the short name: a tag refs/tags/<remote>/main outranks it
    # F6, review 2026-10-06: ONLY main, into its own tracking ref, and no tag. A full `git fetch <remote>` also updates
    # every other branch and tag, so one stale ref of no interest here (a branch that became a folder of branches, a
    # moved tag) failed the fetch AFTER the merge had landed and the batch stopped on a merge that was fine. The
    # refspec is spelled, so main is read fresh whatever this clone is configured to fetch. Any failure is still NO-DATA.
    rc, _out = _git(run, ["git", "fetch", "--quiet", "--no-tags", remote, "+refs/heads/main:" + ref], clone)
    if rc != 0:
        return [_row(e, "NO-DATA", None, "fetch of %s failed" % remote) for e in entries]
    for entry in entries:
        reach = is_reachable(clone, entry["sha"], ref, run)
        if reach is None:
            rows.append(_row(entry, "NO-DATA", None, "git could not answer reachability"))
        elif reach is False:
            try:
                state = state_of(entry["pr"])
            except Exception:  # an unreadable state is NO-DATA
                state = None
            if not isinstance(state, str):
                rows.append(_row(entry, "NO-DATA", None, "state unreadable and sha not on %s" % ref))
            elif state == "MERGED":
                rows.append(_row(entry, "STRANDED", None, "state MERGED but sha is not on %s" % ref))
            else:
                rows.append(_row(entry, "NOT-MERGED", None, "state %s, sha not on %s" % (state[:40], ref)))
        else:
            commit = _merge_commit(run, clone, entry["sha"], ref)
            tree = _tree_of(run, clone, commit) if commit else None
            if tree is None:
                rows.append(_row(entry, "NO-DATA", commit, "the merge commit or its tree cannot be read"))
            elif tree != entry["tree"]:
                rows.append(_row(entry, "TREE-DRIFT", commit,
                                 "main holds %s, the gate passed %s" % (tree, entry["tree"])))
            else:
                rows.append(_row(entry, "PROVEN", commit, "reachable from %s, tree equals the gated tree" % ref))
        if rows[-1]["status"] in ("STRANDED", "NO-DATA", "TREE-DRIFT"):
            for rest in entries[len(rows):]:
                rows.append(_row(rest, "NO-DATA", None, "not examined, the batch stopped at #%s" % entry["pr"]))
            break
    return rows


def format_report(rows):
    """One line per row, then a summary line."""
    if not isinstance(rows, list):
        raise MergeReachError("rows must be a list")
    lines = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            raise MergeReachError("every row must be a dict with a known status")
        commit = row.get("merge_commit")
        lines.append("%s #%s sha=%s merge-commit=%s %s" % (
            row["status"], row.get("pr"), row.get("sha"), commit if isinstance(commit, str) else "none",
            row.get("detail", "")))
    proven = sum(1 for r in rows if r["status"] == "PROVEN")
    lines.append("PROVEN: %d of %d" % (proven, len(rows)))
    return "\n".join(lines)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SystemExit(2)
    if len(argv) != 7 or argv[0] != "check":
        print(USAGE)
        return 2
    _cmd, clone, remote, pr, sha, tree, state = argv
    try:
        rows = prove_merged(clone, [{"pr": pr, "sha": sha, "tree": tree}], lambda _pr: state, remote)
    except MergeReachError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    print(format_report(rows))
    status = rows[0]["status"]
    if status == "PROVEN":
        return 0
    return 2 if status == "NO-DATA" else 1


if __name__ == "__main__":
    sys.exit(main())
