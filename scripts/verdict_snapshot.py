#!/usr/bin/env python3
"""ORCH-21: bind a verdict to the tree it was actually earned on.

THE DECIDING PROPERTY: a verdict binds to the measured source snapshot OF
THE TESTED CHANGE, never merely to a revision name like a git SHA. A SHA
alone misses a dirty tree (uncommitted edits the check actually ran
against), so this module hashes the real file contents of the tested
change and carries the revision only as informational metadata: is_current
below never compares revisions, only file hashes, because two trees with
identical content ARE the same tested change even if one carries a
different or no revision string. A stale snapshot is REFUSED, never
silently re-read as if it were still the one the verdict was earned on.

EVIDENCE EXCLUSION, load-bearing, read this before touching either
function: the receipt/evidence file a caller writes to record a verdict
must be excluded from the hashed set, explicitly, via take_snapshot's
`exclude` argument. Writing the receipt changes a file on disk; if that
file were inside the hashed set, writing the receipt would immediately
stale the verdict the receipt is trying to record, which would make every
verdict this module produces stale from the moment it is written. This is
excluded on purpose, not an oversight: see the `excluded` field this
module records and never compares.

Python 3.9 floor, standard library only, no network, no git.
"""
import argparse
import hashlib
import json
import sys

import evidence_obligation

_CHUNK = 65536


class SnapshotUnavailable(Exception):
    """A snapshot could not be measured. Never becomes a fabricated result:
    an unmeasurable tree must refuse to bind, not silently bind to nothing
    or to a partial read."""


def _normalize(path):
    return path.replace("\\", "/")


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def take_snapshot(paths, revision=None, exclude=()):
    """Hash every path in `paths` (the actual tested change: the files the
    check under test ran against, never merely "HEAD" or "the whole repo").

    revision is opaque metadata (a git SHA, a dirty-tree marker, whatever
    the caller uses to label this snapshot) recorded but never interpreted
    or compared here: see the module docstring for why content, not a
    revision name, is the thing a verdict actually binds to.

    exclude names paths (matched exactly, after normalizing separators) to
    leave OUT of the hashed set: the receipt/evidence output a caller is
    about to write. An excluded path is recorded in the result's
    "excluded" list, visibly, so leaving a path out reads as a deliberate
    choice on inspection, never as a silently dropped input.

    Raises SnapshotUnavailable, never returns an empty or partial result,
    when:
      paths is empty, or every path given is excluded, so nothing remains
        to hash: a verdict cannot bind to a snapshot of nothing. This is
        far more often a caller bug (forgot to pass the diff, or excluded
        the one file that mattered) than a genuine zero-file change, and
        the cost of wrongly refusing an intentional empty-change verdict
        is a caller adding one explicit path, against the cost on this
        estate's own record of a verdict silently bound to nothing and
        later read as covering whatever the tree happened to become.
      a path cannot be opened or read (missing, permission denied, or
        vanished between being listed and being read): an unmeasurable
        tree never yields a verdict, per the module the CLI mirrors
        (record_distance.py's NO-DATA, never a fabricated 0).
    """
    exclude_set = {_normalize(p) for p in exclude}
    files = {}
    excluded = []
    for raw in paths:
        path = _normalize(raw)
        if path in exclude_set:
            excluded.append(path)
            continue
        try:
            files[path] = _hash_file(path)
        except OSError as exc:
            raise SnapshotUnavailable("cannot read %s: %s" % (path, exc))
    if not files:
        raise SnapshotUnavailable("empty change set: nothing to bind a verdict to")
    return {"revision": revision, "files": files, "excluded": sorted(excluded)}


