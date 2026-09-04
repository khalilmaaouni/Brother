#!/usr/bin/env python3
"""release_invariant: one release identity, or say exactly which link broke.

Root 3 of docs/plan/ROOT-CAUSE-REGISTER-2026-08-31.md and A4 of the
productization directive: the string a user installs by must identify one
exact set of bytes. This tool checks every link of that chain it can reach
and names each one:

  1. The two in-repo declarations agree: bundle/.claude-plugin/plugin.json
     and the brother entry of .claude-plugin/marketplace.json.
  2. docs/releases/<version>.md exists for that version.
  3. The public repository carries tag v<version> (git ls-remote, one
     network call), and, when the local public checkout has fetched it, the
     tag commit's own bundle manifest reads the same version.
  4. The installed plugin cache for <version>, when present on this
     machine, is byte-identical to the repo bundle on its manifest and its
     runtime entry point.

EXITS, per this estate's conventions and the readiness gate's reading
(scripts/readiness_gate.py runs this and treats exit 0 as PASS):
  0  every link that COULD be checked agrees, and at least links 1 and 2
     were checked (they need nothing but this repo). Unreachable optional
     links (no network, no installed copy) are printed as NO-DATA lines,
     because absence of a checker's input is never a contradiction.
  1  any reachable link CONTRADICTS: two version sites disagree, the tag's
     bytes disagree, the tag exists with no GitHub Release, or the
     installed copy differs. The broken link is named. A version whose tag
     does NOT EXIST YET is not in this class: that is the moment between
     the cut and the tag, and it reads NO-DATA (see the tag link below).
  2  NO-DATA: not even the in-repo declarations could be read.

Python 3, standard library only. The single network call (ls-remote) is
wrapped; failure to reach the remote is a NO-DATA line, never a crash.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_REMOTE = "https://github.com/khalilmaaouni/Brother"
PUBLIC_REPO = "khalilmaaouni/Brother"  # OWNER/REPO form for gh's --repo flag; PUBLIC_REMOTE above is the same repository's git URL form
#: E80 item 7: this used to be os.path.expanduser("~/Brother"), so a stranger
#: running the tool inside a fresh clone got a verdict about a checkout on
#: THE MAINTAINER'S machine (or, more often, silent NO-DATA lines about a
#: path that has nothing to do with the tree under test). The default is now
#: the tree this script lives in, which is the clone on a stranger's machine
#: and the hub on the maintainer's; --public-checkout still overrides it, and
#: a tree whose tags are missing reads NO-DATA, never a pass.
PUBLIC_CHECKOUT = ROOT
INSTALL_CACHE = os.path.expanduser("~/.claude/plugins/cache/brother/brother")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def declared_versions(root=ROOT):
    """(bundle_version, marketplace_version), either may be None."""
    bundle = _read_json(os.path.join(root, "bundle", ".claude-plugin",
                                      "plugin.json"))
    market = _read_json(os.path.join(root, ".claude-plugin",
                                      "marketplace.json"))
    bv = (bundle or {}).get("version")
    mv = None
    for p in (market or {}).get("plugins", []):
        if p.get("name") == "brother":
            mv = p.get("version")
    return bv, mv


def remote_has_tag(tag, remote=PUBLIC_REMOTE):
    """True / False / None (None: the remote could not be asked)."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", remote, "refs/tags/%s" % tag],
            capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 - any transport failure is the same NO-DATA  # sbe: allow-silent this function's own docstring documents the None sentinel; the sole caller below turns it into a named "public remote unreachable" NO-DATA line, never a crash
        return None
    if proc.returncode != 0:
        return None
    return bool((proc.stdout or "").strip())


def tag_bundle_version(tag, checkout=PUBLIC_CHECKOUT):
    """The version the tag's own tree declares, or None when unreadable."""
    if not os.path.exists(os.path.join(checkout, ".git")):
        return None
    proc = subprocess.run(
        ["git", "-C", checkout, "show",
         "%s^{commit}:bundle/.claude-plugin/plugin.json" % tag],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout).get("version")
    except ValueError:  # sbe: allow-silent this function's own docstring documents the None-when-unreadable contract; the sole caller turns it into a named "checkout cannot show tree" NO-DATA line
        return None


