#!/usr/bin/env python3
"""edition_guard: bind a directory to its nearest .brother-edition, and
refuse a push whose remote is the public export target unless the
invocation is the exporter's own.

WHY THIS EXISTS. docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5, the
mechanical half of the founder's "Private hub, public export" ruling
(docs/decisions/2026-08-30-edition-architecture.html): no working session
ever holds the public remote, so the only route content can take toward the
public repository is scripts/export_public.py's own commit. This is the
guard that makes forgetting fail SAFE (private) rather than fail public,
per docs/plan/PRIVACY-FILTERING-SPEC.md's default rule.

TWO JOBS, ONE FILE, per the migration plan's own two done-checks:

  --where PATH    step 3's job: name the edition and vault that own a
                   directory, by walking up from it to the nearest
                   .brother-edition. Refuses (NO-DATA) when none is found
                   anywhere above it.

  --check-push URL  step 5's job: would a push targeting URL leave this
                   machine correctly? Refused unless URL is not the public
                   export target, OR this invocation carries the exporter's
                   own marker.

THE MARKER, not a secret and not a credential: BROTHER_EXPORT_INVOCATION
must equal EXPORT_MARK, an environment variable scripts/export_public.py
sets (and prints, per the plan's own wording "marked by an env var the
exporter sets and prints") on the one subprocess call that pushes. Nothing
else may claim it: this is a fixed string in this file, not a value a
session can type into its own environment for one push and forget, because
that would be indistinguishable from a session inventing the escape hatch.
The real control is architectural (only export_public.py's own code path
sets it), and this comment says so plainly rather than pretending an env
var is a secret.

NO-DATA IS NEVER A PASS, the same discipline every guard in this estate
follows: a directory with no .brother-edition above it has not been shown
to be SAFE, so both entry points refuse rather than assume public-core.

Python 3, standard library only. No network.
"""
import argparse
import os
import re
import sys

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NODATA = 2

MARKER_FILE = ".brother-edition"

#: The exporter sets this to EXPORT_MARK on the one subprocess call that
#: pushes to the public remote. No other code path may set it: the control
#: is that only scripts/export_public.py's source contains this assignment,
#: not that the string itself is secret.
EXPORT_ENV = "BROTHER_EXPORT_INVOCATION"
EXPORT_MARK = "export_public.py"

#: The public export target named in PROJECT.md
#: (https://github.com/khalilmaaouni/Brother). Matched by owner/repo so the
#: ssh (git@github.com:owner/repo.git) and https
#: (https://github.com/owner/repo) forms of the same remote both hit, and a
#: private fork or an unrelated repository with a similar name does not.
PUBLIC_REMOTE_RE = re.compile(
    r"github\.com[:/]khalilmaaouni/Brother(?:\.git)?/?$", re.IGNORECASE)


def find_edition_file(start):
    """Walk from `start` up to the filesystem root looking for
    .brother-edition. Returns its path, or None when no directory above
    (and including) `start` carries one."""
    cur = os.path.abspath(start)
    while True:
        candidate = os.path.join(cur, MARKER_FILE)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def parse_edition_file(path):
    """(edition, vault) from a `.brother-edition` file's `key: value`
    lines. A missing key comes back as None, never as a made up default:
    a file that forgot to name its edition must not be read as public."""
    edition = None
    vault = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "edition":
                edition = value
            elif key == "vault":
                vault = value
    return edition, vault


def is_public_remote(remote_url):
    """True when `remote_url` names the public export target repository."""
    return bool(remote_url) and bool(PUBLIC_REMOTE_RE.search(remote_url))


def check_push(remote_url, cwd=None, env=None):
    """THE LAW: a push whose remote is the public export target is refused
    unless this invocation carries the exporter's own marker. Applies to
    every edition equally, public-core included, because the ground rule is
    that NO session holds the public remote, not merely that private
    editions may not.

    Returns (exit_code, message). Never raises on a missing or malformed
    .brother-edition: that is NO-DATA, reported, not an exception a caller
    has to catch."""
    env = os.environ if env is None else env
    cwd = cwd or os.getcwd()

    path = find_edition_file(cwd)
    if path is None:
        return EXIT_NODATA, (
            "NO-DATA: no .brother-edition found above %s, so the edition "
            "guard could not tell which edition owns this push. That is "
            "not a pass." % cwd)
    edition, _vault = parse_edition_file(path)
    if not edition:
        return EXIT_NODATA, (
            "NO-DATA: %s carries no 'edition:' line, so the guard could "
            "not tell which edition owns this push." % path)

    if not is_public_remote(remote_url):
        return EXIT_OK, ("OK: edition %s pushing to a non-public remote "
                          "(%s)" % (edition, remote_url or "unknown"))

    # From here the remote IS the public export target.
    if env.get(EXPORT_ENV) == EXPORT_MARK:
        return EXIT_OK, ("OK: %s=%s, the exporter's own marked invocation, "
                          "the single allow for the public remote"
                          % (EXPORT_ENV, EXPORT_MARK))
    return EXIT_REFUSED, (
        "REFUSED: edition %s at %s may not push to the public remote (%s). "
        "THE LAW: the public repository is a read-only export target, fed "
        "only by scripts/export_public.py, the single allow (marked by "
        "%s=%s). No working session pushes it directly, from any edition, "
        "per docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5."
        % (edition, path, remote_url, EXPORT_ENV, EXPORT_MARK))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--where",
                     help="print the edition and vault owning this "
                          "directory (default: --cwd)")
    ap.add_argument("--check-push", dest="remote_url",
                     help="the remote URL a push targets; refuses when it "
                          "is the public export target and this invocation "
                          "is not the exporter's own")
    ap.add_argument("--cwd", default=None)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    cwd = args.cwd or os.getcwd()

    if args.remote_url is not None:
        code, msg = check_push(args.remote_url, cwd=cwd)
        print(msg)
        return code

    where = args.where or cwd
    path = find_edition_file(where)
    if path is None:
        print("NO-DATA: no .brother-edition found above %s" % where)
        return EXIT_NODATA
    edition, vault = parse_edition_file(path)
    if not edition:
        print("NO-DATA: %s carries no 'edition:' line" % path)
        return EXIT_NODATA
    print("edition: %s" % edition)
    print("vault: %s" % (vault or "none"))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
