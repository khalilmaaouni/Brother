#!/usr/bin/env python3
"""generic_skills_adapter: DOM-50.08, one capability registry decides what
frontmatter every host's projected SKILL.md carries, so a new host is a
data row here, not a new hand written generator.

THE PROBLEM. scripts/codex_skills.py hardcodes ACCEPTED_KEYS = ("name",
"description") as Codex's own accepted frontmatter keys, discovered by
reading Codex's own validator (see that module's docstring). That fact is
real and earned; the risk is that the next host's generator writes its
own accepted-keys constant instead of reading this one, and the two
quietly drift apart the next time either validator changes. CAPABILITY_
REGISTRY below is the one place that fact lives for every host this
estate has an adapter for; the codex entry below IS codex_skills.
ACCEPTED_KEYS, imported, not retyped, so an edit to that tuple in
codex_skills.py changes this registry in the same edit, because there is
only one tuple object, never two copies of the same two strings.

THE REGISTRY. host name -> the frontmatter keys that host's skill loader
accepts, or None when the host reads bundle/skills verbatim (no
projection at all: Claude and Cursor both read bundle/skills directly,
recorded in scripts/client_parity.py's own EXEMPT entry for bundle/
codex-skills, "Cursor reads bundle/skills directly through its
manifest").

WHAT THIS MODULE DOES NOT CLAIM. It does not replace codex_skills.py,
codex_surface.py or codex_product_skills.py. build_for_host() reuses
codex_skills.build() for the actual projection, the same function
codex_skills.py's own generate()/check() call; this module is the
generic caller, never a second implementation of frontmatter stripping.

EDGE CASES NAMED, not left implicit (adversarial review round, 2026-09-18):
an unrecognised host raises ValueError, never a permissive default; an
unreadable source tree is reported with a checked count of 0, never read
as zero drift; a missing or non-directory dest_dir is one problem line,
never silently "clean" because nothing was compared; a non-string or
empty dest_dir is refused before any path join, so it can never resolve
to the current working directory by accident; a file that exists but
cannot be opened (permission denied) is reported as unreadable, not
misreported as missing, by classifying on the open() call itself rather
than trusting os.path.isfile's False-on-any-OSError behaviour; an
unreadable subdirectory under dest_dir during the walk is reported as its
own problem line rather than silently skipped, via an explicit os.walk
onerror callback.

Python 3, standard library only. No network.
No em or en dashes anywhere in this file, its comments or its output.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import codex_skills  # noqa: E402

NODATA = "NO-DATA"

#: THE ONE CAPABILITY REGISTRY. See module docstring. A host missing from
#: this mapping is unknown input (worker contract rule 2): every function
#: below raises ValueError rather than guessing a default for it.
CAPABILITY_REGISTRY = {
    "claude": None,
    "cursor": None,
    "codex": codex_skills.ACCEPTED_KEYS,
}

#: host -> the generated tree its projection is checked against, relative
#: to a repo root, as path parts. A host absent here has no committed
#: generated tree to drift-check (Claude and Cursor read bundle/skills
#: directly, so there is nothing generated to compare against).
GENERATED_DIR = {
    "codex": ("bundle", "codex-skills"),
}


def _entry_for(host):
    """CAPABILITY_REGISTRY[host], or raise ValueError naming host.

    Catches TypeError from an unhashable host (a list, a dict) and
    re-raises it as the same ValueError an unrecognised-but-hashable host
    gets, so every caller can catch one exception type for "not a known
    host" regardless of what shape the bad value took."""
    try:
        known = host in CAPABILITY_REGISTRY
    except TypeError:
        known = False
    if not known:
        raise ValueError(
            "generic_skills_adapter: unknown host %r, not one of %s"
            % (host, ", ".join(sorted(CAPABILITY_REGISTRY))))
    return CAPABILITY_REGISTRY[host]


def build_for_host(host, source_dir=None):
    """(files, problems): the whole intended generated tree for `host`,
    computed from bundle/skills and CAPABILITY_REGISTRY[host] alone.

    host not in CAPABILITY_REGISTRY raises ValueError.

    A host whose registry entry is None (reads bundle/skills verbatim)
    has no generated tree at all: this returns ({}, []).

    A host with an accepted-keys tuple reuses codex_skills.build() with
    that tuple; an unreadable or malformed source returns the problems
    codex_skills.build() already reports. This function invents nothing
    further."""
    accepted = _entry_for(host)
    if accepted is None:
        return {}, []
    return codex_skills.build(source_dir, accepted=accepted)


