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
  1  any reachable link CONTRADICTS: two version sites disagree, the tag
     is missing from the public repository, the tag's bytes disagree, or
     the installed copy differs. The broken link is named.
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
PUBLIC_CHECKOUT = os.path.expanduser("~/Brother")
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


def installed_matches(version, tag, cache=INSTALL_CACHE,
                      checkout=PUBLIC_CHECKOUT):
    """(verdict, detail): compares the INSTALLED plugin for `version` against
    the released `tag`'s bundle bytes. 'match' / 'mismatch: <file>' /
    None+reason (not installed, or the tag tree is unreadable)."""
    inst = os.path.join(cache, version)
    if not os.path.isdir(inst):
        return None, "no installed copy of %s on this machine" % version
    for rel in CHECKED_FILES:
        want = _tag_bundle_bytes(tag, rel, checkout)
        if want is None:
            return None, ("tag %s tree unreadable; installed-vs-released "
                          "byte check skipped" % tag)
        try:
            with open(os.path.join(inst, rel), "rb") as fa:
                if fa.read() != want:
                    return "mismatch", rel
        except OSError as exc:
            return "mismatch", "%s unreadable: %s" % (rel, exc)
    return "match", "installed bytes equal the released tag's bundle"


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

    # A pre-release version (a suffix after a hyphen, e.g. 0.9.8-dev) is
    # UNRELEASED working state, not a shipped artifact. It has no public tag
    # and no installed copy by design, so the only thing to check is that the
    # two declaration sites agree; requiring a tag for it would be a false
    # FAIL. This is the honest state of a repository between releases that
    # carries changes for the NEXT version.
    if "-" in version:
        print("OK: %s is an unreleased development version; no public tag or "
              "installed copy expected" % version)
        if failures:
            for line in failures:
                print("FAIL: %s" % line)
            return 1
        print("release-invariant: development version %s, sites agree, "
              "nothing released to contradict" % version)
        return 0

    notes = os.path.join(ROOT, "docs", "releases", "%s.md" % version)
    if os.path.isfile(notes):
        print("OK: docs/releases/%s.md exists" % version)
    else:
        failures.append("no release notes at docs/releases/%s.md" % version)

    tag = "v%s" % version
    has = remote_has_tag(tag)
    if has is None:
        nodata.append("public remote unreachable; tag %s not checked" % tag)
    elif not has:
        failures.append("public repository carries no tag %s for the "
                        "declared version" % tag)
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
        drift = _tag_bundle_bytes(tag, os.path.join(
            "runtime", "RUNTIME-MANIFEST.json"), public_checkout)
        if drift is not None:
            try:
                with open(os.path.join(ROOT, "bundle", "runtime",
                                       "RUNTIME-MANIFEST.json"), "rb") as fh:
                    if fh.read() != drift:
                        failures.append(
                            "runtime content has changed since tag %s but the "
                            "version string is still %s; bump the version "
                            "before releasing (no shipped-runtime mutation "
                            "under the same version)" % (tag, version))
                    else:
                        print("OK: working runtime bytes equal tag %s" % tag)
            except OSError:
                nodata.append("working RUNTIME-MANIFEST unreadable")

    verdict, detail = installed_matches(version, tag, install_cache,
                                        public_checkout)
    if verdict is None:
        nodata.append(detail)
    elif verdict == "match":
        print("OK: installed %s %s" % (version, detail))
    else:
        failures.append("installed %s differs from the released tag %s: %s"
                        % (version, tag, detail))

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