def bind(verdict, snapshot):
    """Bind `verdict` (one of evidence_obligation.VERDICTS) to a snapshot
    already taken by take_snapshot, returning a verdict record.

    Takes an already-measured snapshot rather than a path list on purpose:
    a snapshot that could not be measured never reaches here, because
    take_snapshot raised SnapshotUnavailable instead of returning one. This
    function has no NO-DATA branch of its own for that reason; an
    unmeasurable tree is refused one call earlier.
    """
    if verdict not in evidence_obligation.VERDICTS:
        raise ValueError("unknown verdict: %r" % (verdict,))
    if not isinstance(snapshot, dict) or "files" not in snapshot:
        raise ValueError("not a snapshot from take_snapshot: %r" % (snapshot,))
    return {
        "verdict": verdict,
        "revision": snapshot.get("revision"),
        "files": dict(snapshot["files"]),
        "excluded": list(snapshot.get("excluded", ())),
    }


def is_current(record, snapshot_now):
    """(status, path) where status is "CURRENT" or "STALE".

    Compares record["files"] against snapshot_now["files"] only: revision
    is never consulted (see module docstring), and "excluded" is never
    consulted either, which is precisely what keeps a changing receipt
    file from staling the verdict that describes it, as long as the
    receipt path was passed to take_snapshot's `exclude` on both sides.

    Walks the sorted union of both file-key sets so the result is
    deterministic and the first differing path is always the same one on
    a repeated call against the same two inputs. Three ways a path can
    differ, each is STALE and each names that path:
      present in snapshot_now, absent from record   (added after binding)
      present in record, absent from snapshot_now   (deleted after binding,
        or renamed: same content at a new path is a different key, not a
        match, so a rename shows as one of these two cases rather than
        being missed as "unchanged")
      present in both with different hashes         (changed)

    A path that never appears in either set (changed elsewhere in the
    tree, outside the tested change these two snapshots were both taken
    over) never enters this comparison at all and so can never stale the
    verdict: that scope discipline lives in what the caller passes to
    take_snapshot for snapshot_now, not in this function, which only ever
    sees what it is given.

    Raises ValueError, never a default answer, when either argument is not
    a dict carrying a "files" mapping: a malformed input is a caller
    defect, not a real STALE/CURRENT reading of an actual tree.
    """
    for name, value in (("record", record), ("snapshot_now", snapshot_now)):
        if not isinstance(value, dict) or "files" not in value:
            raise ValueError("not a record/snapshot with files: %s=%r" % (name, value))
    before = record["files"]
    after = snapshot_now["files"]
    for path in sorted(set(before) | set(after)):
        if path not in before or path not in after or before[path] != after[path]:
            return "STALE", path
    return "CURRENT", None


def _cli_bind(args):
    try:
        snap = take_snapshot(args.paths, revision=args.revision, exclude=args.exclude or ())
    except SnapshotUnavailable as exc:
        print("verdict_snapshot bind: NO-DATA (%s)" % exc, file=sys.stderr)
        return 2
    record = bind(args.verdict, snap)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print("verdict_snapshot bind: wrote %s (%s, %d files)"
          % (args.out, args.verdict, len(record["files"])))
    return 0


def _cli_check(args):
    try:
        with open(args.record, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except OSError as exc:
        print("verdict_snapshot check: NO-DATA (cannot read %s: %s)" % (args.record, exc),
              file=sys.stderr)
        return 2
    try:
        snap_now = take_snapshot(args.paths, exclude=args.exclude or ())
    except SnapshotUnavailable as exc:
        print("verdict_snapshot check: NO-DATA (%s)" % exc, file=sys.stderr)
        return 2
    status, path = is_current(record, snap_now)
    if status == "CURRENT":
        print("verdict_snapshot check: CURRENT (%s)" % record.get("verdict"))
        return 0
    print("verdict_snapshot check: STALE, first differing path: %s" % path)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    bind_parser = subparsers.add_parser("bind")
    bind_parser.add_argument("--verdict", required=True, choices=evidence_obligation.VERDICTS)
    bind_parser.add_argument("--paths", nargs="+", required=True)
    bind_parser.add_argument("--exclude", nargs="*")
    bind_parser.add_argument("--revision")
    bind_parser.add_argument("--out", required=True)
    bind_parser.set_defaults(func=_cli_bind)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--record", required=True)
    check_parser.add_argument("--paths", nargs="+", required=True)
    check_parser.add_argument("--exclude", nargs="*")
    check_parser.set_defaults(func=_cli_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
