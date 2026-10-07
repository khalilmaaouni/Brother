#!/usr/bin/env python3
"""verify_runtime.py: do the bytes in an installed copy match its manifest,
and is every read confined to the copy it was pointed at?

This module is the checkout-side home of the verifier that ships inside an
installed plugin as bundle/runtime/verify_runtime.py: the text
scripts/bundle_runtime.py carries in VERIFIER_SOURCE and writes out at
generation time. It answers the same question the shipped file answers, PASS,
FAIL or NO-DATA, and it carries the two controls D5.4 requires of that
verifier.

  1. A manifest entry whose path is absolute or carries a ".." path segment is
     refused BEFORE any file is opened, named by the raw path and never
     followed. A path a verifier would not follow is a defect whether or not
     the file it names happens to exist.
  2. After the join, the realpath of the target must stay under the realpath of
     runtime_dir, so a symlinked parent directory leading out of the copy is
     caught even when no ".." appears anywhere in the manifest text. The read
     that escapes is the one that turns a tampered plugin into a PASS.

FAIL DIRECTION: a copy that would reach outside itself, or whose bytes do not
match its manifest, is a FAIL, never a pass and never a silent read. A manifest
that is missing, unreadable, or names no file is NO-DATA: nothing was checked,
so nothing is known, and NO-DATA is never a pass.

HOSTILE INPUT: every public function here returns its own refusal value rather
than raising. verify() answers NO-DATA for a runtime_dir that is not a path,
load_manifest() answers None for a path that is not a path or for a file that
is not readable JSON, and main() answers exit 2 for an argv that is not a list
of strings.

Python 3, standard library only. No network. Nothing outside the runtime_dir
handed in is ever read.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_NAME = "RUNTIME-MANIFEST.json"
NODATA = "NO-DATA"


def _as_path(value):
    """The filesystem path `value` names, or None when it does not name one. A
    refusal, never a raise: a wrong type, a bool, a number, a NaN, a list, a
    dict, or a bytes value that will not decode as text all come back as None,
    because nothing was read and so nothing is known."""
    try:
        path = os.fspath(value)
    except TypeError:
        return None
    if isinstance(path, bytes):
        try:
            path = os.fsdecode(path)
        except (UnicodeDecodeError, ValueError):
            return None
    if not isinstance(path, str):
        return None
    return path


def load_manifest(path):
    """The manifest at `path` as a dict, or None when it is absent, is not
    readable JSON, or is not shaped like a manifest. None is NO-DATA at the
    call site, never an empty file list, and never a raise: a directory, an
    unreadable file, a file that is not utf-8, and a path that is not a path
    all come back as None."""
    path = _as_path(path)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        return None
    return doc


def _confined(runtime_dir, rel):
    """(ok, full): whether the manifest entry naming `rel` may be read, and the
    path to read when it may. An entry that is not a string, is empty, carries
    a NUL byte, is absolute, or carries a ".." segment is refused before any
    join, so the path it names is never opened. After the join the realpath of
    the target must stay under realpath(runtime_dir): a symlinked parent
    directory leading outside the copy is exactly the case that fails there."""
    if not isinstance(rel, str) or not rel or chr(0) in rel:
        return False, None
    if os.path.isabs(rel) or rel.startswith("/"):
        return False, None
    parts = rel.split("/")
    if ".." in parts:
        return False, None
    full = os.path.join(runtime_dir, *parts)
    try:
        root = os.path.realpath(runtime_dir)
        realfull = os.path.realpath(full)
        inside = os.path.commonpath([root, realfull]) == root
    except (OSError, ValueError, TypeError):
        return False, None
    return inside, full


def verify(runtime_dir=HERE):
    """(verdict, lines): verdict is "PASS", "FAIL" or "NO-DATA"; lines are the
    human readable detail, most important first. A runtime_dir that is not a
    path is NO-DATA, because nothing was read and so nothing is known."""
    root = _as_path(runtime_dir)
    if root is None:
        return NODATA, ["runtime_dir is not a path, so nothing was checked: "
                        "%r" % (runtime_dir,)]
    manifest = load_manifest(os.path.join(root, MANIFEST_NAME))
    if manifest is None:
        return NODATA, ["%s is missing or unreadable in %s; nothing was "
                        "checked" % (MANIFEST_NAME, root)]
    entries = manifest["files"]
    if not entries:
        return NODATA, ["%s names no file, so there is nothing to check"
                        % MANIFEST_NAME]
    bad = []
    for entry in entries:
        if not isinstance(entry, dict):
            bad.append("manifest entry is not an object: %r" % (entry,))
            continue
        rel = entry.get("path")
        want = entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(want, str):
            bad.append("manifest entry has no usable path/sha256: %r"
                       % (entry,))
            continue
        confined, full = _confined(root, rel)
        if not confined:
            bad.append("%s: absolute or leaves the runtime directory, so it "
                       "was never read" % rel)
            continue
        if os.path.islink(full):
            bad.append("%s: a symlink on disk, but the manifest attests a "
                       "regular file" % rel)
            continue
        try:
            with open(full, "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            bad.append("%s: missing or unreadable (%s)"
                       % (rel, exc.strerror or exc.__class__.__name__))
            continue
        if got != want:
            bad.append("%s: sha256 %s, manifest says %s" % (rel, got, want))
    if bad:
        return "FAIL", bad
    return "PASS", ["all %d manifested file(s) match their sha256 in %s"
                    % (len(entries), MANIFEST_NAME)]


def main(argv=None):
    """Exit 0 on PASS, 1 on FAIL, 2 on NO-DATA. An argv that is not a list or
    tuple of strings is refused as NO-DATA rather than coerced, because the
    verifier never guesses what it was asked to check."""
    raw = sys.argv[1:] if argv is None else argv
    if (not isinstance(raw, (list, tuple))
            or any(not isinstance(arg, str) for arg in raw)):
        print("verify_runtime: %s: argv must be a list of strings" % NODATA)
        return 2
    runtime_dir = raw[0] if raw else HERE
    verdict, lines = verify(runtime_dir)
    print("verify_runtime: %s: %s" % (verdict, lines[0] if lines else ""))
    for line in lines[1:]:
        print("verify_runtime:   %s" % line)
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
