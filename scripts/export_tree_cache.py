"""export_tree_cache.py: one export tree per source tree, leased to every consumer of the cut path.

WHY (FR1.b, C2). A rehearsal built the orphan export tree six to eight times on identical bytes (the preflight's
two export rows, the chain's export_rows, the tag time id, the Linux canary), each build copying the allowlist and
regenerating two checksum files. This module builds it ONCE per key and gives every consumer its own detached git
worktree of that one orphan commit.

KEY. sha256 of three digests: the source tree id (`git rev-parse HEAD^{tree}` of the root), the sha256 of the
exporter file and the sha256 of the allowlist file. A dirty root is never keyed (the exporter copies working files,
which a HEAD tree id cannot name). Any doubt returns (None, reason) and the caller builds as before.

LAYOUT. <scratch_root>/export-tree-cache/<key16>/{meta.json, orphan/}. Beside it: <key16>.lock (held only around
a worktree add), <key16>.tmp-<pid>-<id> (a build in flight, invisible to a lease) and <key16>.bad-<pid>-<id> (an entry
that failed verification, renamed aside atomically and then deleted; only a kill mid delete leaves one, and the age
prune removes it).

FOOTPRINT (resource law). One orphan tree of the export (a few thousand small files) per entry, at most MAX_ENTRIES
entries (newest kept), nothing older than MAX_AGE_S, all of it under the scratch root and never the system temp
folder. Pruning runs after every publish. A leased worktree is the consumer's own directory and costs one checkout;
the consumer deletes it, and the stale git record is pruned by the next lease.

FAILS: every branch that cannot prove the entry returns (None, reason) or rebuilds; a corrupt or mutated entry is
never handed out. ROLLBACK: pass cache=False at the three cut path call sites; the folder ages out by itself.
KNOWN CEILING: a dead owner's lock is removed the cut_rehearsal way, so two processes that both see it dead can
race; the cost is one extra worktree add failing and its consumer building as before.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from typing import Callable, Dict, NamedTuple, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_preflight as P  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

KEY_FIELDS = ("source_tree", "exporter_sha256", "allowlist_sha256")
MAX_ENTRIES = 3
MAX_AGE_S = 86400
LOCK_WAIT_S = 120
GIT_TIMEOUT_S = 600
EXPORTER_REL = os.path.join("scripts", "export_public.py")
ALLOWLIST_REL = os.path.join("docs", "plan", "EXPORT-ALLOWLIST.txt")
_ENTRY = re.compile(r"[0-9a-f]{16}")
_TREE = re.compile(r"[0-9a-f]{40}")


class Lease(NamedTuple):
    path: str
    export_tree_id: str
    key: str


def _git(args, cwd, runner=None):
    """(exit code, stdout stripped, last line of output). Never raises; no GIT_ variable reaches the child."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    p = P._run(["git"] + list(args), cwd, runner, env=env, timeout=GIT_TIMEOUT_S)
    return p.returncode, (p.stdout or "").strip(), P._last(p)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(root: str, exporter: str = "", allowlist: str = "", runner=None) -> Tuple[Dict[str, str], str]:
    """(fields, full key sha256 hex), or ({}, reason). `exporter` and `allowlist` are file paths and default to the
    root's own. Dirty root, unreadable file, failing git, hostile root: ({}, reason)."""
    if not isinstance(root, str) or not root or not os.path.isdir(root):
        return {}, "root %r is not an existing directory" % (root,)
    exporter = exporter if isinstance(exporter, str) and exporter else os.path.join(root, EXPORTER_REL)
    allowlist = allowlist if isinstance(allowlist, str) and allowlist else os.path.join(root, ALLOWLIST_REL)
    rc, dirty, last = _git(["status", "--porcelain"], root, runner)
    if rc != 0:
        return {}, "git status failed in %s: %s" % (root, last)
    if dirty:
        return {}, "the source tree is dirty, so its HEAD tree id does not name the bytes the exporter copies"
    rc, tree, last = _git(["rev-parse", "HEAD^{tree}"], root, runner)
    if rc != 0 or not _TREE.fullmatch(tree):
        return {}, "git rev-parse HEAD^{tree} failed in %s: %s" % (root, last)
    try:
        fields = {"source_tree": tree, "exporter_sha256": _sha256(exporter), "allowlist_sha256": _sha256(allowlist)}
        with open(allowlist, encoding="utf-8") as fh:
            live = [l for l in fh.read().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    except (OSError, ValueError) as exc:
        return {}, "a key file could not be read: %s" % exc
    if not live:
        return {}, "the allowlist names no path, so there is no export tree to cache"
    return fields, hashlib.sha256("\n".join(fields[k] for k in KEY_FIELDS).encode("utf-8")).hexdigest()


def cache_root(directory: str = "") -> str:
    return os.path.abspath(directory) if directory else os.path.join(CR.scratch_root(), "export-tree-cache")


def _verified(entry, fields, runner):
    """(meta, '') when the entry proves itself, else (None, reason)."""
    orphan = os.path.join(entry, "orphan")
    try:
        with open(os.path.join(entry, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "meta.json is not readable: %s" % exc
    tid = meta.get("export_tree_id") if isinstance(meta, dict) else None
    if not isinstance(meta, dict) or meta.get("key_fields") != fields or not isinstance(tid, str) \
            or not _TREE.fullmatch(tid):
        return None, "meta.json does not name this key"
    if not os.path.isdir(os.path.join(orphan, ".git")) or not os.path.isfile(os.path.join(orphan, P.EXPORT_TREE_MARKER)):
        return None, "the entry is not an export tree"
    rc, out, last = _git(["rev-parse", "HEAD^{tree}"], orphan, runner)
    if rc != 0 or out != tid:
        return None, "the entry's HEAD tree is not the recorded one (%s)" % last
    rc, out, last = _git(["status", "--porcelain"], orphan, runner)
    if rc != 0 or out:
        return None, "the entry was modified after it was built"
    return meta, ""


def _publish(base, key, fields, build, runner):
    """Build into a private folder and publish it with one rename. (meta, '') or (None, reason). A rename lost to
    another process discards this build and answers with the winner's verified entry."""
    entry = os.path.join(base, key[:16])
    tmp = "%s.tmp-%d-%s" % (entry, os.getpid(), uuid.uuid4().hex[:8])
    try:
        os.makedirs(os.path.join(tmp, "orphan"))
        build(os.path.join(tmp, "orphan"))
        rc, tid, last = _git(["rev-parse", "HEAD^{tree}"], os.path.join(tmp, "orphan"), runner)
        if rc != 0 or not _TREE.fullmatch(tid) or not os.path.isfile(os.path.join(tmp, "orphan", P.EXPORT_TREE_MARKER)):
            return None, "the built tree is not an export tree with a commit (%s)" % last
        meta = {"key": key, "key_fields": fields, "export_tree_id": tid, "built_at": int(time.time())}
        with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        try:
            os.rename(tmp, entry)
        except OSError as exc:
            if not os.path.isdir(entry):
                return None, "the entry could not be published: %s" % exc
            return _verified(entry, fields, runner)
        return meta, ""
    except Exception as exc:  # sbe: allow-silent returned as the reason, never swallowed
        return None, "the export tree could not be built: %s" % exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prune(base, keep):
    """Remove aged or surplus entries and aged tmp/bad folders, only inside `base`; the `keep` entry stays."""
    now, entries = time.time(), []
    for name in os.listdir(base):
        path = os.path.join(base, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:  # sbe: allow-silent a folder that vanished is already pruned
            continue
        age = now - mtime
        if not os.path.isdir(path):
            continue
        if _ENTRY.fullmatch(name):
            entries.append((mtime, name))
        elif (".tmp-" in name or ".bad-" in name) and age > MAX_AGE_S:
            shutil.rmtree(path, ignore_errors=True)
    for i, (mtime, name) in enumerate(sorted(entries, reverse=True)):
        if name != keep and (i >= MAX_ENTRIES or now - mtime > MAX_AGE_S):
            shutil.rmtree(os.path.join(base, name), ignore_errors=True)


def _take(lock):
    """'' once the O_EXCL lock is held (our pid inside), else the reason; a dead owner's lock is replaced."""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not CR._lock_alive(lock)[0]:
                try:
                    os.unlink(lock)
                except OSError:  # sbe: allow-silent another process replaced it first, the loop retries
                    pass
                continue
            if time.monotonic() > deadline:
                return "the lease lock %s is still held after %ds" % (lock, LOCK_WAIT_S)
            time.sleep(0.05)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return ""


def _empty(dest):
    if isinstance(dest, str) and os.path.isdir(dest):
        for name in os.listdir(dest):
            path = os.path.join(dest, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)


def lease_into(dest: str, root: str, build: Optional[Callable[[str], None]] = None, directory: str = "",
               exporter: str = "", allowlist: str = "", runner=None) -> Tuple[Optional[Lease], str]:
    """(Lease, '') with `dest` now a worktree of the cached export tree for root's key, building it first when no
    verified entry exists; or (None, reason) with `dest` left empty. Never raises."""
    touched = False
    try:
        if not isinstance(dest, str) or not dest:
            return None, "dest must be a non empty path"
        if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
            return None, "dest %r is not an empty directory" % dest
        if build is not None and not callable(build):
            return None, "build must be callable or None"
        fields, key = cache_key(root, exporter, allowlist, runner)
        if not fields:
            return None, key
        base = cache_root(directory)
        entry = os.path.join(base, key[:16])
        os.makedirs(base, exist_ok=True)
        meta, why = _verified(entry, fields, runner) if os.path.isdir(entry) else (None, "no entry")
        if meta is None:
            if os.path.lexists(entry):
                bad = "%s.bad-%d-%s" % (entry, os.getpid(), uuid.uuid4().hex[:8])
                os.rename(entry, bad)
                shutil.rmtree(bad, ignore_errors=True)
            meta, why = _publish(base, key, fields, build or P.export_tree_builder(root), runner)
            _prune(base, key[:16])
            if meta is None:
                return None, why
        lock = entry + ".lock"
        why = _take(lock)
        if why:
            return None, why
        try:
            orphan, touched = os.path.join(entry, "orphan"), True
            _git(["worktree", "prune"], orphan, runner)
            rc, _out, last = _git(["worktree", "add", "--detach", dest, "HEAD"], orphan, runner)
        finally:
            if os.path.lexists(lock):
                os.unlink(lock)
        if rc != 0:
            _empty(dest)
            return None, "git worktree add failed: %s" % last
        return Lease(dest, meta["export_tree_id"], key), ""
    except Exception as exc:  # sbe: allow-silent returned as the reason, never swallowed
        if touched:
            _empty(dest)
        return None, "the export tree cache failed: %s" % exc


def release(lease: Lease) -> str:
    """Remove the leased worktree and its git record. '' or the reason; never raises."""
    try:
        path, why = lease.path, ""
        orphan = ""
        try:
            with open(os.path.join(path, ".git"), encoding="utf-8") as fh:   # "gitdir: <orphan>/.git/worktrees/<n>"
                gitdir = fh.read().strip().split("gitdir:", 1)[1].strip()
            orphan = os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))
        except (OSError, IndexError):  # sbe: allow-silent no readable link: the path is removed below all the same
            why = "the worktree link is unreadable"
        if orphan and os.path.isdir(orphan):
            rc, _o, last = _git(["worktree", "remove", "--force", path], orphan)
            why = "" if rc == 0 else "git worktree remove failed: %s" % last
        shutil.rmtree(path, ignore_errors=True)
        if orphan and os.path.isdir(orphan):
            _git(["worktree", "prune"], orphan)
        return why
    except Exception as exc:  # sbe: allow-silent returned as the reason, never swallowed
        return "release failed: %s" % exc


def main(argv=None, build: Optional[Callable[[str], None]] = None) -> int:
    """--root R --id: print `EXPORT-TREE: <id>` (exit 0) or `NO-DATA: <reason>` (exit 2)."""
    ap = argparse.ArgumentParser(description="print the export tree id for a root, building it once per key")
    ap.add_argument("--root", default=os.path.dirname(HERE))
    ap.add_argument("--directory", default="")
    ap.add_argument("--id", action="store_true", required=True)
    args = ap.parse_args(argv)
    try:
        os.makedirs(CR.scratch_root(), exist_ok=True)
        dest = tempfile.mkdtemp(prefix="export-tree-id-", dir=CR.scratch_root())
    except OSError as exc:
        print("NO-DATA: the scratch root is not usable: %s" % exc)
        return 2
    try:
        lease, why = lease_into(dest, args.root, build=build, directory=args.directory)
        if lease is None:
            print("NO-DATA: %s" % why)
            return 2
        print("EXPORT-TREE: %s" % lease.export_tree_id)
        release(lease)
        return 0
    finally:
        shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
