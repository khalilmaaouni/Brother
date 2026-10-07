#!/usr/bin/env python3
"""Print the next release cut: the date, the version it would be, and the
closeout command that follows the tag. Row S29 (docs/plan/READINESS-
ROADMAP-2026-08-29.json): a fixed cadence needs a named weekday in
docs/plan/RELEASE-POLICY.md before any cut can claim to run on a schedule.
This script is that read, not a cutter: it never bumps a manifest, never
tags, never runs the closeout matrix. Reading the policy stays a plain grep
away, never invented on the day.

WHERE THE TWO INPUTS COME FROM. The weekday: a line in the policy file that
mentions "cadence" or "weekday" and names one of the seven days (case
insensitive), the way scripts/refresh_cut.py's own NO-DATA / CLEAR /
REFUSED convention reads a real file rather than assuming a shape.
RELEASE-POLICY.md as it stands on 2026-09-05 names no such line, which is
exactly the NO-DATA case below and not a bug in this script. The version:
scripts/cut_v1.0.0.sh writes the cut version into
bundle/.claude-plugin/plugin.json's own "version" key (see its step 1); the
version this script prints is that file's current value with the patch
component bumped by one, since a fixed-cadence cut is a routine patch cut
until the policy says otherwise. F9 (architecture review 2026-09-30): the
manifests are bumped BEFORE the tag exists, so while `v<manifest>` is not
yet a tag of the repository the manifest version IS the cut in flight and
is printed as is, never bumped past it (measured: manifests at 1.1.0, no
v1.1.0 tag, this tool printed 1.1.1 and scripts/cut.py's read_next_version
took that line for a bare cut). The tag is asked of the remote the release
publishes to (export_public.DEFAULT_REMOTE, `git ls-remote --tags`), never
of the local tag cache: this hub carries no tags and a clone without them
would call a released version the cut in flight (M2, attack 2026-09-30). A
remote git cannot answer for is NO-DATA.

--version-only (CV1.a, 2026-10-03), the one path scripts/cut.py runs: the
version is read from the ONE version source, .claude-plugin/marketplace.json
through scripts/version_source.py (read_source, run_check), never from a
second reader, and the release policy is not read at all, so the version
never depends on a weekday line. derive_next names the version the source
carries while its tag exists neither in the local tag list nor on the public
remote (in flight), and the patch bump only when the public remote has the
tag (released). A tag only in the local list (a half done cut or a stray
tag), a remote that cannot be read, names another repository or disagrees
with the local tag list, a carrier that drifts from the source, a source that
cannot be read and a source older than the highest public tag are each
NO-DATA, exit 3, with no version line. A `next cut basis:` line says which
rule fired. Without the flag every line and exit code is unchanged.

Exit codes: 0 printed, 3 NO-DATA (no cut weekday named, or no version could
be read). NO-DATA is never a pass. Python 3.9, standard library only.
"""
import argparse
import contextlib
import datetime
import io
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
try:
    from export_public import DEFAULT_REMOTE as DEFAULT_TAG_SOURCE  # noqa: E402
except ImportError:  # a tree without the exporter cannot name its release remote: NO-DATA below
    DEFAULT_TAG_SOURCE = None

DEFAULT_POLICY = os.path.join(ROOT, "docs", "plan", "RELEASE-POLICY.md")
DEFAULT_MANIFEST = os.path.join(ROOT, "bundle", ".claude-plugin", "plugin.json")

# CV1.a: the one reader of the version source. A tree without it cannot say
# which version it carries, so --version-only reads NO-DATA (derive_next); the
# path without the flag never needs it.
try:
    import version_source as VS  # noqa: E402
except ImportError:
    VS = None

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
WEEKDAY_RE = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE)

EXIT_OK = 0
EXIT_NODATA = 3


def find_cadence_weekday(text):
    """The first line naming both a cadence cue ('cadence' or 'weekday')
    and a day name wins; returns the day capitalized, or None."""
    for line in text.splitlines():
        low = line.lower()
        if "cadence" not in low and "weekday" not in low:
            continue
        m = WEEKDAY_RE.search(line)
        if m:
            return m.group(1).capitalize()
    return None


