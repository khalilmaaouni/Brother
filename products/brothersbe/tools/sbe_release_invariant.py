#!/usr/bin/env python3
"""The release invariant: distributable bytes cannot move without VERSION moving.

Why this file exists. VERSION, .claude-plugin/plugin.json and
.claude-plugin/marketplace.json can all agree with each other and still lie
about what a marketplace user is running, because none of them are tied to
the CONTENT being shipped. Claude Code's own marketplace behaviour is that a
user is offered an update only when the version STRING changes; a
commit-only change to tracked bytes is invisible to that mechanism. A
maintainer can fix a security bug in src/brothersbe/evidence.py, regenerate
CHECKSUMS.sha256 so scripts/test-install-artifact.sh stays green, leave
VERSION untouched, and ship a CI-green merge that an existing install never
learns about: it keeps running the OLD bytes under a version string that
still claims to be current.

CHECKSUMS.sha256 already guards against a manifest drifting from the bytes
it describes; it says nothing about whether the VERSION those bytes ship
under has moved. This gate closes that narrower gap and nothing wider: it
does not re-verify checksums, does not read what changed inside a file, and
does not judge whether a change was safe. It asks one mechanical question:
did anything a user runs change in this range, and if so, did VERSION
change in the same range. Two yeses is a release; one yes and one no is the
defect this project's own kill criterion names by name ("distributable
bytes change without a version change", the release-plan review's words).

The distributable set, named once so a change here is the only place this
gate's scope can drift: paths under src/, tools/, bin/, skills/, hooks/,
agents/, scripts/, plus install.sh and .claude-plugin/ (the plugin manifest
and marketplace listing, whose own version fields this gate protects).
Confirmed against the live tree by hand when this gate was written, and
re-confirmed on every test run so a later rename fails a fixture instead of
silently narrowing the scope (see test_every_declared_root_exists below).

Honesty law inherited from this project's other tooling: absent evidence is
NO-DATA, never PASS. A docs-only change, a workflow-only change, or two refs
with nothing between them all read NO-DATA, not PASS: silence about the code
is not evidence VERSION is right. FAIL is reserved for the one shape that is
provably wrong: distributable bytes moved and VERSION did not move with
them. A base ref this checkout never fetched (a shallow clone, a typo, a
remote that does not exist here) is NO-DATA carrying the reason, never a
crash and never a silent PASS, mirroring how tools/sbe_gate.py's own
`_current_head` treats "git could not answer" as nothing to check against.

One limit stated rather than left implicit: the default base is
`origin/main`, meaningful on a pull request (the PR head against the branch
it targets) and degenerate on a direct push to `main` (by the time CI checks
it out, `origin/main` already points at the pushed commit, so the diff is
empty and the verdict is NO-DATA, never a false PASS and never a missed
FAIL). NO-DATA never blocks --strict here, exactly as everywhere else in
this project.
"""
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import say

DEFAULT_BASE = "origin/main"

# The distributable roots, named once. Confirmed by hand against this
# repository's own top level on 2026-08-04 (every name below existed as a
# real directory or file at that commit) and re-confirmed mechanically on
# every test run by tools/test_sbe_release_invariant.py's
# test_every_declared_root_exists_in_this_repository, so a directory renamed
# or removed later fails a fixture instead of silently narrowing this gate's
# scope. A directory here that does not exist in the tree this gate ships
# beside is a bug in this list, not a fact about the repository.
DISTRIBUTABLE_DIRS = ("src", "tools", "bin", "skills", "hooks", "agents", "scripts", ".claude-plugin")
DISTRIBUTABLE_FILES = ("install.sh",)


def _is_distributable(path):
    """True when `path` (a repo-root-relative, forward-slash git path) is
    bytes a user runs or a plugin manifest a marketplace reads.

    Matched by the FIRST path segment against DISTRIBUTABLE_DIRS, or by exact
    name against DISTRIBUTABLE_FILES, mirroring how tools/sbe_checks.py's own
    skip_reason and _top_level read a git-relative path: by its leading
    component, never by a substring, so `tools-legacy/` (a real sibling
    directory this scope must NOT cover) does not collide with `tools/`.
    """
    path = path.strip()
    if not path:
        return False
    if path in DISTRIBUTABLE_FILES:
        return True
    head = path.split("/", 1)[0]
    return head in DISTRIBUTABLE_DIRS


def _named_few(items, limit=12):
    """A bounded list that states how much it left out rather than hiding it.

    Mirrors tools/sbe_checks._named_few's contract (same shape, same
    "and N more" tail) without importing a name that module marks private
    with a leading underscore; the two are free to drift independently
    because they bound two different report shapes for two different
    audiences (a directory list there, a file list here).
    """
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return "%s, and %d more" % (", ".join(items[:limit]), len(items) - limit)


def _git(root, *args):
    """Run one git subcommand against `root`, returning (ok, stdout, stderr).

    `ok` is the checked, surfaced return code, never assumed: every call site
    below reads it and turns a nonzero code into a named NO-DATA reason
    rather than treating stdout as meaningful when git refused to produce it.
    The only swallowed exception is "git is not on PATH at all" (OSError),
    which is not silent either: it becomes the stderr text the caller prints.
    """
    try:
        result = subprocess.run(
            ["git", "-C", root] + list(args), capture_output=True, text=True)
    except OSError as e:
        return False, "", "git could not be run here (%s: %s)" % (type(e).__name__, e)
    return result.returncode == 0, result.stdout, (result.stderr or "").strip()


