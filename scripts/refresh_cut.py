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

THE 1.0.4 REPEAT (measured 2026-09-05, X7 second side). This tool writes the
note by asking git for HEAD, and HEAD is whatever the tree happened to be at
the moment this runs. On 1.0.4 it ran while bundle/runtime, SYSTEM.md and a
product's CHECKSUMS.sha256 were regenerated on disk but NOT YET COMMITTED:
the note stamped the commit BEFORE those files, and all of it, note included,
landed in one later commit. Rebuilding the export from the revision the note
names then lacked exactly those files, and X7's second side (the tag against
its named source revision) read FAIL. So this tool now refuses to write
anything (exit 2, naming the paths) unless `git status --porcelain` shows
nothing beyond the two release files it is about to (re)write: the tree it
stamps into the note has to already be the tree everything else in the same
commit will carry. `--allow-dirty "reason"` overrides this, and the reason
is folded into the release note itself (docs/releases/<version>.notes.txt,
the hand written slot the note already reads), so a reader of the shipped
note sees why the stamped revision does not cover the whole tree rather than
being left to guess.

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


def release_paths(version):
    """The two files a write run is about to (re)write, relative to
    whichever root it runs against, in the form `git status --porcelain`
    prints them. The only paths a dirty tree may carry going into a
    refresh.

    Built directly (docs/releases/<version>.md and
    .../<version>.export-manifest.txt) rather than through
    RN.notes_path_for/manifest_write_path_for: those are joined against
    RN's own module-level ROOT, bound to the real hub at import time, so
    they always answer "where in the real hub", never "where under this
    root", which is what a test pointed at a throwaway repository needs."""
    return {
        os.path.join("docs", "releases", "%s.md" % version),
        os.path.join("docs", "releases", "%s.export-manifest.txt" % version),
    }


def _porcelain_path(line):
    """A porcelain line is '<XY> <path>', or '<XY> <path> -> <path2>' for a
    rename, where the path that matters is the one it will be found at
    after this status. Git quotes a path carrying a quote, a backslash or a
    non-ASCII byte in double quotes; strip that quoting rather than compare
    a quoted form against a plain one and miss the match."""
    rest = line[3:] if len(line) > 3 else line.strip()
    if " -> " in rest:
        rest = rest.split(" -> ", 1)[1]
    if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
        rest = rest[1:-1]
    return rest


def dirty_paths(root=EP.ROOT):
    """(paths, why): every path `git status --porcelain` names in root, or
    (None, why) when the status itself could not be read.

    `--untracked-files=all` is load bearing on a first-ever cut for a given
    version: with the default setting, git collapses a brand new
    docs/releases/ directory to one line naming the directory itself
    ("?? docs/"), which then compares equal to neither release path and
    refuses a genuinely clean tree. Asking for every file individually
    keeps the comparison exact."""
    proc = subprocess.run(["git", "status", "--porcelain",
                          "--untracked-files=all"], cwd=root,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None, ("NO-DATA: git status --porcelain exited %d in %s: %s"
                      % (proc.returncode, root,
                         (proc.stderr or "").strip()))
    return [_porcelain_path(l) for l in proc.stdout.splitlines()
            if l.strip()], ""


def notes_extra_path(version, root=EP.ROOT):
    """docs/releases/<version>.notes.txt: the hand written slot
    release_note_from_tree.py folds into the generated note (its
    extra_notes()). Built from `root` directly rather than imported from RN,
    because RN's own RELEASES_DIR is bound to RN's module-level ROOT, which
    a test redirecting `root` here would not move."""
    return os.path.join(root, "docs", "releases", "%s.notes.txt" % version)


def record_allow_dirty_reason(version, reason, root=EP.ROOT):
    """Fold an --allow-dirty reason into the note's own hand written extra
    paragraph, appended after anything already there rather than replacing
    it: that file can carry a founder-authored fact release_note_from_tree.py
    has no other way to measure, and an override reason is not a license to
    drop it."""
    path = notes_extra_path(version, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read().rstrip("\n")
    line = ("Refreshed over an uncommitted tree (--allow-dirty): %s"
           % reason.strip())
    body = (existing + "\n\n" + line) if existing else line
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")


def refuse_if_dirty(version, allow_dirty, root=EP.ROOT):
    """(exit_code_or_None, lines). None means proceed (lines are still
    informational and worth printing); an int means stop here with that
    code. `allow_dirty` is the --allow-dirty reason string, or None."""
    paths, why = dirty_paths(root)
    if paths is None:
        return EXIT_NODATA, [why]
    allowed = release_paths(version)
    unexpected = sorted(p for p in paths if p not in allowed)
    if not unexpected:
        return None, []
    if allow_dirty is None:
        return EXIT_NODATA, [
            "NO-DATA: refusing to stamp a note over an uncommitted tree: "
            "%s carries changes beyond the two files this run writes (%s); "
            "commit or stash them first, or pass --allow-dirty \"reason\" "
            "to override" % (root, ", ".join(unexpected))]
    if not allow_dirty.strip():
        return EXIT_NODATA, [
            "NO-DATA: --allow-dirty needs a one-line reason, e.g. "
            "--allow-dirty \"merged main, regen commit pending\""]
    record_allow_dirty_reason(version, allow_dirty, root)
    return None, [
        "allow-dirty: proceeding over an uncommitted tree (%s carries %s); "
        "reason recorded in %s"
        % (root, ", ".join(unexpected), notes_extra_path(version, root))]


def stage_release_files(version, root=EP.ROOT):
    """git add the two files just written, so build_export_tree's
    `git ls-files` (the INDEX, never the working tree alone) sees them for
    the check that follows this call."""
    paths = sorted(release_paths(version))
    proc = subprocess.run(["git", "add"] + paths, cwd=root,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return ["NO-DATA: git add %s exited %d in %s: %s"
               % (" ".join(paths), proc.returncode, root,
                  (proc.stderr or "").strip())]
    return ["staged %s so the check below reads what was just written"
           % " and ".join(paths)]


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
    ap.add_argument("--allow-dirty", metavar="REASON", default=None,
                     help="write anyway when the tree carries changes "
                          "beyond the two files this run writes; requires "
                          "a one-line reason, folded into the release note")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    version = args.version or RN.default_version()
    if not version:
        print("NO-DATA: no --version given and no version could be read "
              "from the marketplace manifest")
        return EXIT_NODATA

    if not args.check:
        refusal, lines = refuse_if_dirty(version, args.allow_dirty)
        for line in lines:
            print(line)
        if refusal is not None:
            return refusal
        ok, lines = regenerate(version)
        for line in lines:
            print(line)
        if not ok:
            return EXIT_NODATA
        for line in stage_release_files(version):
            print(line)

    code, lines = check(version)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
