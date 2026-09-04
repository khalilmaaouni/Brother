#!/usr/bin/env python3
"""Refresh a cut: rewrite the export manifest and the release note that
states its digest, then say whether they describe the tree as it stands.

WHY THIS EXISTS (row E110, measured on the public tag v1.0.2). The cut
script writes docs/releases/<version>.export-manifest.txt and
docs/releases/<version>.md together at step 2b, and both describe the tree
AT THAT MOMENT. Anything that lands on the cut branch afterwards, a fix, a
merge of main, a follow-up commit, moves the tree and leaves both files
describing a tree nobody will publish. That is exactly what happened to
v1.0.2: four commits landed after the cut commit, and the tag shipped a
1198 line manifest whose named bytes differ for 18 files.

THE TWO INVOCATIONS, from the hub root:

    python3 scripts/refresh_cut.py --version 1.0.3
        regenerate the manifest and the note. This is the same command the
        cut script runs at step 2b (`release_note_from_tree.py --write
        --version <version>`), spawned as its own process on purpose so it
        reads the tree fresh; it is safe to run as many times as there are
        commits. COMMIT the two files it rewrites before tagging.

    python3 scripts/refresh_cut.py --version 1.0.3 --check
        change nothing, build the export tree the way the exporter itself
        builds it, and run the exporter's own tag-time manifest check over
        it. CLEAR means a tag cut from this tree would carry a manifest
        that describes it. REFUSED names the first three files that
        disagree and the count. This is the cheap dry run of the check that
        export_public.py --push --tag now performs for real.

Exit codes, this estate's three: 0 CLEAR, 1 REFUSED, 2 NO-DATA. NO-DATA is
never a pass. Python 3.9, standard library only.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402
import release_note_from_tree as RN  # noqa: E402

EXIT_CLEAR = 0
EXIT_REFUSED = 1
EXIT_NODATA = 2


def regenerate(version, root=EP.ROOT):
    """Run the cut script's own step 2b command, verbatim, in its own
    process. A subprocess rather than RN.main() because export_manifest()
    memoizes its answer for the life of a process: a refresh that reused a
    memo from an earlier call in the same run would write the manifest of
    a tree that has already moved, which is the very defect this closes.
    Returns (ok, lines)."""
    cmd = [sys.executable,
           os.path.join(root, "scripts", "release_note_from_tree.py"),
           "--write", "--version", version]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    except OSError as exc:
        return False, ["NO-DATA: could not run %s (%s)"
                       % (" ".join(cmd), exc)]
    lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        lines.append("NO-DATA: %s exited %d: %s"
                     % (" ".join(cmd), proc.returncode,
                        tail[-1] if tail else "(no output)"))
        return False, lines
    # build_export_tree walks `git ls-files`, so it sees the INDEX, never
    # the working tree alone: a manifest just written and not yet staged is
    # invisible to the check below and reads NO-DATA. Said here rather than
    # left for the operator to deduce from that verdict.
    lines.append("stage the two files above (git add) before checking: the "
                 "export tree is built from git's index, so an unstaged "
                 "manifest is not in it")
    return True, lines


def check(version, root=EP.ROOT):
    """Build the export tree with the exporter's own build_export_tree and
    ask the exporter's own tag-time check whether the manifest inside it
    describes it. Returns (exit_code, lines)."""
    allowlist = EP.load_allowlist()
    if allowlist is None:
        return EXIT_NODATA, ["NO-DATA: no export allowlist at %s, so there "
                             "is no export tree to check a manifest against"
                             % EP.DEFAULT_ALLOWLIST]
    dest = tempfile.mkdtemp(prefix="refresh-cut-export-")
    try:
        copied = EP.build_export_tree(dest, allowlist, root)
        if not copied:
            return EXIT_NODATA, ["NO-DATA: the allowlist copied nothing "
                                 "from %s" % root]
        ok, lines = EP.check_export_manifest(dest, version)
    except OSError as exc:
        return EXIT_NODATA, ["NO-DATA: the export tree could not be built "
                             "or read: %s" % exc]
    finally:
        # ignore_errors: cleanup must never turn a finished verdict into a
        # crash, and by this point the verdict is already decided.
        shutil.rmtree(dest, ignore_errors=True)
    if ok:
        lines.append("CLEAR: the manifest in this tree describes this tree, "
                     "so a tag cut from it would ship a contents claim a "
                     "clone can check")
        return EXIT_CLEAR, lines
    if lines and lines[0].startswith("NO-DATA"):
        return EXIT_NODATA, lines
    return EXIT_REFUSED, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=None,
                     help="the release being cut (default: "
                          ".claude-plugin/marketplace.json's own "
                          "metadata.version)")
    ap.add_argument("--check", action="store_true",
                     help="change nothing; only report whether the manifest "
                          "in the tree describes the tree")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    version = args.version or RN.default_version()
    if not version:
        print("NO-DATA: no --version given and no version could be read "
              "from the marketplace manifest")
        return EXIT_NODATA

    if not args.check:
        ok, lines = regenerate(version)
        for line in lines:
            print(line)
        if not ok:
            return EXIT_NODATA

    code, lines = check(version)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
