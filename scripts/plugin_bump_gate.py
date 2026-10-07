"""plugin_bump_gate.py: a plugin whose shipped files changed must carry a new
version, or the release cut is refused.

WHY THIS EXISTS. `claude plugin update <plugin>@<marketplace>` compares the
plugin's version STRING and nothing else. Releases 1.0.19 and 1.0.21 shipped
changed files under products/brothermode and products/brothersbe while both
kept their version. Measured 2026-09-20 on an installed machine after the
marketplace refresh: 29 differing entries for one and 13 for the other between
the release source and the installed cache, while the updater printed "already
at the latest version". Users silently kept old product code. Measured again
on the public tags the same day: 24 and 12 files differ between v1.0.20 and
v1.0.21 under those two directories, versions unchanged. Nothing in the cut
compared content against version, so nothing could refuse.

WHAT IT COMPARES. The CANDIDATE is the export tree (export_public's own
builder, through cut_preflight.export_tree_builder), never the hub checkout:
the allowlist decides what users receive, so a hub side diff would count files
that never ship. The PREVIOUS RELEASE is the newest vX.Y.Z tag BELOW the
version being cut on the public remote, read with `git ls-remote --tags`:
release tags are cut on the public repository and the hub does not carry
them. For every plugin .claude-plugin/marketplace.json lists, the plugin's
subdirectory is diffed between the two; files differ and the version string
is the one the previous release listed means REFUSED.

THE UMBRELLA. The cut itself writes the umbrella entry's version
(scripts/version_source.py --write), so before the bump it still reads the old
number. It is judged at the version being cut, which is what ships. Every
other plugin is judged at the version its entry carries.

IT RUNS TWICE, on purpose. cut_preflight.py asks it before the cut starts, so
a forgotten bump costs seconds. cut.py asks it again right after
cut_v1.0.0.sh, on the exact tree that ships, because the cut script itself
writes into a product directory (the pinned tag in a README, a facts file):
a product nobody touched by hand still changes at the bump, and only the
second read can see that.

THE ENTRIES THE CUT RETIRES. From retire_catalogs.RETIRE_AT upward the cut
removes every catalog entry but the umbrella before it bumps anything
(retire_catalogs.py --apply, the first step of cut.py's chain). The first read
above happens before that step, so it asks the same rule (retirement_rule
below) and does not judge an entry the cut is about to remove: no shipped
catalog will list it, so no updater will ever compare its version. Each such
entry is named in the verdict line as not judged. The umbrella is always
judged, and below RETIRE_AT every entry is.

Exit 0 OK. Exit 1 REFUSED. Exit 2 NO-DATA: the remote, the previous tag, either
marketplace file, a plugin's subdirectory or the retirement rule could not be
read, or the rule leaves nothing to judge. NO-DATA is never a pass, and both
callers block on anything but 0. Python 3, standard library only. No em or en
dashes.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cut_preflight as P  # noqa: E402

OK, REFUSED, NODATA = P.OK, P.REFUSED, P.NODATA
NAME = "plugin-bumps"
MARKETPLACE_REL = ".claude-plugin/marketplace.json"
#: The one entry the cut bumps by itself (version_source.py's "brother").
UMBRELLA = "brother"
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
NETWORK_TIMEOUT_S = 300


def _git_env():
    """git exports GIT_DIR to its hooks; with it set, every git command here
    would answer for THAT repository whatever its cwd says (measured twice on
    2026-09-20, see cut_preflight.no_git_location)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def previous_release_tag(root, version, remote, runner=None):
    """(tag, why): the newest vX.Y.Z tag on `remote` below `version`, or
    (None, why). Compared as numbers: v1.0.9 is older than v1.0.20, and a
    product tag such as v3.4.2 is above every umbrella release, never below."""
    m = RELEASE_TAG.match("v%s" % version)
    if not m:
        return None, "version %r is not a release number X.Y.Z" % version
    want = tuple(int(x) for x in m.groups())
    proc = P._run(["git", "ls-remote", "--tags", remote], root, runner,
                  env=_git_env(), timeout=NETWORK_TIMEOUT_S)
    if proc.returncode != 0:
        return None, "git ls-remote --tags %s failed: %s" % (remote, P._last(proc))
    lower = set()
    for line in (proc.stdout or "").splitlines():
        ref = line.split()[-1] if line.strip() else ""
        if ref.endswith("^{}"):
            ref = ref[:-3]
        m = RELEASE_TAG.match(ref[len("refs/tags/"):] if ref.startswith("refs/tags/") else "")
        if m:
            key = tuple(int(x) for x in m.groups())
            if key < want:
                lower.add(key)
    if not lower:
        return None, "no release tag below v%s on %s" % (version, remote)
    return "v%d.%d.%d" % max(lower), ""


