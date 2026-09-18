#!/usr/bin/env python3
"""release_claim_reproduction: prove a release claim from a FRESH, independent
git clone, never from the same working tree, a worktree add, or an in-place
git init.

THE GAP THIS CLOSES. e80_release_reproduction_drive.py builds a fixture
release with `git init` in a temp directory and then runs
scripts/reproduce_export.py IN THAT SAME DIRECTORY. Its own test suite says
this plainly (test_e80_release_reproduction_drive.py): "It does NOT prove
that a real git clone actually reproduces or that reproduce_export.py
actually catches a real tamper: that is only proven by running the module's
own main() for real, which is what this repo's release done-check does
separately." Even that real run of e80's main() never leaves the directory
it committed the fixture into: there is no second, independent copy of the
object database anywhere in that chain. A stranger checking a real release
does not have the exporter's working tree; they have `git clone` and nothing
else. This module is that stranger.

THE DECIDING PROPERTY (docs/plan/ORCH-1020-WBS.json, unit DOM-10.07): a
release claim, tag X at commit Y with manifest digest Z, passes only when a
FRESH CLONE of the repository, made with `git clone` and never a worktree
add or an in-place init, reproduces digest Z at tag X. A missing tag, an
unreachable clone, a commit that does not match Y, or a digest that does not
match Z is FAIL or NO-DATA. It is never PASS. Neither is an export manifest
that names zero files: a check that passes over an empty file list has
proven nothing about reproduction.

WHAT IS REUSED, NOTHING REWRITTEN. reproduce_export.py already knows how to
read a tag's own manifest and recompute it byte for byte
(verify_tree/manifest_path_for/tag_file_bytes/parse_manifest); this module
imports it and never restates that logic. It is run the SAME way a stranger
holding only the clone would run it: as a subprocess of the CLONE'S OWN copy
of the script (scripts/reproduce_export.py inside the fresh clone), never
this checkout's copy, so a change made only to the release being verified
(never to the reviewer's own tree) is what gets exercised.

Exit codes: 0 PASS, 1 FAIL (the claim is false), 2 NO-DATA (the claim could
not be checked at all: no clone, no tag, no manifest). Python 3, standard
library only, no network (a `git clone` of a local path or a file:// URL
never touches one).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reproduce_export as RE  # noqa: E402


def run(cmd, cwd=None, timeout=180):
    """The one place this module shells out. Returns a
    subprocess.CompletedProcess with text output; never raises on a nonzero
    exit (callers decide what that means), but a command that could not even
    start, or hung past `timeout`, is an environment failure, not a verdict
    about the claim, so it raises rather than being read as a FAIL."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not run %s: %s" % (" ".join(cmd), exc)) from exc


def fresh_clone(repo, dest):
    """A real `git clone` of `repo` into `dest`, never `git worktree add` and
    never `git init` plus a manual copy: those two share the source
    checkout's own object database or working tree, so a defect in THAT copy
    (a stray local change, an uncommitted edit, a ref nobody pushed) reaches
    the check for free. `--no-hardlinks` also rules out the local clone
    optimisation that shares blob files with the source by hardlink: every
    byte this reads back comes from `dest`'s own copy. (ok, detail)."""
    proc = run(["git", "clone", "--no-hardlinks", "--quiet", repo, dest])
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()
    return True, ""


def resolve_tag_commit(clone_dir, tag):
    """The commit `tag` resolves to inside `clone_dir`, read from the clone's
    OWN refs, never the source repo's. None when the clone does not carry
    the tag at all: an unreachable clone, exactly one of the three named
    non-pass states."""
    proc = run(["git", "-C", clone_dir, "rev-list", "-n", "1",
                "%s^{commit}" % tag])
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def manifest_entry_count(clone_dir, tag):
    """How many files the tag's OWN export manifest names, read straight off
    the tag with reproduce_export.py's own reader (never the subprocess's
    printed prose, which is meant for a human, not a second check). None
    when the manifest is absent or unreadable (NO-DATA); this is also where
    an empty manifest is caught before it ever reaches the subprocess below.
    parse_manifest already treats zero rows as unparseable rather than an
    empty-but-valid list (`return out or None`), so this returns None for a
    manifest with no lines, not 0. See test_empty_manifest_is_never_a_pass
    for the row this exists to refuse."""
    version = tag.lstrip("v")
    rel_manifest = RE.manifest_path_for(version)
    data = RE.tag_file_bytes(tag, rel_manifest, public=clone_dir)
    if data is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    entries = RE.parse_manifest(text)
    return None if entries is None else len(entries)


