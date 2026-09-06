#!/usr/bin/env python3
"""bm_reconcile_worktrees.py: read-only worktree data-loss reconciliation.

WHY THIS EXISTS
  Agent worktrees accumulate. This machine currently carries roughly 45 of
  them across three repositories, many holding uncommitted TRACKED
  modifications. A routine cleanup (`git worktree prune`, or deleting the
  directories by hand) discards whatever exists ONLY in that worktree's
  working copy. Most of that is safe to discard (the same bytes already sit
  on a reachable branch somewhere) and some of it is not (real, unlanded
  work), and today nothing tells the two apart. A measurement run minutes
  before this file was written found BrotherModeUp itself at LANDED 27 /
  UNLANDED 4, and BrotherSBE holding genuinely unlanded edits to
  tools/sbe_gate.py and src/brothersbe/contracts.py. So both blind answers
  are wrong: "delete it all" destroys real work, "keep it all" never cleans
  anything. This tool is the missing classifier.

THE THREE VERDICTS, AND WHY UNKNOWN NEVER BECOMES LANDED
  LANDED    these exact bytes already exist at this path in some object
            reachable from a ref, so discarding this worktree's copy loses
            nothing.
  UNLANDED  no reachable object carries these bytes at this path. Discarding
            the worktree destroys the only copy.
  UNKNOWN   the file could not be read or hashed (permission error, a
            symlink loop, disappeared between listing and hashing).
  A wrong LANDED is the unrecoverable direction: it tells a human it is safe
  to delete something that is not. This estate's own law is that an
  unexamined state is never reported clean (the NO-DATA convention every
  other tool in tools/ follows), so UNKNOWN is folded into "unsafe to
  remove" everywhere a caller has to choose a side, never into "safe".

THE ALGORITHM, AND WHY IT IS SHAPED THIS WAY
  A first attempt ran one `git rev-parse <ref>:<path>` per uncommitted file
  per ref. On BrotherSBE (many refs, many files) that did NOT finish in two
  minutes. The working algorithm instead builds the reachable set ONCE per
  repository with `git rev-list --objects --all`, which prints `<sha>
  <path>` lines for every blob reachable from any ref, into a set of
  (sha, path) pairs. Every uncommitted tracked file is then `git
  hash-object`'d once and tested for membership in that one set. This
  finished well under two minutes on the same repository where the
  per-file-per-ref approach did not.

  A `git hash-object` on the worktree's own working copy is what git itself
  would compute for that file if it were staged right now, so this is
  exactly the identity check git's own object store uses: the same path AND
  the same content hash, not merely the same content anywhere.

WHAT THIS TOOL NEVER DOES
  It is read-only with respect to every repository it inspects: no writes,
  no prunes, no `git worktree remove`, no `git gc`, no staging, no commits.
  The `check` subcommand REFUSES removal and names the unsafe files; it
  never removes anything itself. Removing a worktree is a human's decision
  (or another tool's job) once this one has named what would be lost.

Python 3.9, standard library only. No em or en dashes anywhere in this file
or its output.

Usage:
  python3 tools/bm_reconcile_worktrees.py [sweep] [REPO_PATH] [--json]
  python3 tools/bm_reconcile_worktrees.py check WORKTREE_PATH [--json]

`sweep` (default verb) classifies every uncommitted tracked file in every
linked worktree of the repository at REPO_PATH (default: the current
directory). Exits 0 when every uncommitted file it found is LANDED, 1 when
at least one is UNLANDED or UNKNOWN, 2 on NO-DATA (could not determine).

`check WORKTREE_PATH` answers exactly the removal question for ONE
worktree: exits 0 and prints "safe to remove" when every uncommitted
tracked file in it is LANDED (or there are none), exits 1 and names every
UNLANDED or UNKNOWN file when it is not, exits 2 on NO-DATA.
"""

import json
import os
import subprocess
import sys

NULL_SHA = "0" * 40

LANDED = "LANDED"
UNLANDED = "UNLANDED"
UNKNOWN = "UNKNOWN"


class GitError(Exception):
    """Raised by _git() on any nonzero exit or unreadable output. Callers
    turn this into an explicit NO-DATA line, never a silent empty result."""