def plugin_path(entry):
    """The plugin's subdirectory inside this repository, or None when the
    entry does not name one this gate can diff (another repository, a shape
    nobody taught it, a path that leaves the tree). None blocks as NO-DATA."""
    src = entry.get("source") if isinstance(entry, dict) else None
    if isinstance(src, dict):
        path = src.get("path")
    elif isinstance(src, str) and src.startswith("./"):
        path = src
    else:
        return None
    if not isinstance(path, str) or os.path.isabs(path):
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _plugins(text):
    """name -> entry out of one marketplace.json text; raises ValueError."""
    plugins = json.loads(text)["plugins"]
    out = {p["name"]: p for p in plugins}
    if not out:
        raise ValueError("the plugin list is empty")
    return out


def retirement_rule(version):
    """(retiring, why): does the cut of `version` retire every catalog entry
    but UMBRELLA? Read from the one rule the cut itself applies, and never
    restated here: retire_catalogs.applies(version) and retire_catalogs.KEEP,
    the same pair scripts/test_cursor_plugin.py reads the catalog shape through.

    WHY THIS GATE ASKS. cut.py runs retire_catalogs.py --apply as the first
    step of its chain, before the bump, so from retire_catalogs.RETIRE_AT
    upward the catalog that ships lists the kept entry alone. The preflight
    asks this gate BEFORE that step, on a catalog that still lists the entries
    the cut is about to remove, and judged them as if they shipped. Measured
    2026-10-05 on the 1.1.0 candidate: three entries refused for an unchanged
    version that no shipped catalog will carry. The second read, after the
    bump, never sees those entries at all; this makes the first read judge the
    same set.

    FAIL DIRECTION: unknown blocks. A rule that cannot be imported, lacks
    either name, raises, exits, answers anything but exactly True or False,
    or keeps an entry other than this gate's UMBRELLA returns (None, why),
    and the caller reads that as NO-DATA. Unknown never means retired, and it
    never means "nothing retired" either: each would be a guess about what
    ships. An exit is caught with the exceptions on purpose (review round 1):
    a rule that left through SystemExit(0) ended this gate's own process at
    exit 0 with no verdict line, which both callers read as a pass. For the
    same reason everything read OFF the rule's own objects, the comparison of
    its kept name and the text of both values, happens inside the same try
    (review round 2): a comparison or a repr the rule defines can exit too."""
    try:
        import retire_catalogs
        kept = retire_catalogs.KEEP
        retiring = retire_catalogs.applies(version)
        keeps_umbrella = bool(kept == UMBRELLA)
        answered, keeps = repr(retiring), repr(kept)
    except (Exception, SystemExit) as exc:
        return None, ("the retirement rule (scripts/retire_catalogs.py) could not be "
                      "read, so which entries the cut retires is unknown: %s: %s"
                      % (type(exc).__name__, exc))
    if type(retiring) is not bool:
        return None, ("the retirement rule (scripts/retire_catalogs.py) answered %s "
                      "for %s, neither True nor False" % (answered, version))
    if not keeps_umbrella:
        return None, ("the retirement rule (scripts/retire_catalogs.py) keeps %s and "
                      "this gate judges %r as the entry the cut bumps: the two must "
                      "be one entry" % (keeps, UMBRELLA))
    return retiring, ""