def next_weekday_date(today, weekday_name):
    """The next date on or after `today` that falls on `weekday_name`."""
    target = WEEKDAYS.index(weekday_name)
    delta = (target - today.weekday()) % 7
    return today + datetime.timedelta(days=delta)


def bump_patch(version):
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        return None  # Non-integer patch: caller reports NO-DATA.
    return ".".join(parts)


def tag_exists(source, version, run=subprocess.run):
    """True or False when the release remote answers, None when it cannot
    (no such remote, no git, no network, a timeout): None is NO-DATA, never
    a guess in either direction. An empty answer from a remote that DID
    answer is a real absence; an empty local cache never is."""
    if not isinstance(source, str) or not source:
        return None
    ref = "refs/tags/v%s" % version
    try:
        proc = run(["git", "ls-remote", "--tags", source, ref],
                   capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return True
    return False


# --- CV1.a: the version from the one source, while its tag is nowhere ------

#: The first word of a derive_next basis that carries a version.
IN_FLIGHT = "in-flight"
RELEASED = "released"

#: The one repository whose tags say "released", the one export_public.py
#: publishes to. Every URL tag_state and highest_public_tag read must name this
#: owner/repo path on HUB_HOST, compared after the scheme, the host user, a
#: port and a trailing .git are stripped; any other repository, a local path
#: or a file:// URL is unknown, NO-DATA, so a rewritten remote URL cannot
#: redirect the read (council finding 2026-10-03, third round).
HUB_REPO_PATH = "khalilmaaouni/Brother"
HUB_HOST = "github.com"

GIT_READ_TIMEOUT_S = 60
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
RELEASE_TAG_RE = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)")
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HOSTED_URL_RE = re.compile(
    r"(?:(?:https|ssh|git)://(?:[^@/\s]+@)?([A-Za-z0-9.-]+)(?::[0-9]+)?/"
    r"|(?:[^@/:\s]+@)?([A-Za-z0-9.-]+):(?!/))(\S+)")


def _is_version(value):
    return isinstance(value, str) and VERSION_RE.fullmatch(value) is not None


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def _git(argv, root, runner=None):
    """(exit code, stdout) of one git read run in `root`, or (None, why) when
    it could not run or answered something that is not a process result (an
    exit code that is not an int, a bool included, or stdout that is not
    text). Never raises."""
    read_with = runner or subprocess.run
    try:
        proc = read_with(argv, cwd=root, capture_output=True, text=True,
                         timeout=GIT_READ_TIMEOUT_S)
        code, out = proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError, ValueError, TypeError,
            AttributeError) as exc:
        return None, "%s could not run (%s)" % (" ".join(argv[:3]), exc)
    if type(code) is not int:
        return None, "%s answered no exit code" % " ".join(argv[:3])
    if out is None:
        out = ""
    if not isinstance(out, str):
        return None, "%s answered no text" % " ".join(argv[:3])
    return code, out


def _checked_run(runner=None):
    """The `run` tag_exists receives: an answer that is not a process result
    becomes OSError, which tag_exists reads as None (unknown), never as an
    answer and never a crash."""
    inner = runner or subprocess.run

    def checked(argv, **kwargs):
        try:
            proc = inner(argv, **kwargs)
            code, out = proc.returncode, proc.stdout
        except (TypeError, AttributeError, KeyError) as exc:
            raise OSError("the tag read answered garbage (%s)" % exc)
        if type(code) is not int or not isinstance(out, (str, type(None))):
            raise OSError("the tag read answered garbage")
        return proc
    return checked


def hub_repo_path(url):
    """The owner/repo path a hosted remote URL names on HUB_HOST, '' for any
    other host, a local path, a file:// URL or anything that is not a URL.
    https://github.com/o/r.git, ssh://git@github.com/o/r and
    git@github.com:o/r.git all give 'o/r'."""
    if not isinstance(url, str):
        return ""
    m = HOSTED_URL_RE.fullmatch(url)
    if not m:
        return ""
    host = (m.group(1) or m.group(2) or "").lower()
    path = m.group(3)
    if host != HUB_HOST:
        return ""
    if path.endswith("/"):
        path = path[:-1]
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
        return ""
    return path


