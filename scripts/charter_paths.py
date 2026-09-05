#!/usr/bin/env python3
"""charter_paths: every repository path docs/CHARTER.md names must exist here.

WHY THIS EXISTS. Row E47 of docs/plan/READINESS-ROADMAP-2026-08-29.json: the
charter named the architecture of record as a file under docs/plan/ and that
file was not in the tree. Nothing could notice. tests/test_surface.py only
asserted that COORDINATION.md CONTAINED the record's name as a string, which
stays green whether or not the file exists, and a reader following the charter
from a fresh clone got "no such file".

WHAT IT CHECKS. Every backticked token in docs/CHARTER.md that looks like a
repository path must resolve against the repository root. Three tokens in the
charter are deliberately NOT paths in this repository and are listed in GENERIC
below with the reason: each is a relative name inside some other tree, or a path
the charter itself describes as one that must not exist yet.

DRIVEN BOTH WAYS. An exemption is only safe while it is still earned, so a
GENERIC entry the charter no longer mentions is itself a FAIL: the list cannot
quietly outlive the sentence it was written for. scripts/test_charter_paths.py
drives all four verdicts over temporary fixtures.

EXITS, this estate's convention: 0 PASS, 1 FAIL (a named path is missing, or an
exemption is stale), 2 NO-DATA (the charter could not be read). NO-DATA is never
a pass.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tokens that LOOK like repository paths but are not paths in this repository.
# Each entry gives its segments and the reason it is not resolvable here; a
# reason nobody can point at is a reason to look, not to renew.
_GENERIC_ENTRIES = [
    ((".claude-plugin", "plugin.json"),
     "a per product relative name: the charter says each product directory "
     "carries its own, and tests/test_surface.py resolves it per product"),
    (("hooks", "hooks.json"),
     "a per plugin relative name, named by the charter only to say it does "
     "NOT specify that file's schema"),
    (("plugins", "brotherds"),
     "named by the charter as one that must NOT exist yet: the claims product "
     "joins the marketplace only once its context is separated out"),
    (("products", "brothermode", "docs", "plan",
      "ADR-2026-08-23-one-brother-repository.md"),
     "the private deciding record this ADR was written from: the founder's "
     "own words, an internal effort/token table and a private archive path "
     "(docs/plan/EXPORT-ALLOWLIST.txt, 2026-09-05). It exists in the hub, "
     "so this exemption only matters in a public export tree, which never "
     "carries it; docs/plan/ADR-2026-08-23-one-brother-repository.md is the "
     "public sibling that reads the same decision back off the tree, and "
     "that one IS exported"),
    (("COORDINATION.md",),
     "withheld from the public export on purpose "
     "(docs/plan/EXPORT-DENYLIST.txt, 2026-09-03): an internal hub process "
     "document, not linked from README.md. It exists in the hub, so this "
     "exemption only matters in a public export tree, which never carries "
     "it; tests/test_surface.py reports NO-DATA there rather than passing "
     "quietly"),
]
GENERIC = dict((os.path.join(*seg), why) for seg, why in _GENERIC_ENTRIES)

# A token is path shaped when it carries a separator or a file extension this
# repository actually uses. Anything with whitespace is prose or a command line
# (the unittest invocation the charter quotes, for one), never a path.
_EXTENSIONS = (".md", ".py", ".json", ".sh", ".html", ".txt", ".yml", ".sha256")


def path_shaped(token):
    if not token or any(c.isspace() for c in token):
        return False
    return os.sep in token or token.endswith(_EXTENSIONS)


def read_charter(path):
    """Return the charter text, or None when it cannot be read (NO-DATA)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print("NO-DATA: cannot read %s (%s)" % (path, exc))
        return None


def check(charter_path, root):
    text = read_charter(charter_path)
    if text is None:
        return 2

    tokens = sorted(set(t for t in re.findall(r"`([^`\n]+)`", text)
                        if path_shaped(t)))
    if not tokens:
        print("NO-DATA: %s names no repository path in backticks" % charter_path)
        return 2

    missing = []
    checked = 0
    for token in tokens:
        if token in GENERIC:
            continue
        checked += 1
        if not os.path.exists(os.path.join(root, token)):
            missing.append(token)

    stale = [t for t in GENERIC if t not in tokens]

    for token in missing:
        print("FAIL: %s names `%s`, which is not in the tree"
              % (charter_path, token))
    for token in stale:
        print("FAIL: `%s` is exempt in GENERIC but %s no longer names it; "
              "drop the exemption" % (token, charter_path))

    if missing or stale:
        print("FAIL  %d missing, %d stale exemption(s), out of %d checked"
              % (len(missing), len(stale), checked))
        return 1

    print("PASS  %d repository path(s) named by %s all exist; "
          "%d generic token(s) exempt"
          % (checked, charter_path, len(GENERIC)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT,
                        help="repository root the named paths resolve against")
    parser.add_argument("--charter", default=None,
                        help="charter file to read (default: CHARTER.md under "
                             "the root's docs directory)")
    args = parser.parse_args(argv)
    charter = args.charter or os.path.join(args.root, "docs", "CHARTER.md")
    return check(charter, args.root)


if __name__ == "__main__":
    sys.exit(main())
