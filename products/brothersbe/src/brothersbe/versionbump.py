"""Move every version declaration site in one command, and verify they agree.

Why this exists: parity row PT-2. Three release candidates in one night were
bumped by hand across four files (VERSION, .claude-plugin/plugin.json,
.claude-plugin/marketplace.json twice, DIGEST.md line 1), and every manual
pass is one typo away from the release invariant refusing the seal. One
command edits all the sites the invariant reads, from one argument, and then
re-reads every site to prove they now agree, because an edit that is not
re-read is a claim rather than a fact.

What this command deliberately does NOT do, printed as reminders instead of
performed: the CHANGELOG heading (prose a human or the sealing orchestrator
writes), `evals/replay_book.py --write` (book echoes regenerate from live
runs, never from a substitution), and CHECKSUMS.sha256 (regenerated LAST in
the seal order, after git add). Doing those here would hide three steps that
each have their own failure modes behind one that looks atomic.

Refusal semantics, in the house shape: a malformed target version is refused
by name (the accepted shape is the release invariant's own v-digit form,
without the leading v). Sites that ALREADY disagree are refused with every
site and its current value named, because bumping over a disagreement would
bury the evidence of whatever went wrong. A target equal to the current
version is refused as a no-op rather than reported as success, because a
command that succeeds at nothing is the failure this project exists to stop.
`--dry-run` shows every intended edit and writes nothing.
"""
import json
import os
import re
import sys

from . import cwd as cwd_mod

#: The accepted shape: MAJOR.MINOR.PATCH with an optional -rc.N tail, the
#: same population the release invariant's v-digit tag filter selects from.
VERSION_SHAPE = re.compile(r"^\d+\.\d+\.\d+(-rc\.\d+)?$")

#: Every declaration site the release invariant and the docs treat as live.
#: marketplace.json carries the version twice (the marketplace entry and the
#: plugin entry), which is exactly why a single-file mental model fails.
SITES = ("VERSION", ".claude-plugin/plugin.json",
         ".claude-plugin/marketplace.json", "DIGEST.md")

#: The steps a release still owes after this command, in the order they must be
#: run. This list is not documentation: it is the ONLY place a releaser is told
#: what remains, so a step missing from here is a step that does not happen.
#:
#: The sandbox-guide line was added 2026-08-24 after the 3.4.0 release followed
#: this list exactly and still failed the gate battery at command 42 of 52.
#: docs/guides/00-sandbox.md quotes two commit hashes from a DETERMINISTIC driven
#: run, and that run builds from this repository's own files, so moving the
#: version moves the tree and moves the hash. The guide's own prose predicts this
#: ("that hash moves whenever those files move, for example at a version bump")
#: and tools/regen_sandbox_guide.py exists to repin it. The only gap was that
#: nothing told the releaser to run it.
#:
#: The `sbe book` line was added 2026-08-24, by the 3.4.1 battery, one command
#: after the sandbox repin that this same list had just gained. The booklet's
#: provenance section is RENDERED FROM VERSION, so moving the version staled it
#: and `sbe book --check --strict` failed the battery at command 21 of 52 with
#: "the section 'provenance' was rendered from VERSION, which has changed
#: since". Note this is NOT the same command as the replay_book line above it:
#: replay_book patches echoes of live OUTPUT into the chapters, while `sbe book`
#: rebinds generated SECTIONS to their sources. Running one does not do the
#: other, and the list had only ever named one of them.
#:
#: The lesson this list keeps paying for: a checklist that is the only place a
#: releaser is told what remains is only ever as complete as the last failure
#: that taught it. Adding one missing step revealed the next missing step of
#: exactly the same shape, one release later.
#:
#: It sits BEFORE the checksums line because checksums must stay last: the repin
#: edits a tracked file, so a manifest generated before it would ship stale.
REMINDERS = (
    "CHANGELOG.md: add the new heading yourself; prose is not substituted",
    "evals/replay_book.py --write: regenerate book echoes from live runs",
    "sbe book: rebind the booklet's generated sections, whose provenance "
    "section is rendered FROM VERSION and goes stale the moment it moves",
    "tools/regen_sandbox_guide.py: repin the sandbox guide's head hashes, which "
    "a version bump always moves",
    "scripts/checksums.sh CHECKSUMS.sha256: LAST, after git add",
)