def check_plugin_bumps(root, version, remote=None, runner=None, build=None):
    """(verdict, name, detail), the shape every cut_preflight check returns."""
    if remote is None:
        import export_public
        remote = export_public.DEFAULT_REMOTE
    prev, why = previous_release_tag(root, version, remote, runner)
    if prev is None:
        return NODATA, NAME, why
    # Asked before the export tree is built: an unreadable rule costs nothing.
    retiring, why = retirement_rule(version)
    if retiring is None:
        return NODATA, NAME, why
    build = build or P.export_tree_builder(root)
    tree = tempfile.mkdtemp(prefix="plugin-bump-gate-")
    try:
        try:
            build(tree)
        except Exception as exc:  # sbe: allow-silent reported as NO-DATA below, never swallowed
            return NODATA, NAME, "the export tree could not be built: %s" % exc
        return _compare(tree, version, remote, prev, runner, retiring)
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def _compare(tree, version, remote, prev, runner, retiring):
    """`retiring` is retirement_rule's answer, exactly True or False. It has
    no default on purpose: a caller that forgot it would read as "nothing is
    retired", which is one of the two guesses that function refuses to make."""
    env = _git_env()

    def git(*args, **kw):
        return P._run(("git",) + args, tree, runner, env=env, **kw)

    proc = git("fetch", "-q", "--depth", "1", remote, "refs/tags/%s" % prev,
               timeout=NETWORK_TIMEOUT_S)
    if proc.returncode != 0:
        return NODATA, NAME, "git fetch %s from %s failed: %s" % (prev, remote, P._last(proc))
    proc = git("rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}")
    base = (proc.stdout or "").strip()
    if proc.returncode != 0 or not base:
        return NODATA, NAME, "%s did not resolve to a commit: %s" % (prev, P._last(proc))
    if git("rev-parse", "--verify", "-q", "HEAD^{commit}").returncode != 0:
        return NODATA, NAME, "the candidate tree has no commit to compare"
    proc = git("show", "%s:%s" % (base, MARKETPLACE_REL))
    try:
        if proc.returncode != 0:
            raise ValueError(P._last(proc))
        old = _plugins(proc.stdout or "")
    except (ValueError, KeyError, TypeError) as exc:
        return NODATA, NAME, "%s at %s unreadable: %s" % (MARKETPLACE_REL, prev, exc)
    try:
        with open(os.path.join(tree, MARKETPLACE_REL), encoding="utf-8") as fh:
            new = _plugins(fh.read())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return NODATA, NAME, "the candidate %s is unreadable: %s" % (MARKETPLACE_REL, exc)

    if retiring and UMBRELLA not in new:
        # Every listed entry would be skipped below, so nothing would be judged.
        return NODATA, NAME, ("the cut of %s retires every catalog entry but %s "
                              "(scripts/retire_catalogs.py) and the candidate %s lists "
                              "no %s, so there is nothing left to judge"
                              % (version, UMBRELLA, MARKETPLACE_REL, UMBRELLA))
    stale, unknown, fine, retired = [], [], [], []
    for name, entry in new.items():
        if retiring and name != UMBRELLA:
            # Never silent: every verdict line below names what was skipped.
            retired.append("%s: not judged, the cut of %s retires it from the "
                           "catalog (scripts/retire_catalogs.py)" % (name, version))
            continue
        path = plugin_path(entry)
        if path is None:
            unknown.append("%s: no subdirectory of this repository to diff" % name)
            continue
        if name not in old:
            fine.append("%s new since %s" % (name, prev))
            continue
        proc = git("diff", "--name-only", base, "HEAD", "--", path)
        if proc.returncode != 0:
            unknown.append("%s: git diff of %s failed: %s" % (name, path, P._last(proc)))
            continue
        changed = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        ships = version if name == UMBRELLA else entry.get("version")
        was = old[name].get("version")
        if changed and ships == was:
            stale.append("%s: %d file(s) under %s differ from %s and the version "
                         "is still %s (first: %s)"
                         % (name, len(changed), path, prev, was,
                            next((c for c in changed
                                  if not c.endswith("CHECKSUMS.sha256")), changed[0])))
        else:
            fine.append("%s %s, %d file(s) changed" % (name, ships, len(changed)))
    if stale:
        return REFUSED, NAME, ("bump each plugin's version (every carrier "
                               "version_source.py --check names) before cutting, "
                               "the updater compares the version string only: "
                               + "; ".join(stale + ["NO-DATA " + u for u in unknown]
                                           + retired))
    if unknown:
        return NODATA, NAME, "; ".join(unknown + retired)
    return OK, NAME, "against %s: %s" % (prev, "; ".join(fine + retired))


def exit_code(verdict):
    return {OK: P.EXIT_OK, REFUSED: P.EXIT_REFUSED}.get(verdict, P.EXIT_NODATA)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Refuse a release cut that ships a "
                                 "changed plugin under an unchanged version.")
    ap.add_argument("--version", required=True, help="the release being cut, e.g. 1.0.22")
    ap.add_argument("--remote", default=None, help="the public remote (default: "
                    "export_public.DEFAULT_REMOTE)")
    args = ap.parse_args(argv)
    verdict, name, detail = check_plugin_bumps(ROOT, args.version, args.remote)
    print("%-8s %-16s %s" % (verdict, name, detail))
    return exit_code(verdict)


if __name__ == "__main__":
    sys.exit(main())