def _default_dest_dir(host):
    rel = GENERATED_DIR.get(host)
    if rel is None:
        return None
    return os.path.join(REPO_ROOT, *rel)


def check_for_host(host, source_dir=None, dest_dir=None):
    """(problems, checked): does the committed generated tree for `host`
    match a fresh build_for_host(host) right now. Read-only, writes
    nothing.

    host not in CAPABILITY_REGISTRY raises ValueError.

    A host whose registry entry is None is not a projection at all
    (bundle/skills IS its generated tree, there is nothing derived to
    compare); this returns one explanatory line and a checked count of 0,
    never an empty list, since empty would read as "verified clean".

    dest_dir defaults to GENERATED_DIR[host] under the repo root. A host
    with neither an explicit dest_dir nor a GENERATED_DIR entry, or a
    dest_dir that is not a non-empty string, is reported the same way:
    one problem line, checked count 0. A non-directory or missing
    dest_dir is likewise one problem line, never a silent pass, so a
    wiped-out generated tree with an empty source cannot read as clean.

    problems is empty only when every expected file exists on disk with
    byte-identical content and nothing extra survives on disk that the
    registry would not produce (a hand edit, or a file the registry
    dropped)."""
    accepted = _entry_for(host)
    if accepted is None:
        return (["%s: host %r has no projection to check, it reads "
                 "bundle/skills directly" % (NODATA, host)], 0)

    if dest_dir is None:
        dest_dir = _default_dest_dir(host)
    if not isinstance(dest_dir, str) or not dest_dir:
        return (["%s: host %r has no usable destination directory to "
                 "check against (%r)" % (NODATA, host, dest_dir)], 0)

    files, problems = build_for_host(host, source_dir)
    if problems:
        return ([p if p.startswith(NODATA) else "%s: %s" % (NODATA, p)
                 for p in problems], 0)

    if not os.path.isdir(dest_dir):
        return (["%s: missing entirely" % dest_dir], 0)

    drift = []
    checked = 0
    expected_rel = set()
    for rel_path, data in sorted(files.items()):
        parts = rel_path.split("/")
        if any(part in ("", ".", "..") or os.path.isabs(part)
               for part in parts):
            drift.append("%s: unsafe relative path produced by the "
                         "registry for host %r, refusing to join it to a "
                         "destination directory" % (rel_path, host))
            continue
        expected_rel.add(rel_path)
        path = os.path.join(dest_dir, *parts)
        checked += 1
        try:
            with open(path, "rb") as fh:
                on_disk = fh.read()
        except FileNotFoundError:
            drift.append("%s: missing on disk for host %r" % (rel_path, host))
            continue
        except OSError as exc:
            drift.append("%s: unreadable (%s)" % (rel_path, exc))
            continue
        if on_disk != data:
            drift.append("%s: drift, does not match a fresh build from "
                         "bundle/skills for host %r" % (rel_path, host))

    walk_errors = []
    on_disk_rel = set()
    for dirpath, _dirnames, filenames in os.walk(
            dest_dir, onerror=lambda exc: walk_errors.append(str(exc))):
        for name in filenames:
            full = os.path.join(dirpath, name)
            on_disk_rel.add(os.path.relpath(full, dest_dir).replace(os.sep, "/"))
    for message in walk_errors:
        drift.append("%s: could not be fully walked (%s)" % (dest_dir, message))
    for rel_path in sorted(on_disk_rel - expected_rel):
        drift.append("%s: on disk but the registry does not produce it "
                     "for host %r, stale" % (rel_path, host))
    return drift, checked


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    hosts = args or sorted(CAPABILITY_REGISTRY)
    ok = True
    for host in hosts:
        try:
            problems, checked = check_for_host(host)
        except ValueError as exc:
            print("generic_skills_adapter: %s" % exc, file=sys.stderr)
            ok = False
            continue
        if problems:
            for line in problems:
                print("generic_skills_adapter: %s: %s" % (host, line))
            if not all(line.startswith(NODATA) for line in problems):
                ok = False
            elif checked == 0 and host in GENERATED_DIR:
                ok = False
        else:
            print("generic_skills_adapter: %s: %d file(s) match the "
                  "capability registry" % (host, checked))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