def _read(root, rel):
    # encoding= is not optional here, and this file was the one place in the
    # package that omitted it. Without it Python reads in the process locale's
    # encoding, and a UTF-8 byte sequence then goes wrong in one of two ways,
    # both checked against the real codecs rather than assumed: a strict
    # locale (the C locale, and the sequences code page 932 rejects) raises
    # an unhandled UnicodeDecodeError, so the release command dies with a
    # traceback where it owes a refusal; a permissive page (1252, and 932 for
    # many sequences) silently mis-decodes to mojibake, which corrupts the
    # text with no traceback at all. Reproduced on POSIX under LC_ALL=C,
    # which resolves to ASCII and takes the raising path; see
    # TestNonUtf8CodePage in tools/test_sbe_version_bump.py.
    with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def _site_versions(root):
    """Read every declared version, one list entry per DECLARATION (so
    marketplace.json contributes two), each as (site-label, value)."""
    found = []
    found.append(("VERSION", _read(root, "VERSION").strip()))
    plugin = json.loads(_read(root, ".claude-plugin/plugin.json"))
    found.append((".claude-plugin/plugin.json version", plugin.get("version")))
    market = json.loads(_read(root, ".claude-plugin/marketplace.json"))
    market_versions = []
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "version" and isinstance(value, str):
                    market_versions.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(market)
    for index, value in enumerate(market_versions):
        found.append((".claude-plugin/marketplace.json version #%d" % (index + 1), value))
    digest_first = _read(root, "DIGEST.md").splitlines()[0] if _read(root, "DIGEST.md") else ""
    match = re.search(r"version (\d+\.\d+\.\d+(?:-rc\.\d+)?)", digest_first)
    found.append(("DIGEST.md line 1", match.group(1) if match else None))
    return found


def _apply(root, old, new, dry_run):
    """Rewrite each site by exact-string substitution of the OLD version,
    which can only be reached after the agreement check, so the substitution
    is unambiguous by construction. Returns the list of edited sites."""
    edited = []
    for rel in SITES:
        before = _read(root, rel)
        if rel == "VERSION":
            after = new + "\n"
        elif rel == "DIGEST.md":
            lines = before.splitlines(True)
            lines[0] = lines[0].replace("version " + old, "version " + new)
            after = "".join(lines)
        else:
            after = before.replace('"version": "%s"' % old, '"version": "%s"' % new)
        if before != after:
            if not dry_run:
                # The write half of the same defect: a locale-encoded write
                # would mangle or refuse the very bytes _read just decoded.
                with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                    fh.write(after)
            edited.append(rel)
    return edited


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    argv, cwd = cwd_mod.split(list(rest))
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if len(argv) != 2 or argv[0] != "bump":
        sys.stderr.write(
            "usage: sbe version bump <new-version> [--dry-run] [--cwd <repo>]\n"
            "  <new-version> shape: MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rc.N, no leading v\n"
            "  --cwd <repo>  the repository whose declaration sites are edited (default: "
            "the current directory)\n"
            "  edits: %s (marketplace carries the version twice)\n"
            "  never edits: CHANGELOG.md, book echoes, CHECKSUMS.sha256 (printed as reminders)\n"
            % ", ".join(SITES))
        return exit_usage
    new = argv[1]
    if not VERSION_SHAPE.match(new):
        sys.stderr.write("version bump REFUSED: %r does not match the accepted shape "
                         "MAJOR.MINOR.PATCH[-rc.N] (no leading v)\n" % new)
        return exit_usage

    root = os.path.abspath(cwd) if cwd else os.getcwd()
    for rel in SITES:
        if not os.path.isfile(os.path.join(root, rel)):
            sys.stderr.write("version bump REFUSED: %s is missing under %s; run from the "
                             "repository root that carries all four declaration sites\n"
                             % (rel, root))
            return exit_failed

    sites = _site_versions(root)
    values = set(value for _, value in sites)
    if None in values or len(values) != 1:
        sys.stderr.write("version bump REFUSED: the declaration sites already disagree, and "
                         "bumping over a disagreement would bury its evidence. Reconcile these "
                         "first:\n")
        for label, value in sites:
            sys.stderr.write("  %-45s %s\n" % (label, value if value is not None else "UNREADABLE"))
        return exit_failed
    old = values.pop()
    if old == new:
        sys.stderr.write("version bump REFUSED: every site already reads %s; a bump to the "
                         "same version succeeds at nothing\n" % new)
        return exit_failed

    edited = _apply(root, old, new, dry_run)
    verb = "would edit" if dry_run else "edited"
    sys.stdout.write("version bump %s -> %s: %s %d file(s): %s\n"
                     % (old, new, verb, len(edited), ", ".join(edited)))

    if not dry_run:
        after = _site_versions(root)
        wrong = [(label, value) for label, value in after if value != new]
        if wrong:
            sys.stderr.write("version bump FAILED its own re-read: these sites do not read "
                             "%s after the edit: %s\n"
                             % (new, "; ".join("%s=%s" % pair for pair in wrong)))
            return exit_failed
        sys.stdout.write("re-read: all %d declaration(s) now read %s\n" % (len(after), new))

    sys.stdout.write("NOT done by this command, in seal order:\n")
    for line in REMINDERS:
        sys.stdout.write("  - %s\n" % line)
    return exit_ok