def check(root, base):
    """(verdict, evidence) for the release invariant over `root`, `base`..HEAD.

    Three ways in to NO-DATA, each named because each is a different reason
    this gate has nothing to grade: `root` is not a git working tree at all;
    HEAD does not resolve (an unborn checkout); `base` does not resolve here
    (the ordinary shape of a shallow clone or a mistyped ref). A fourth,
    "nothing distributable changed", is the gate's own central law rather
    than a git failure: silence about the code is not evidence the version
    is right, so it is never read as a pass.
    """
    ok, _out, err = _git(root, "rev-parse", "--is-inside-work-tree")
    if not ok:
        return "NO-DATA", ("%s is not a git working tree here (%s), so no range could be "
                           "compared" % (root, err or "git refused"))
    ok, head_out, err = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    head = head_out.strip()
    if not ok or not head:
        return "NO-DATA", ("HEAD does not resolve to a commit in %s (%s); an unborn checkout "
                           "has nothing to compare" % (root, err or "no commits yet"))
    ok, base_out, err = _git(root, "rev-parse", "--verify", "--quiet", base + "^{commit}")
    base_commit = base_out.strip()
    if not ok or not base_commit:
        return "NO-DATA", ("base ref %r does not resolve in %s (%s); a shallow checkout or a "
                           "ref this clone never fetched reads as unknown, never as a silent "
                           "pass over a range nobody could compute" % (base, root, err or "unknown revision"))
    ok, diff_out, err = _git(root, "diff", "--name-only", base_commit, head)
    if not ok:
        return "NO-DATA", ("git diff between %s and %s failed in %s (%s), so the changed-file "
                           "set is unknown" % (base_commit[:12], head[:12], root, err or "git refused"))
    changed = sorted({line.strip() for line in diff_out.splitlines() if line.strip()})
    distributable = [f for f in changed if _is_distributable(f)]
    version_changed = "VERSION" in changed
    span = "%s..%s" % (base_commit[:12], head[:12])
    if not distributable:
        return "NO-DATA", ("no distributable path changed over %s (%d file(s) changed in all); "
                           "absence of a distributable change is not evidence VERSION is right, "
                           "so this is not a PASS either" % (span, len(changed)))
    if version_changed:
        return "PASS", ("%d distributable file(s) changed over %s and VERSION changed in the "
                        "same range (%s)" % (len(distributable), span, _named_few(distributable)))
    return "FAIL", ("%d distributable file(s) changed over %s but VERSION did not move in the "
                    "same range: %s; a marketplace user is offered an update only when the "
                    "version STRING changes, so these bytes would ship as if unchanged"
                    % (len(distributable), span, _named_few(distributable)))


from sbe_checks import Check as _Check
CHECKS = {"release-invariant": _Check(
    check, reads=("VERSION",), kind="git", severity="gate", empty_expect="NO-DATA",
    empty_fixture="",
    full_fixture={"files": {"VERSION": "1.0.0\n"}, "git": {"message": "test"}},
    full_expect="NO-DATA", full_expect_reason="experiment")}

USAGE = (
    "usage: sbe_release_invariant.py [directory] [base-ref] [--strict]\n"
    "  reads: git history under the directory (default .); opens no working file.\n"
    "  writes: nothing.\n"
    "  directory  the git working tree to check (default: .)\n"
    "  base-ref   the ref to diff against HEAD (default: %s)\n"
    "  flags:\n"
    "    --strict    make a FAIL block the exit code\n"
    "    -h, --help  print this and exit 0 without examining anything"
) % DEFAULT_BASE


def main():
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        for line in USAGE.splitlines():
            say(line)
        sys.exit(0)
    strict = "--strict" in argv
    positional = [a for a in argv if a != "--strict"]
    roots, bases = [], []
    for a in positional:
        if a.startswith("-"):
            for line in USAGE.splitlines():
                say(line)
            say("sbe_release_invariant: unrecognized flag %r; refusing rather than running "
                "past it" % a)
            sys.exit(2)
        elif os.path.isdir(a):
            roots.append(a)
        else:
            bases.append(a)
    if len(roots) > 1:
        say("sbe_release_invariant: one directory at a time, got %d (%s)."
            % (len(roots), ", ".join(roots)))
        sys.exit(1)
    if len(bases) > 1:
        say("sbe_release_invariant: one base ref at a time, got %d (%s)."
            % (len(bases), ", ".join(bases)))
        sys.exit(1)
    root = roots[0] if roots else "."
    base = bases[0] if bases else DEFAULT_BASE
    verdict, evidence = check(root, base)
    say("BROTHERSBE RELEASE INVARIANT  (advisory unless --strict; NO-DATA is never a pass)")
    say("  %-18s %-8s %s [severity: gate]" % ("release-invariant", verdict, evidence))
    if strict and verdict == "FAIL":
        say("STRICT: release invariant failed; exiting nonzero to block the merge.")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # A broken gate must not silently pass work. In strict mode a crash blocks,
        # mirroring tools/sbe_gate.py's own top-level guard.
        say("sbe_release_invariant: error %r" % (e,))
        sys.exit(1 if "--strict" in sys.argv else 0)