def pinned_url(root, remote, runner=None):
    """(url, '') for the exact URL git prints for `remote`, or ('', why). A
    remote NAME is resolved with `git remote get-url <name>` in `root` and the
    URL it prints is what every read uses, never the name; a URL is expanded
    with `git ls-remote --get-url <url>` (url.<base>.insteadOf applied) and
    must come back unchanged. Either way the URL must name HUB_REPO_PATH."""
    if not isinstance(root, str) or not root or not os.path.isdir(root):
        return "", "the repository root %r is not a folder" % (root,)
    if not isinstance(remote, str) or not remote:
        return "", "no remote was named (%r)" % (remote,)
    if REMOTE_NAME_RE.fullmatch(remote):
        code, out = _git(["git", "remote", "get-url", remote], root, runner)
    else:
        code, out = _git(["git", "ls-remote", "--get-url", remote], root,
                         runner)
    if code != 0:
        return "", "git could not resolve the remote %s (%s)" % (
            remote, out if code is None else "exit %d" % code)
    lines = out.splitlines()
    if len(lines) != 1 or not lines[0].strip():
        return "", "git printed no single URL for the remote %s" % remote
    url = lines[0].strip()
    if not REMOTE_NAME_RE.fullmatch(remote) and url != remote:
        return "", "git rewrites %s to %s" % (remote, url)
    if hub_repo_path(url) != HUB_REPO_PATH:
        return "", "%s names another repository than %s/%s" % (
            url, HUB_HOST, HUB_REPO_PATH)
    return url, ""


def _local_release_tags(root, runner=None):
    """The X.Y.Z of every vX.Y.Z in `git tag -l`, or None when it cannot be
    read."""
    code, out = _git(["git", "tag", "-l"], root, runner)
    if code != 0:
        return None
    tags = set()
    for line in out.splitlines():
        m = RELEASE_TAG_RE.fullmatch(line.strip())
        if m:
            tags.add(m.group(1))
    return tags


def _remote_release_tags(root, url, runner=None):
    """The X.Y.Z of every refs/tags/vX.Y.Z on `url` (peeled lines and other
    tag shapes ignored). ValueError when the remote cannot be read or answers
    a line git never prints (a truncated or garbled list)."""
    code, out = _git(["git", "ls-remote", "--tags", url], root, runner)
    if code != 0:
        raise ValueError("git ls-remote --tags %s failed (%s)" % (
            url, out if code is None else "exit %d" % code))
    tags = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2 or not OBJECT_ID_RE.fullmatch(parts[0]) \
                or not parts[1].startswith("refs/"):
            raise ValueError("the tag list of %s carries a line git never "
                             "prints: %r" % (url, line[:80]))
        if parts[1].endswith("^{}") or not parts[1].startswith("refs/tags/"):
            continue
        m = RELEASE_TAG_RE.fullmatch(parts[1][len("refs/tags/"):])
        if m:
            tags.add(m.group(1))
    return tags


def tag_state(root, version, remote, runner=None):
    """'present' when the remote has tag v<version>; 'absent' when the remote
    answers without it and the local tag list has none; 'local-only' when the
    local list has it and the remote does not; 'unknown' when the remote cannot
    be read. Local: git tag -l. Remote: the existing tag_exists(remote,
    version, run) of this file (True, False, None), on the URL pinned_url
    prints, never a name. A remote that cannot be read is 'unknown' whatever
    the local list says; so is a remote that names another repository, whose
    tag list disagrees with its own answer for v<version>, or that hides a
    release tag below <version> the local list holds (a falsely low history
    never steers derive_next into in-flight). Release tags above <version> in
    the local list are not this check's: this hub carries the merged products'
    own v2 and v3 tags, which were never this remote's."""
    if not _is_version(version):
        return "unknown"
    url, _why = pinned_url(root, remote, runner)
    if not url:
        return "unknown"
    remote_has = tag_exists(url, version, _checked_run(runner))
    public = None
    if remote_has is not None:
        try:
            public = _remote_release_tags(root, url, runner)
        except ValueError:
            public = None
    if public is None:
        return "unknown"
    local = _local_release_tags(root, runner)
    if local is None or remote_has != (version in public):
        return "unknown"
    hidden = [v for v in local
              if v not in public and _version_key(v) < _version_key(version)]
    if hidden:
        return "unknown"
    if remote_has:
        return "present"
    if version in local:
        return "local-only"
    return "absent"


