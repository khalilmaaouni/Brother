#!/usr/bin/env python3
"""changelog_from_commits.py: a changelog generated from git history, never
hand written.

WHY THIS EXISTS. docs/plan/RELEASE-POLICY.md's cadence section says the
changelog is generated from the commits since the previous tag rather than
hand written (row S29); nothing in this tree did that before this file.
Reads local git history between two refs, the same "git -C ROOT ..." shape
scripts/release_note_from_tree.py already reads git in: no network, stdlib
and a git subprocess only.

WHAT COUNTS AS AN ENTRY. A merge commit whose subject is GitHub's own
"Merge pull request #N from <owner>/<branch>" shape (`git log --merges`) is
a landed pull request; a plain commit or a "Merge main into <branch>"
housekeeping merge names no pull request and is skipped, per the row's own
wording "pull request titles" (this tree has no network access to GitHub's
actual PR title, so the merge subject, which GitHub writes from that title
by default, is the checkable proxy).

GROUPING. A merge's branch is checked for a roadmap row id (row ids in
docs/plan/READINESS-ROADMAP-2026-08-29.json look like S24, X8, P1-4:
letters then digits, optionally a hyphenated suffix) at the start of the
slug after a `wbs/` or `fix/` prefix (`wbs/s24-codex-guide` -> S24,
`wbs/x8-rows-1.0.8` -> X8, `wbs/p1-4-competitive-harness` -> P1-4). A
branch that carries no such id groups under its own leading path segment
(the wbs prefix itself: `wbs`, `fix`, `reland`); anything matching neither
groups under "Other".

USAGE (from the hub root):
    python3 scripts/changelog_from_commits.py <from-ref> <to-ref>

Prints one NO-DATA line and exits 2 when either ref cannot be resolved or
the range holds no pull request merges at all; never a stack trace, never a
silent empty changelog. Exit 0 otherwise, changelog on stdout. Python 3.9,
standard library only.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODATA = "NO-DATA"

#: GitHub's own default merge-commit subject for a merged pull request.
MERGE_RE = re.compile(r"^Merge pull request #(\d+) from [^/]+/(.+)$")
#: A roadmap row id (letters then digits, optionally -digits) at the start
#: of a wbs/fix slug, e.g. "s24-codex-guide" -> "s24", "p1-4-thing" -> "p1-4".
ROW_ID_RE = re.compile(r"^([a-z]{1,4}\d+(?:-\d+)*)-", re.IGNORECASE)
#: A public release tag name, e.g. "v1.0.9" -> "1.0.9".
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
#: The line scripts/release_note_from_tree.py's build() writes for the hub
#: commit a tag was cut from (docs/releases/<version>.md), same pattern as
#: scripts/release_closeout.py's SOURCE_REV_RE. Not imported from there:
#: that module pulls in codex_smoke, virgin_unit_proof and export_public at
#: import time, a heavy transitive load for one regex.
SOURCE_REV_RE = re.compile(r"Cut from hub commit `([0-9a-f]{7,40})`")


def ref_exists(ref, root=ROOT):
    """True when `ref` resolves to a commit in `root`'s repository."""
    proc = subprocess.run(
        ["git", "-C", root, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def merge_entries(from_ref, to_ref, root=ROOT):
    """[(pr_number, branch), ...] for every pull-request merge in
    `from_ref..to_ref`, oldest first, or None when the range itself could
    not be read (a bad ref git itself did not refuse to resolve, or some
    other git failure)."""
    proc = subprocess.run(
        ["git", "-C", root, "log", "--merges", "--reverse", "--pretty=%s",
         "%s..%s" % (from_ref, to_ref)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        return None
    entries = []
    for line in proc.stdout.splitlines():
        m = MERGE_RE.match(line.strip())
        if m:
            entries.append((int(m.group(1)), m.group(2)))
    return entries


def source_rev_for_tag(ref, root=ROOT):
    """(rev, why): the hub commit docs/releases/<version>.md names as the
    source of tag `ref` (DEL-13: a public tag is an orphan export commit
    sharing no history with the hub, so `ref..HEAD` subtracts nothing when
    the tag is even present; the release note's own "Cut from hub commit"
    stamp is the checkable pointer back into real hub history). None with a
    reason when `ref` is not a "vX.Y.Z" tag name, the note does not exist,
    it names no revision in the form the generator writes, or that revision
    does not resolve to a commit in `root`; the caller falls back to `ref`
    itself unchanged in every one of those cases."""
    m = TAG_RE.match(ref)
    if not m:
        return None, "%r is not a vX.Y.Z release tag" % ref
    note = os.path.join(root, "docs", "releases", "%s.md" % m.group(1))
    try:
        with open(note, "r", encoding="utf-8") as fh:
            body = fh.read()
    except OSError as exc:
        return None, "%s: %s" % (note, exc)
    found = SOURCE_REV_RE.search(body)
    if not found:
        return None, "%s names no source revision in the form the " \
                     "generator writes" % note
    rev = found.group(1)
    if not ref_exists(rev, root):
        return None, "%s names %s, which does not resolve to a commit here" \
                     % (note, rev)
    return rev, os.path.relpath(note, root)


def group_key(branch):
    """The changelog section a merged branch's pull request falls under."""
    core = branch[len("reland/"):] if branch.startswith("reland/") else branch
    prefix, sep, slug = core.partition("/")
    if not sep:
        return "Other"
    m = ROW_ID_RE.match(slug)
    if m:
        return m.group(1).upper()
    return prefix


def changelog_lines(from_ref, to_ref, root=ROOT):
    """The changelog body as a list of lines (group headings and bullets,
    no leading top-level title), or a single-element list carrying one
    NO-DATA line when a ref is missing or the range holds no pull-request
    merges. Never raises; a caller wiring this into a larger document
    decides how loudly that one line is treated."""
    resolved_rev, resolved_note = source_rev_for_tag(from_ref, root)
    if resolved_rev is not None:
        git_from = resolved_rev
        header = ("Generated from hub revision %s (the source revision %s "
                   "names for %s)." % (resolved_rev[:12], resolved_note, from_ref))
    else:
        git_from = from_ref
        header = None

    if not ref_exists(git_from, root):
        return ["%s: ref %r does not exist" % (NODATA, git_from)]
    if not ref_exists(to_ref, root):
        return ["%s: ref %r does not exist" % (NODATA, to_ref)]
    entries = merge_entries(git_from, to_ref, root)
    if entries is None:
        return ["%s: git log %s..%s failed" % (NODATA, git_from, to_ref)]
    if not entries:
        return ["%s: no pull request merges between %s and %s"
                % (NODATA, git_from, to_ref)]

    groups = {}
    order = []
    for pr, branch in entries:
        key = group_key(branch)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((pr, branch))

    lines = []
    if header:
        lines.append(header)
        lines.append("")
    for key in sorted(order):
        lines.append("## %s" % key)
        for pr, branch in groups[key]:
            lines.append("- #%d %s" % (pr, branch))
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def build(from_ref, to_ref, root=ROOT):
    """(text, exit_code) for standalone use: a top-level heading plus
    changelog_lines()'s body, or that body's own single NO-DATA line (exit
    2) when the range holds nothing to changelog."""
    body = changelog_lines(from_ref, to_ref, root)
    if len(body) == 1 and body[0].startswith(NODATA):
        return body[0] + "\n", 2
    text = "\n".join(["# Changelog %s..%s" % (from_ref, to_ref), ""] + body)
    return text.rstrip("\n") + "\n", 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("from_ref", help="the previous public tag, e.g. v1.0.8")
    ap.add_argument("to_ref", help="the tip to changelog up to, usually HEAD")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    text, code = build(args.from_ref, args.to_ref)
    print(text, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
