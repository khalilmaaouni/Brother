#!/usr/bin/env python3
"""Report, and optionally prune, this estate's leftover temp trees.

Measured 2026-09-04 18:05: the user temp directory held 85,119 entries and
11 GB of test trees this repository's own suites had walked away from, and
free disk fell from 13 GiB to 8 GiB in two hours of lane runs. scripts and
tests now sandbox their temp trees (scripts/tmp_sandbox.py), so new residue
should be zero; this instrument exists to SAY so at the start and the end of
a battery run, and to clear whatever a killed process left behind.

PRUNING IS BY MODIFICATION TIME, NOT CREATION TIME, and that is deliberate:
lanes run concurrently on this machine, a peer's suite can still be writing
into a tree created an hour ago, and removing it underneath the peer turns
one lane's tidiness into another lane's traceback. A tree nothing has
written to for the whole window is the only tree this removes.

Exit 0 with a PASS line, or 2 with NO-DATA when the temp root cannot be
read. Never exit 1: leftover files are not a broken build.
"""
import argparse
import os
import shutil
import sys
import tempfile
import time

# The families observed in the 2026-09-04 measurement, plus the sandbox
# prefix tmp_sandbox.py now creates. Extend this tuple in place when a new
# family appears; nothing outside it is ever touched.
PREFIXES = (
    "brother-test-",
    "brother-run-",
    "brother-lane-",
    "brother-export-",
    "bm-",
    "bm_",
    "sbe-",
    "sbe_",
    "canon-",
    "rewrite-check-",
    "dep-mutation-",
    "dep-nofile-",
    "dep-e2e-",
    "e1-evidence-",
    "e81-receipt-",
    "bundle-runtime-",
    "product-acceptance-",
    "refuse-broken-",
    "usage-seam-",
    "usage-sidecar-",
    "lens-intent-",
    "screen-loom-",
    "root-commit-",
    "zero-change-",
    "not-a-repo-",
    "unreadable-root-",
    "required-fast-fail-",
)


def temp_root():
    """The real temp root, even inside a tmp_sandbox'd process."""
    return os.environ.get("BROTHER_TEMP_ROOT") or tempfile.gettempdir()


def brother_entries(root):
    """(name, path, mtime) for every entry whose name starts with a prefix."""
    try:
        names = os.listdir(root)
    except OSError as exc:
        sys.stderr.write("temp_residue: cannot read %s: %s\n" % (root, exc))
        return None
    found = []
    for name in names:
        if not name.startswith(PREFIXES):
            continue
        path = os.path.join(root, name)
        try:
            mtime = os.lstat(path).st_mtime
        except FileNotFoundError:
            # Lanes run concurrently: a peer deleting its own tree between
            # the listdir and the stat is the outcome this tool wants, not
            # an error worth a line.
            continue
        except OSError as exc:
            sys.stderr.write("temp_residue: cannot stat %s: %s\n" % (path, exc))
            continue
        found.append((name, path, mtime))
    return found


def force_writable(path):
    """Tests create read-only fixture trees on purpose; rmtree cannot enter."""
    for dirpath, dirnames, _files in os.walk(path):
        for name in [dirpath] + [os.path.join(dirpath, d) for d in dirnames]:
            try:
                os.chmod(name, 0o700)
            except OSError as exc:
                sys.stderr.write(
                    "temp_residue: cannot chmod %s: %s\n" % (name, exc))


def prune(entries, older_than_seconds, now=None):
    """Remove entries untouched for the window. Returns the removed paths."""
    now = time.time() if now is None else now
    removed = []
    for _name, path, mtime in entries:
        if now - mtime < older_than_seconds:
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            force_writable(path)
            try:
                shutil.rmtree(path)
            except OSError as exc:
                sys.stderr.write(
                    "temp_residue: left behind %s: %s\n" % (path, exc))
                continue
        else:
            try:
                os.remove(path)
            except OSError as exc:
                sys.stderr.write(
                    "temp_residue: left behind %s: %s\n" % (path, exc))
                continue
        removed.append(path)
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="temp",
                    help="what to call this reading in the printed line")
    ap.add_argument("--prune", action="store_true",
                    help="remove matching entries untouched for --hours")
    ap.add_argument("--hours", type=float, default=1.0,
                    help="age window in hours (default 1)")
    args = ap.parse_args(argv)

    root = temp_root()
    entries = brother_entries(root)
    if entries is None:
        print("NO-DATA temp_residue: %s could not be read" % root)
        return 2
    removed = []
    if args.prune:
        removed = prune(entries, args.hours * 3600.0)
        for path in removed:
            print("  removed %s" % path)
    print("PASS temp_residue %s: %d Brother temp entries in %s, "
          "%d removed (older than %gh)"
          % (args.label, len(entries), root, len(removed), args.hours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