def highest_public_tag(root, remote, runner=None):
    """The highest X.Y.Z among the remote's refs/tags/vX.Y.Z (peeled lines
    ignored, other tag shapes ignored), '' when it has none, and raises
    ValueError when the remote cannot be read (the caller maps that to
    NO-DATA). The remote is read on the URL pinned_url prints."""
    url, why = pinned_url(root, remote, runner)
    if not url:
        raise ValueError(why)
    tags = _remote_release_tags(root, url, runner)
    if not tags:
        return ""
    return max(tags, key=_version_key)


def read_source_version(root):
    """(version, '') for the "brother" entry of .claude-plugin/marketplace.json
    read through version_source.read_source, or (None, why): no
    version_source module, a missing or unreadable source, or a value that is
    not X.Y.Z."""
    if not isinstance(root, str) or not root:
        return None, "the repository root %r is not a path" % (root,)
    if VS is None:
        return None, ("scripts/version_source.py could not be loaded, so the "
                      "version source cannot be read")
    try:
        version, _doc = VS.read_source(pathlib.Path(root))
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return None, "the version source could not be read (%s)" % exc
    if version is None:
        return None, ("%s is missing or unreadable, or names no brother "
                      "entry with a version" % VS.MARKETPLACE_REL)
    if not _is_version(version):
        return None, ("%s gives the brother entry the version %r, not X.Y.Z"
                      % (VS.MARKETPLACE_REL, version))
    return version, ""