def _git(repo_dir, args, timeout=90):
    """Run one read-only git subprocess rooted at repo_dir. Returns stdout
    as text. Raises GitError on nonzero exit, a missing git binary, or a
    timeout, so a caller can never mistake "git failed" for "git found
    nothing"."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir] + list(args),
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise GitError("git is not on PATH")
    except subprocess.TimeoutExpired:
        raise GitError("git %s timed out after %ds" % (" ".join(args), timeout))
    if proc.returncode != 0:
        raise GitError(
            "git %s exited %d: %s"
            % (" ".join(args), proc.returncode, (proc.stderr or "").strip()))
    return proc.stdout


def list_worktrees(repo_dir):
    """[(path, is_main)] for every worktree `git worktree list --porcelain`
    reports, in the order git prints them (main checkout first, per git's
    own documented porcelain format)."""
    out = _git(repo_dir, ["worktree", "list", "--porcelain"])
    worktrees = []
    path = None
    is_first = True
    for line in out.splitlines():
        if line.startswith("worktree "):
            if path is not None:
                worktrees.append((path, is_first))
                is_first = False
            path = line[len("worktree "):].strip()
    if path is not None:
        worktrees.append((path, is_first))
    return worktrees


def uncommitted_tracked_files(worktree_dir):
    """Paths (worktree-relative) of every TRACKED file `git status
    --porcelain --untracked-files=no` reports as modified, added, deleted,
    or renamed in worktree_dir. A rename line ("old -> new") contributes
    only the new path, per this tool's own spec: the new path is where the
    uncommitted bytes actually live now."""
    out = _git(worktree_dir, ["status", "--porcelain", "--untracked-files=no"])
    paths = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # Porcelain v1: two status chars, one space, then the path (or
        # "old -> new" for a rename/copy).
        rest = line[3:] if len(line) > 3 else ""
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        rest = rest.strip()
        if rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
            rest = rest[1:-1]
        if rest:
            paths.append(rest)
    return paths


def build_reachable_set(repo_dir):
    """set of (sha, path) for every blob reachable from any ref, built
    with exactly one `git rev-list --objects --all` call per repository.
    This is the set-once step that made the classifier fast enough (see
    module docstring): membership testing against this set replaces one
    `git rev-parse <ref>:<path>` per file per ref."""
    out = _git(repo_dir, ["rev-list", "--objects", "--all"], timeout=180)
    reachable = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        reachable.add((sha, path))
    return reachable


def hash_worktree_file(worktree_dir, rel_path):
    """The sha git would assign this file's CURRENT working-copy bytes, or
    None if the file cannot be read or hashed (deleted, permission denied,
    a broken symlink, or any other read failure). None is the caller's cue
    to classify UNKNOWN, never LANDED."""
    abs_path = os.path.join(worktree_dir, rel_path)
    if not os.path.exists(abs_path):
        # A path git reports as modified but that no longer exists on disk
        # (deleted since the status snapshot, or between listing and
        # hashing here) cannot be honestly hashed as "this file's bytes";
        # report UNKNOWN rather than guessing.
        return None
    try:
        out = _git(worktree_dir, ["hash-object", "--", rel_path])
    except GitError:  # sbe: allow-silent documented contract, this function's own docstring says None is the caller's cue to classify UNKNOWN
        return None
    sha = out.strip()
    if len(sha) not in (40, 64) or not sha:
        return None
    return sha


def classify_file(worktree_dir, rel_path, reachable):
    """(verdict, sha_or_None). LANDED iff the file hashed cleanly AND
    (sha, rel_path) is in the reachable set; UNLANDED iff it hashed cleanly
    but that pair is absent; UNKNOWN iff it could not be hashed at all.
    UNKNOWN is never folded into LANDED here or anywhere downstream."""
    sha = hash_worktree_file(worktree_dir, rel_path)
    if sha is None:
        return UNKNOWN, None
    if (sha, rel_path) in reachable:
        return LANDED, sha
    return UNLANDED, sha


def classify_worktree(worktree_dir, reachable):
    """[{"path", "verdict", "sha"}] for every uncommitted tracked file in
    one worktree, using an already-built reachable set."""
    results = []
    for rel_path in uncommitted_tracked_files(worktree_dir):
        verdict, sha = classify_file(worktree_dir, rel_path, reachable)
        results.append({"path": rel_path, "verdict": verdict, "sha": sha})
    return results


def sweep(repo_dir):
    """{"worktrees": [{"path", "is_main", "files": [...]}]} for the whole
    repository at repo_dir. The reachable set is built exactly once and
    reused across every linked worktree (they all share the same object
    store), which is the whole point of the set-once algorithm."""
    worktrees = list_worktrees(repo_dir)
    reachable = build_reachable_set(repo_dir)
    result = {"worktrees": []}
    for path, is_main in worktrees:
        if is_main:
            continue
        files = classify_worktree(path, reachable)
        result["worktrees"].append(
            {"path": path, "is_main": is_main, "files": files})
    return result


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _parse_argv(argv):
    """(verb, kv, error). error set means print it and exit 2 (a plain
    usage failure, not a NO-DATA verdict), mirroring tools/bm_stall.py's
    own split between usage errors and NO-DATA."""
    args = list(argv)
    verb = "sweep"
    if args and args[0] in ("sweep", "check"):
        verb = args.pop(0)
    kv = {"target": None, "json": False}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            kv["json"] = True
            i += 1
        elif not arg.startswith("--") and kv["target"] is None:
            kv["target"] = arg
            i += 1
        else:
            return None, None, ("bm_reconcile_worktrees: unknown argument: %s"
                                % arg)
    if verb not in ("sweep", "check"):
        return None, None, ("bm_reconcile_worktrees: unknown verb: %s" % verb)
    if verb == "check" and not kv["target"]:
        return None, None, "bm_reconcile_worktrees: check requires WORKTREE_PATH"
    return verb, kv, None


def _render_sweep_text(data):
    lines = []
    total = 0
    by_verdict = {LANDED: 0, UNLANDED: 0, UNKNOWN: 0}
    for wt in data["worktrees"]:
        for f in wt["files"]:
            total += 1
            by_verdict[f["verdict"]] += 1
    if total == 0:
        lines.append("bm_reconcile_worktrees sweep: 0 uncommitted tracked "
                     "file(s) across %d linked worktree(s). Nothing to "
                     "classify." % len(data["worktrees"]))
        return "\n".join(lines)
    lines.append("bm_reconcile_worktrees sweep: %d uncommitted tracked "
                 "file(s) across %d linked worktree(s) (LANDED=%d "
                 "UNLANDED=%d UNKNOWN=%d)"
                 % (total, len(data["worktrees"]), by_verdict[LANDED],
                    by_verdict[UNLANDED], by_verdict[UNKNOWN]))
    for wt in data["worktrees"]:
        if not wt["files"]:
            continue
        lines.append("")
        lines.append("worktree %s" % wt["path"])
        for f in wt["files"]:
            lines.append("  [%s] %s" % (f["verdict"], f["path"]))
    return "\n".join(lines)


def cmd_sweep(kv):
    repo_dir = kv["target"] or os.getcwd()
    if not os.path.isdir(repo_dir):
        _out("NO-DATA: no such directory: %s" % repo_dir)
        return 2
    try:
        data = sweep(repo_dir)
    except GitError as exc:
        _out("NO-DATA: %s" % exc)
        return 2
    if kv["json"]:
        _out(json.dumps(data, indent=2, sort_keys=True))
    else:
        _out(_render_sweep_text(data))
    unsafe = any(f["verdict"] != LANDED
                for wt in data["worktrees"] for f in wt["files"])
    return 1 if unsafe else 0


def cmd_check(kv):
    worktree_dir = kv["target"]
    if not os.path.isdir(worktree_dir):
        _out("NO-DATA: no such directory: %s" % worktree_dir)
        return 2
    try:
        # A linked worktree shares its main checkout's object store and refs
        # (that is what "linked" means to git), so `git rev-list --objects
        # --all` run FROM the worktree itself sees the exact same reachable
        # set the main checkout would report; no separate resolution to the
        # main checkout's own path is needed.
        reachable = build_reachable_set(worktree_dir)
        files = classify_worktree(worktree_dir, reachable)
    except GitError as exc:
        _out("NO-DATA: %s" % exc)
        return 2
    unsafe = [f for f in files if f["verdict"] != LANDED]
    if kv["json"]:
        _out(json.dumps({"path": worktree_dir, "safe_to_remove": not unsafe,
                         "files": files}, indent=2, sort_keys=True))
        return 1 if unsafe else 0
    if not unsafe:
        _out("bm_reconcile_worktrees check: safe to remove -- every "
             "uncommitted tracked file in %s is LANDED (%d checked)."
             % (worktree_dir, len(files)))
        return 0
    _out("bm_reconcile_worktrees check: UNSAFE to remove %s -- %d file(s) "
        "are not confirmed LANDED:" % (worktree_dir, len(unsafe)))
    for f in unsafe:
        _out("  [%s] %s" % (f["verdict"], f["path"]))
    return 1


def main(argv):
    verb, kv, err = _parse_argv(argv)
    if err:
        _err(err)
        return 2
    try:
        if verb == "check":
            return cmd_check(kv)
        return cmd_sweep(kv)
    except Exception as exc:
        _out("NO-DATA: %s: %s" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