def run_verify_tree(clone_dir, tag, digest):
    """The clone's OWN copy of scripts/reproduce_export.py, run with
    --verify-tree and --expect pinned to the claimed digest Z, exactly the
    command a stranger holding only this clone would run, from inside it,
    with no access to whatever repository this module itself lives in.
    Returns (code, output) with code None when the clone carries no such
    script at all (NO-DATA, named in `output`)."""
    script = os.path.join(clone_dir, "scripts", "reproduce_export.py")
    if not os.path.isfile(script):
        return None, ("NO-DATA: the fresh clone carries no "
                       "scripts/reproduce_export.py to verify itself with")
    proc = run([sys.executable, script, "--verify-tree", "--tag", tag,
                "--expect", digest], cwd=clone_dir)
    return proc.returncode, (proc.stdout or "").strip()


def verify_claim(repo, tag, digest, commit=None, workdir=None):
    """The deciding property, end to end: a release claim (tag `tag` at
    commit `commit`, manifest digest `digest`) passes only when a FRESH,
    independent `git clone` of `repo` reproduces `digest` at `tag`. Returns
    (code, lines): 0 PASS, 1 FAIL, 2 NO-DATA. `commit` is optional (a caller
    checking only the digest half of the claim may omit it); when given, a
    tag that resolves to a different commit in the fresh clone is FAIL, not
    a silent pass on the digest alone."""
    dest = tempfile.mkdtemp(prefix="release-claim-clone-", dir=workdir)
    try:
        ok, err = fresh_clone(repo, dest)
        if not ok:
            return 2, ["NO-DATA: could not make a fresh clone of %s: %s"
                        % (repo, err)]

        resolved = resolve_tag_commit(dest, tag)
        if resolved is None:
            return 2, ["NO-DATA: %s does not resolve to a commit in a fresh "
                        "clone of %s; the tag is unreachable there" % (tag, repo)]

        if commit is not None and resolved != commit:
            return 1, ["FAIL: %s resolves to %s in the fresh clone, but the "
                        "claim names commit %s" % (tag, resolved, commit)]

        count = manifest_entry_count(dest, tag)
        if not count:
            return 2, ["NO-DATA: %s carries no readable, non-empty export "
                        "manifest in the fresh clone; an empty file list is "
                        "never a reproduction" % tag]

        code, out = run_verify_tree(dest, tag, digest)
        lines = out.splitlines() if out else []
        if code is None:
            lines.append(out)
            return 2, lines
        if code == 2:
            lines.append("NO-DATA: the clone's own reproduce_export.py could "
                          "not verify %s" % tag)
            return 2, lines
        if code != 0:
            lines.append("FAIL: the clone's own reproduce_export.py did not "
                          "reproduce digest %s for %s" % (digest, tag))
            return 1, lines
        lines.append("PASS: a fresh, independent clone of %s at %s (commit "
                      "%s) reproduces manifest digest %s over %d file(s)"
                      % (repo, tag, resolved, digest, count))
        return 0, lines
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True,
                     help="the repository to clone fresh: a local path or a "
                          "git URL, never the working tree running this "
                          "script")
    ap.add_argument("--tag", required=True, help="the release tag, e.g. v1.0.1")
    ap.add_argument("--digest", required=True,
                     help="the manifest digest Z the release claim states")
    ap.add_argument("--commit", default=None,
                     help="the commit Y the release claim states the tag "
                          "names; when given, the fresh clone's own "
                          "resolution of the tag must match it exactly")
    args = ap.parse_args(argv)
    code, lines = verify_claim(args.repo, args.tag, args.digest,
                                commit=args.commit)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