def carrier_drift(root):
    """'' when every carrier agrees with the source (version_source.run_check
    exit 0), else the drift in words; a check that cannot run is a drift."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            drift_code = VS.run_check(pathlib.Path(root))
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return "the carrier check could not run (%s)" % exc
    if drift_code != 0:
        lines = [l for l in out.getvalue().splitlines()
                 if l.startswith(("DRIFT", "NO-DATA: " + VS.MARKETPLACE_REL))]
        return ("a carrier drifts from the version source "
                "(version_source.run_check exit %r): %s"
                % (drift_code, "; ".join(lines[:5]) or "no line printed"))
    return ""


def derive_next(root, remote, runner=None):
    """(version or None, basis). basis starts with 'in-flight:' or 'released:'
    on success and with 'NO-DATA:' when version is None. Reads the version only
    through version_source.read_source; drift or an unreadable source is
    NO-DATA."""
    version, why = read_source_version(root)
    if version is None:
        return None, "NO-DATA: " + why
    drift = carrier_drift(root)
    if drift:
        return None, "NO-DATA: " + drift
    state = tag_state(root, version, remote, runner)
    if state == "local-only":
        return None, ("NO-DATA: v%s is in the local tag list but not on %s: a "
                      "half done cut or a stray tag, never released and never "
                      "in flight" % (version, remote))
    if state not in ("absent", "present"):
        return None, ("NO-DATA: whether v%s is released is unknown: %s could "
                      "not be read, names another repository than %s, or "
                      "disagrees with the local tag list"
                      % (version, remote, HUB_REPO_PATH))
    try:
        highest = highest_public_tag(root, remote, runner)
    except ValueError as exc:
        return None, "NO-DATA: the public tags could not be read (%s)" % exc
    if highest and _version_key(highest) > _version_key(version):
        return None, ("NO-DATA: the manifest is older than a released tag: "
                      "the source carries %s and %s already has v%s"
                      % (version, remote, highest))
    if state == "absent":
        return version, ("%s: v%s is tagged neither in the local list nor on "
                         "%s, and the source carries it, so it is the next "
                         "cut" % (IN_FLIGHT, version, remote))
    return bump_patch(version), ("%s: v%s is on %s, so the next cut is the "
                                 "patch bump" % (RELEASED, version, remote))


def _version_only(args, root, runner):
    """--version-only: the version derive_next answers, the release policy
    never read. The --manifest the path without the flag reads must agree
    with the source (its default, the bundle manifest, is one of the
    carriers): a disagreement is a drift, NO-DATA."""
    source, why = read_source_version(root)
    if source is None:
        print("NO-DATA: " + why)
        return EXIT_NODATA
    manifest = args.manifest
    if manifest == DEFAULT_MANIFEST:
        manifest = os.path.join(root, "bundle", ".claude-plugin", "plugin.json")
    try:
        with open(manifest, "r", encoding="utf-8") as fh:
            carried = json.load(fh)["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("NO-DATA: could not read a version from %s (%s)"
              % (manifest, exc))
        return EXIT_NODATA
    if carried != source:
        print("NO-DATA: %s carries %r while the version source carries %s: "
              "a drift" % (manifest, carried, source))
        return EXIT_NODATA
    remote = args.remote if args.remote is not None else args.tag_source
    version, basis = derive_next(root, remote, runner)
    if version is None:
        print(basis)
        return EXIT_NODATA
    if basis.startswith(IN_FLIGHT + ":"):
        print("cut in flight: v%s is not tagged yet, the manifest already "
              "carries it, so it is the next cut" % version)
    print("next cut basis: %s" % basis)
    print("next cut version: %s" % version)
    print("closeout command: python3 scripts/release_closeout.py all "
          "--version %s" % version)
    return EXIT_OK


def main(argv=None, root=None, runner=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag-source", default=DEFAULT_TAG_SOURCE,
                     help="the remote (URL or path) whose tags decide "
                          "whether the manifest version is already cut "
                          "(default: the remote export_public.py publishes "
                          "to)")
    ap.add_argument("--policy", default=DEFAULT_POLICY,
                     help="path to RELEASE-POLICY.md (default: the hub's own)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                     help="path to the plugin manifest cut_v1.0.0.sh writes "
                          "the version into (default: bundle/.claude-plugin/"
                          "plugin.json)")
    ap.add_argument("--today", default=None,
                     help="fix today's date as YYYY-MM-DD, for tests; "
                          "default: the real date")
    ap.add_argument("--version-only", action="store_true",
                     help="print only the version, derived from the version "
                          "source (.claude-plugin/marketplace.json) and the "
                          "tags; the release policy is not read (the path "
                          "scripts/cut.py runs)")
    ap.add_argument("--remote", default=None,
                     help="with --version-only: the git remote name or URL "
                          "whose tags say released; its URL must name %s "
                          "(default: --tag-source)" % HUB_REPO_PATH)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version_only:
        return _version_only(args, ROOT if root is None else root, runner)

    if args.today:
        try:
            today = datetime.datetime.strptime(args.today, "%Y-%m-%d").date()
        except ValueError:
            print("NO-DATA: --today %r is not YYYY-MM-DD" % args.today)
            return EXIT_NODATA
    else:
        today = datetime.date.today()

    try:
        with open(args.policy, "r", encoding="utf-8") as fh:
            policy_text = fh.read()
    except OSError as exc:
        print("NO-DATA: could not read %s (%s)" % (args.policy, exc))
        return EXIT_NODATA

    weekday = find_cadence_weekday(policy_text)
    if not weekday:
        print("NO-DATA: RELEASE-POLICY.md names no cut weekday (S29, founder)")
        return EXIT_NODATA

    try:
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        current_version = manifest["version"]
    except (OSError, ValueError, KeyError) as exc:
        print("NO-DATA: could not read a version from %s (%s)"
              % (args.manifest, exc))
        return EXIT_NODATA

    cut_date = next_weekday_date(today, weekday)
    bumped = bump_patch(current_version)
    if bumped is None:
        # Judged before the tag read: a version that cannot be bumped is not
        # a cut in flight either, it is an unreadable manifest.
        print("NO-DATA: version %r has a non-integer patch component"
              % current_version)
        return EXIT_NODATA
    tagged = tag_exists(args.tag_source, current_version)
    if tagged is None:
        print("NO-DATA: could not read the tags of %s, so whether v%s is "
              "already cut is unknown; pass --tag-source, or --version to "
              "the cut, explicitly" % (args.tag_source, current_version))
        return EXIT_NODATA
    if tagged:
        version = bumped
    else:
        version = current_version
        print("cut in flight: v%s is not tagged yet, the manifest already "
              "carries it, so it is the next cut" % version)

    print("next cut weekday: %s" % weekday)
    print("next cut date: %s" % cut_date.isoformat())
    print("next cut version: %s" % version)
    print("closeout command: python3 scripts/release_closeout.py all "
          "--version %s" % version)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
