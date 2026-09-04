#!/usr/bin/env python3
"""verify_runtime.py: do the bytes in this directory match RUNTIME-MANIFEST.json?

Run it from anywhere, with nothing else installed:

    python3 <this file>

It reads RUNTIME-MANIFEST.json out of ITS OWN directory, re-hashes every file
that manifest names, and reports one verdict:

    PASS      every manifested file is present and its sha256 matches
    FAIL      a file is missing, unreadable, or its bytes differ (exit 1)
    NO-DATA   the manifest is missing or unreadable, so nothing was checked
              and this is NEVER a pass (exit 2)

What it does NOT answer: whether the manifest itself is the one the release
published. That is a different question, answered by comparing this file tree
against the published tag (scripts/release_invariant.py does that on a
checkout). Here the manifest is the reference, so a tamper that rewrites both
a file and its manifest line passes: say so plainly rather than implying more.

Written by scripts/bundle_runtime.py; never hand edited.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_NAME = "RUNTIME-MANIFEST.json"


def load_manifest(path):
    """The manifest as a dict, or None when it is absent or not readable
    JSON. None is NO-DATA at the call site, never an empty file list."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        return None
    return doc


def verify(runtime_dir=HERE):
    """(verdict, lines): verdict is "PASS", "FAIL" or "NO-DATA"; lines are
    the human readable detail, most important first."""
    manifest = load_manifest(os.path.join(runtime_dir, MANIFEST_NAME))
    if manifest is None:
        return "NO-DATA", ["%s is missing or unreadable in %s; nothing was "
                           "checked" % (MANIFEST_NAME, runtime_dir)]
    entries = manifest["files"]
    if not entries:
        return "NO-DATA", ["%s names no file, so there is nothing to check"
                           % MANIFEST_NAME]
    bad = []
    for entry in entries:
        if not isinstance(entry, dict):
            bad.append("manifest entry is not an object: %r" % (entry,))
            continue
        rel = entry.get("path")
        want = entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(want, str):
            bad.append("manifest entry has no usable path/sha256: %r" % (entry,))
            continue
        full = os.path.join(runtime_dir, *rel.split("/"))
        if os.path.islink(full):
            bad.append("%s: a symlink on disk, but the manifest attests a "
                       "regular file" % rel)
            continue
        try:
            with open(full, "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            bad.append("%s: missing or unreadable (%s)" % (rel, exc.strerror))
            continue
        if got != want:
            bad.append("%s: sha256 %s, manifest says %s" % (rel, got, want))
    if bad:
        return "FAIL", bad
    return "PASS", ["all %d manifested file(s) match their sha256 in %s"
                    % (len(entries), MANIFEST_NAME)]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    runtime_dir = args[0] if args else HERE
    verdict, lines = verify(runtime_dir)
    print("verify_runtime: %s: %s" % (verdict, lines[0]))
    for line in lines[1:]:
        print("verify_runtime:   %s" % line)
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