CHECKED_FILES = (
    os.path.join(".claude-plugin", "plugin.json"),
    # The Codex manifest, added 2026-09-04 for ship gate 6. An installed
    # Brother is one directory serving both clients, so the Codex manifest is
    # release-critical in exactly the way the Claude one already is: an
    # installed copy whose .codex-plugin/plugin.json is not the tag's bytes
    # is a Codex user running something nobody released. A tag cut before
    # this file existed does not carry it; that is reported as a named
    # NO-DATA on this one file (see installed_matches) rather than being
    # allowed to turn the whole comparison NO-DATA.
    os.path.join(".codex-plugin", "plugin.json"),
    os.path.join("runtime", "brother_run.py"),
    os.path.join("runtime", "RUNTIME-MANIFEST.json"),
)


def _tag_bundle_bytes(tag, rel, checkout=PUBLIC_CHECKOUT):
    """The bytes of one bundle file AS THE TAG SHIPPED THEM, or None when the
    checkout cannot show the tag. The release is frozen at the tag; HEAD
    moving on is normal development, never release drift, so the installed
    copy is compared against the TAG, never against the working tree
    (CORRECTED 2026-08-31: comparing installed against HEAD's bundle read
    every normal post-release commit as a mismatch, the false FAIL a peer
    caught on a fresh clone)."""
    if not os.path.exists(os.path.join(checkout, ".git")):
        return None
    proc = subprocess.run(
        ["git", "-C", checkout, "show",
         "%s^{commit}:bundle/%s" % (tag, rel.replace(os.sep, "/"))],
        capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout


#: The shipped runtime, as a path prefix inside the tag's tree. Drift is
#: measured over EVERY file under it, not over one manifest.
RUNTIME_PREFIX = "bundle/runtime"


def tag_tree_files(tag, prefix, checkout=PUBLIC_CHECKOUT):
    """Every path the tag ships under `prefix`. None (never an empty list
    standing in for it) when the tag tree cannot be listed, so an unreadable
    checkout reads NO-DATA instead of "no files, therefore no drift"."""
    if not os.path.exists(os.path.join(checkout, ".git")):
        return None
    proc = subprocess.run(
        ["git", "-C", checkout, "ls-tree", "-r", "--name-only",
         "%s^{commit}" % tag, "--", prefix],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return [p for p in (proc.stdout or "").splitlines() if p.strip()]


def runtime_drift(tag, root=ROOT, checkout=PUBLIC_CHECKOUT):
    """(drifted, reason, checked): which SHIPPED RUNTIME FILES differ here
    from what `tag` shipped.

    E80: this check used to compare exactly one file, runtime/RUNTIME-
    MANIFEST.json, so a one byte tamper in any other shipped runtime file
    (brother_run.py included) passed while the manifest still matched. It now
    reads the tag's own file list under bundle/runtime/ and compares every
    one, which is the set the release actually ships.

    drifted is a list (empty means every shipped runtime file is byte
    identical) and reason is None; on NO-DATA drifted is None and reason
    names why. A file the tag ships that is missing or unreadable here counts
    as DRIFT, not NO-DATA: the release claims to ship it."""
    paths = tag_tree_files(tag, RUNTIME_PREFIX, checkout)
    if paths is None:
        return None, ("tag %s tree unreadable; shipped-runtime drift not "
                      "checked" % tag), 0
    if not paths:
        return None, ("tag %s ships no file under %s/" % (tag, RUNTIME_PREFIX)), 0
    drifted = []
    for rel in paths:
        want = _tag_bundle_bytes(tag, rel.split("bundle/", 1)[-1], checkout)
        if want is None:
            return None, ("tag %s could not show %s" % (tag, rel)), 0
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                if fh.read() != want:
                    drifted.append(rel)
        except OSError:
            drifted.append(rel)
    return drifted, None, len(paths)


def shipped_runtime_matches_its_source(root=ROOT):
    """(ok, problems): does the bundle/runtime this tree would ship still
    equal its scripts/ source?

    E80 item 4: that comparison already existed, in scripts/bundle_runtime.py
    (`check`), and the release identity chain simply never asked it, so a
    release could be certified while the shipped runtime and the source it is
    generated from disagreed. This WIRES THE EXISTING COMPARISON IN rather
    than writing a second one: every byte here is hashed by bundle_runtime's
    own code. (None, reason) when that module cannot be imported, which is
    NO-DATA, never a pass."""
    try:
        sys.path.insert(0, os.path.join(root, "scripts"))
        import bundle_runtime as BR
    except ImportError as exc:
        return None, "scripts/bundle_runtime.py could not be imported (%s)" % exc
    try:
        ok, problems, _closure = BR.check(
            scripts_dir=os.path.join(root, "scripts"),
            runtime_dir=os.path.join(root, "bundle", "runtime"))
    except OSError as exc:
        return None, "bundle_runtime.check could not read the tree (%s)" % exc
    return bool(ok), problems


def installed_matches(version, tag, cache=INSTALL_CACHE,
                      checkout=PUBLIC_CHECKOUT):
    """(verdict, detail): compares the INSTALLED plugin for `version` against
    the released `tag`'s bundle bytes. 'match' / 'mismatch: <file>' /
    None+reason (not installed, or the tag tree is unreadable)."""
    inst = os.path.join(cache, version)
    if not os.path.isdir(inst):
        return None, "no installed copy of %s on this machine" % version
    absent, compared = [], []
    for rel in CHECKED_FILES:
        want = _tag_bundle_bytes(tag, rel, checkout)
        if want is None:
            # Two different facts share this None: the checkout cannot show
            # the tag at all, and the tag simply does not carry this file.
            # Telling them apart is what keeps a file added after the last
            # cut (the Codex manifest, today) from turning a working check
            # into a permanent "tree unreadable" NO-DATA. If NOTHING could be
            # read, it is the tree; if some file was read, the tag is fine
            # and this one file is not in this release.
            absent.append(rel)
            continue
        compared.append(rel)
        try:
            with open(os.path.join(inst, rel), "rb") as fa:
                if fa.read() != want:
                    return "mismatch", rel
        except OSError as exc:
            return "mismatch", "%s unreadable: %s" % (rel, exc)
    if not compared:
        return None, ("tag %s tree unreadable; installed-vs-released "
                      "byte check skipped" % tag)
    detail = "installed bytes equal the released tag's bundle"
    if absent:
        detail += ("; NO-DATA on %s (tag %s does not carry %s)"
                   % (", ".join(absent), tag,
                      "them" if len(absent) > 1 else "it"))
    return "match", detail


def _call(args, parse_json=True):
    """The one subprocess helper behind every link below (gh and plain git
    alike). Returns (True, payload) on a clean call: payload is the
    parsed JSON stdout when parse_json, else the raw stripped stdout.
    Returns (False, reason) on anything else: the executable missing, a
    non-zero exit (reason is the tool's own stderr, which is how a
    genuinely-absent release or ruleset is told apart from an
    infrastructure failure by the callers below), or unparseable JSON.
    No retries."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=30)
    except FileNotFoundError:
        return False, "%s not installed" % args[0]
    except Exception as exc:  # noqa: BLE001 - any transport failure is one NO-DATA line, never a crash
        return False, "%s call failed: %s" % (args[0], exc)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        return False, stderr or ("%s exited %d" % (args[0], proc.returncode))
    out = (proc.stdout or "").strip()
    if not parse_json:
        return True, out
    try:
        return True, json.loads(out) if out else {}
    except ValueError:
        return False, "unparseable JSON from: %s" % " ".join(args)


def release_info(tag, repo=PUBLIC_REPO):
    """(status, payload_or_reason): 'ok' with the release's JSON dict
    (tagName, isDraft, isPrerelease, targetCommitish, name), 'missing'
    when gh itself reports no such release (a genuine contradiction of
    the identity chain, not an absence of input), or 'nodata' when gh
    could not be asked at all (absent, unauthenticated, network down)."""
    ok, result = _call(["gh", "release", "view", tag, "--repo", repo,
                        "--json",
                        "tagName,isDraft,isPrerelease,targetCommitish,name"])
    if ok:
        return "ok", result
    if "release not found" in (result or "").lower():
        return "missing", result
    return "nodata", result


def remote_tag_commit(tag, remote=PUBLIC_REMOTE):
    """(status, commit_or_reason): 'ok' with the peeled (^{}) commit sha
    git ls-remote reports for `tag` on the public remote, else 'nodata'
    naming why (unreachable, or no peeled line for an unexpected tag
    shape). No refspec pattern is passed to ls-remote: git only emits a
    tag's peeled ^{} line for an UNFILTERED listing, not for one matched
    by a pattern (proven live: `git ls-remote --tags <remote> v1.0.1`
    returns just the tag ref, no peeled line; the bare, pattern-free form
    returns both), so the full tag list is fetched once and matched by
    the exact "refs/tags/<tag>^{}" refname to avoid a partial-name
    collision (e.g. v1.0.1 vs v1.0.10)."""
    ok, out = _call(["git", "ls-remote", "--tags", remote], parse_json=False)
    if not ok:
        return "nodata", out
    wanted = "refs/tags/%s^{}" % tag
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1] == wanted:
            return "ok", parts[0]
    return "nodata", "no peeled tag line for %s on the public remote" % tag


def gh_tag_commit(tag, repo=PUBLIC_REPO):
    """(status, commit_or_reason): 'ok' with the commit sha the GitHub API
    resolves `tag` to, via the ref and then, for an annotated tag, the
    tag object it points at; 'nodata' when either gh call fails."""
    ok, ref = _call(["gh", "api", "repos/%s/git/ref/tags/%s" % (repo, tag)])
    if not ok:
        return "nodata", ref
    obj = ref.get("object") or {}
    sha, kind = obj.get("sha"), obj.get("type")
    if not sha:
        return "nodata", "GitHub ref for %s carried no object sha" % tag
    if kind == "commit":
        return "ok", sha  # a lightweight tag: the ref IS the commit
    ok2, tagobj = _call(["gh", "api", "repos/%s/git/tags/%s" % (repo, sha)])
    if not ok2:
        return "nodata", tagobj
    commit = (tagobj.get("object") or {}).get("sha")
    if not commit:
        return "nodata", "GitHub tag object for %s carried no commit sha" % tag
    return "ok", commit


def branch_ruleset_requires_pr(branch="main", repo=PUBLIC_REPO):
    """(status, detail): 'ok' when an active ruleset on `branch` includes
    a rule of type 'pull_request', 'missing' when the (readable) rule
    list lacks one, 'nodata' when the rules could not be read."""
    ok, rules = _call(["gh", "api",
                       "repos/%s/rules/branches/%s" % (repo, branch)])
    if not ok:
        return "nodata", rules
    if not isinstance(rules, list):
        return "nodata", "unexpected rules payload for %s" % branch
    if any(isinstance(r, dict) and r.get("type") == "pull_request"
          for r in rules):
        return "ok", "an active ruleset on %s requires pull requests" % branch
    return "missing", "no active ruleset on %s requires pull requests" % branch


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--public-checkout", default=None,
                    help="local checkout of the public repository, used to "
                         "read a released tag's own tree (default: %s)"
                         % PUBLIC_CHECKOUT)
    ap.add_argument("--install-cache", default=None,
                    help="root of the installed plugin cache to compare "
                         "against the released tag (default: %s)"
                         % INSTALL_CACHE)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    # Read at call time, never bound as an argparse default: an argparse
    # default evaluates once at definition time, so a test's monkeypatch of
    # these globals would be inert against it (this estate's recorded
    # lesson, the same reason ROOT and INSTALL_CACHE below are passed
    # explicitly rather than relied on as function defaults).
    public_checkout = args.public_checkout or PUBLIC_CHECKOUT
    install_cache = args.install_cache or INSTALL_CACHE

    failures, nodata = [], []

    bv, mv = declared_versions(ROOT)
    if not bv and not mv:
        print("NO-DATA: neither version declaration could be read")
        return 2
    if bv != mv:
        failures.append("version sites disagree: bundle=%s marketplace=%s"
                        % (bv, mv))
    else:
        print("OK: both version sites declare %s" % bv)

    version = bv or mv
    version_tag = "v%s" % version

    # Populations this invariant does not examine, named so a reader can
    # never mistake their silence for coverage. Not failures; printed on
    # every run, and counted in the closing NO-DATA total.
    nodata.append("tag signature not examined (tags are unsigned; the "
                  "README names it as a limit)")
    nodata.append("hook scoping not examined")
    nodata.append("virgin-install run for %s not examined" % version_tag)

    # A pre-release version (a suffix after a hyphen, e.g. 0.9.8-dev) is
    # UNRELEASED working state, not a shipped artifact. It has no public tag
    # and no installed copy by design, so the only thing to check is that the
    # two declaration sites agree; requiring a tag for it would be a false
    # FAIL. This is the honest state of a repository between releases that
    # carries changes for the NEXT version.
    if "-" in version:
        print("OK: %s is an unreleased development version; no public tag or "
              "installed copy expected" % version)
        for line in nodata:
            print("NO-DATA: %s (not a pass, and not a contradiction)" % line)
        if failures:
            for line in failures:
                print("FAIL: %s" % line)
            return 1
        print("release-invariant: development version %s, sites agree, "
              "nothing released to contradict (%d link(s) NO-DATA)"
              % (version, len(nodata)))
        return 0

    notes = os.path.join(ROOT, "docs", "releases", "%s.md" % version)
    if os.path.isfile(notes):
        print("OK: docs/releases/%s.md exists" % version)
    else:
        failures.append("no release notes at docs/releases/%s.md" % version)

    tag = "v%s" % version
    has = remote_has_tag(tag)
    #: Whether the public repository is KNOWN to carry the tag. False both
    #: when the remote says no and when the remote could not be asked, which
    #: is what the tag-dependent links below need: neither state contradicts
    #: anything, and both are already named as their own NO-DATA line.
    tag_present = bool(has)
    if has is None:
        nodata.append("public remote unreachable; tag %s not checked" % tag)
    elif not has:
        # THE MOMENT BETWEEN THE CUT AND THE TAG. A version declared here
        # with nothing pushed public yet is not a contradiction, it is the
        # ordinary state of a release branch before its tag exists. Calling
        # it a FAIL made the tag UNREACHABLE: scripts/export_public.py runs
        # the export tree's readiness gate at tag time, that gate runs this
        # invariant as the non-critical "Reproducible release artifact" item,
        # and a FAIL there refuses the push that would create the very tag
        # whose absence produced the FAIL. A tag that EXISTS and disagrees
        # with the declared version is still a FAIL, below.
        nodata.append("public repository carries no tag %s yet; the cut "
                      "precedes the tag" % tag)
    else:
        print("OK: public repository carries %s" % tag)
        tv = tag_bundle_version(tag, public_checkout)
        if tv is None:
            nodata.append("local public checkout cannot show %s's tree; "
                          "byte check skipped" % tag)
        elif tv != version:
            failures.append("tag %s ships bundle version %s, not %s"
                            % (tag, tv, version))
        else:
            print("OK: %s's own tree declares %s" % (tag, version))

        # RUNTIME MUTATION UNDER THE SAME VERSION: the working bundle's own
        # runtime bytes must equal what the tag of THIS version shipped. When
        # they differ while the version string has not moved, the release
        # rule "no shipped-runtime mutation under the same version" is broken
        # and the working version must bump before anything is released. This
        # is the exact regression the directive watches for.
        drifted, reason, checked = runtime_drift(tag, ROOT, public_checkout)
        if drifted is None:
            nodata.append(reason)
        elif drifted:
            failures.append(
                "shipped runtime content has changed since tag %s but the "
                "version string is still %s; %d of %d shipped runtime file(s) "
                "differ (%s); bump the version before releasing (no shipped-"
                "runtime mutation under the same version)"
                % (tag, version, len(drifted), checked,
                   ", ".join(drifted[:5])
                   + (", ..." if len(drifted) > 5 else "")))
        else:
            print("OK: all %d runtime file(s) tag %s ships are byte-identical "
                  "here" % (checked, tag))

    shipped_ok, shipped_detail = shipped_runtime_matches_its_source(ROOT)
    if shipped_ok is None:
        nodata.append(shipped_detail)
    elif not shipped_ok:
        failures.append(
            "the bundle/runtime this tree would ship disagrees with its own "
            "scripts/ source in %d place(s): %s"
            % (len(shipped_detail), "; ".join(shipped_detail[:3])
               + ("; ..." if len(shipped_detail) > 3 else "")))
    else:
        print("OK: bundle/runtime equals its scripts/ source "
              "(scripts/bundle_runtime.py's own check)")

    verdict, detail = installed_matches(version, tag, install_cache,
                                        public_checkout)
    if verdict is None:
        nodata.append(detail)
    elif verdict == "match":
        print("OK: installed %s %s" % (version, detail))
    else:
        failures.append("installed %s differs from the released tag %s: %s"
                        % (version, tag, detail))

    # WHAT GITHUB ITSELF REPORTS, not the local git object model above:
    # the git tag can exist (checked via remote_has_tag) while GitHub
    # carries no Release for it and no branch protection at all, and the
    # links above never ask, so a broken chain reads OK. This is the exact
    # gap a reviewer found on v1.0.1.
    rstatus, rpayload = release_info(tag)
    if rstatus == "nodata":
        nodata.append("GitHub Release for %s not checked: %s"
                      % (tag, rpayload))
    elif rstatus == "missing" and not tag_present:
        # Same moment as above: no tag yet, so no Release yet. A Release
        # missing while the TAG EXISTS stays a FAIL (that is the v1.0.1
        # defect, a tag nobody turned into a Release).
        nodata.append("no GitHub Release for %s yet; the cut precedes the "
                      "tag" % tag)
    elif rstatus == "missing":
        failures.append("no GitHub Release exists for %s" % tag)
    else:
        print("OK: GitHub Release exists for %s" % tag)
        if rpayload.get("isDraft"):
            failures.append("GitHub Release %s is a draft" % tag)
        elif rpayload.get("isPrerelease"):
            failures.append("GitHub Release %s is marked prerelease" % tag)
        else:
            print("OK: GitHub Release %s is published, not draft, not "
                  "prerelease" % tag)

    cstatus1, c1 = remote_tag_commit(tag)
    cstatus2, c2 = gh_tag_commit(tag)
    if cstatus1 != "ok" or cstatus2 != "ok":
        reasons = [r for st, r in ((cstatus1, c1), (cstatus2, c2))
                  if st != "ok"]
        nodata.append("GitHub tag commit for %s not checked: %s"
                      % (tag, "; ".join(reasons)))
    elif c1 != c2:
        failures.append("tag %s resolves to commit %s via git but %s via "
                        "the GitHub API" % (tag, c1, c2))
    else:
        print("OK: GitHub Release tag %s resolves to commit %s" % (tag, c1))

    bstatus, bdetail = branch_ruleset_requires_pr()
    if bstatus == "nodata":
        nodata.append("public main branch protection not checked: %s"
                      % bdetail)
    elif bstatus == "missing":
        failures.append("public main has no active ruleset requiring pull "
                        "requests")
    else:
        print("OK: %s" % bdetail)

    for line in nodata:
        print("NO-DATA: %s (not a pass, and not a contradiction)" % line)
    if failures:
        for line in failures:
            print("FAIL: %s" % line)
        print("release-invariant: %d broken link(s) in the identity chain"
              % len(failures))
        return 1
    print("release-invariant: every reachable link agrees on %s (%d "
          "link(s) NO-DATA)" % (version, len(nodata)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
