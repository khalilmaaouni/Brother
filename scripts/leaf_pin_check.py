#!/usr/bin/env python3
"""Does the umbrella promise the release tag the leaf actually published?

THE DEFECT THIS EXISTS FOR, 2026-08-24. BrotherSBE released 3.4.2. The
umbrella went on advertising 3.4.1 in all three of its declaration sites while
the resolver delivered 3.4.2, and a pilot runner was one command away from
meeting it. An internal consistency check would NOT have caught it: the three
sites agreed with each other perfectly. They were all wrong together. So the
comparison that matters is against the LEAF, and it needs the network.

M6 ADAPTATION, 2026-08-31 (docs/plan/ONE-REPO-TRANSITION-2026-08-31.md).
Before the cutover, "the leaf" meant BrotherModeUp's or BrotherSBE's OWN
repository, each publishing its OWN semver tags independently of the
umbrella; the two-word bug above was exactly that independence going wrong.
After the cutover, both products ship from THIS repository's products/
subtrees at ONE shared release tag (D1, D6): there is no longer a second
repository to drift against, because both leaves ARE this repository now.
So the comparison this check makes moved with them: it now asks whether
marketplace.json's git-subdir `ref` for brothermode and brothersbe matches
the newest tag actually published on THIS repository (both leaves share one
URL below, on purpose: a mismatch BETWEEN them is itself now a defect this
check would catch, where before there was nothing to compare). The check
kept, not deleted: the drift class it was written for (an umbrella that
advertises a version older than what actually shipped) is exactly as
possible post-cutover as before, just measured against the one shared tag
instead of two independent ones.

DROPPED, not adapted: the old comparison against bundle's own
`dependencies` array (e.g. "brothermode@^3.4.2"). That string is a
PRODUCT-version compatibility constraint (does the bundle still work with
brothermode 3.4.2), a different number in a different space from the
umbrella's OWN release tag (v0.9.8); comparing it against the one repo's
newest tag would be a false MISMATCH by construction, not a real defect.
Whether the bundle's declared product-version pin matches what a given
release tag's tree actually ships is release_invariant.py's job (its link 3
already reads a tag's own bundle manifest), not this file's.

Exit contract, mirroring scripts/cleanse.sh and scripts/bundle-install-smoke.sh:
  0  PASS      every declared pin matches the leaf's newest published tag
  1  FAIL      at least one disagrees
  2  NO-DATA   the leaf tags could not be read at all

NO-DATA IS NOT A PASS. It exits 2, distinct from 0, for the same reason the
sibling gates do: a check that could not look must never be mistaken for a
check that looked and found nothing wrong.

Tags are read with `git ls-remote --tags`, never with an API listing. This
estate recorded on 2026-08-23 that the API tag listing returned nothing for a
repository holding 36 tags, and silence looks exactly like zero.
"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Both names share one URL post-cutover: the one repository is now both
#: leaves at once (see the M6 ADAPTATION note above).
_ONE_REPO = "https://github.com/khalilmaaouni/Brother.git"
LEAVES = {
    "brothermode": _ONE_REPO,
    "brothersbe": _ONE_REPO,
}

TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$")


def newest_published_tag(url):
    """The newest plain vX.Y.Z tag on the remote, or None if it cannot be read.

    Deliberately ignores the ^{} dereference lines: an annotated tag appears
    twice and the version is the same on both."""
    try:
        out = subprocess.run(["git", "ls-remote", "--tags", url],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    found = []
    for line in out.stdout.splitlines():
        ref = line.split("\t")[-1]
        m = TAG.match(ref)
        if m:
            found.append(tuple(int(g) for g in m.groups()))
    if not found:
        return None
    return ".".join(str(n) for n in max(found))


def declared():
    """Every place the umbrella states a leaf's RELEASE TAG, with its file,
    so a failure names the site to edit rather than just the number.

    M6: a plugin whose `source` is the git-subdir form (both leaves, post
    cutover) is pinned by its `ref`, not its cosmetic top-level `version`
    (that field is the PRODUCT's own version, e.g. "3.4.2", a different
    number in a different space; see the M6 ADAPTATION note in this file's
    module docstring for why it is not compared here). A plugin whose
    `source` is anything else (a plain string, or a dict with no `ref`)
    falls back to its top-level `version`, so this still works for a plugin
    that has not moved to the subdir form."""
    sites = {name: [] for name in LEAVES}

    m = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for p in m["plugins"]:
        if p["name"] not in sites:
            continue
        source = p.get("source")
        if isinstance(source, dict) and source.get("ref"):
            sites[p["name"]].append(
                (".claude-plugin/marketplace.json (ref)",
                 source["ref"].lstrip("v")))
        else:
            sites[p["name"]].append(
                (".claude-plugin/marketplace.json (version)", p["version"]))
    return sites


def main():
    sites = declared()
    failures, nodata, checked = [], [], 0

    for name, url in sorted(LEAVES.items()):
        published = newest_published_tag(url)
        if published is None:
            nodata.append(name)
            print(f"leaf-pin-check: NO-DATA: could not read tags for {name} at {url}")
            continue
        if not sites[name]:
            nodata.append(name)
            print(f"leaf-pin-check: NO-DATA: {name} is declared nowhere in this umbrella")
            continue
        for where, ver in sites[name]:
            checked += 1
            if ver != published:
                failures.append(f"{name}: {where} says {ver}, the leaf published {published}")
        print(f"leaf-pin-check: {name} published {published}, "
              f"{len(sites[name])} declaration site(s) checked")

    if nodata:
        print(f"leaf-pin-check: NO-DATA for {', '.join(nodata)}; "
              f"this is NOT a pass, it is a check that could not look")
        return 2
    if failures:
        for f in failures:
            print(f"leaf-pin-check: MISMATCH: {f}")
        print(f"leaf-pin-check: FAILED: {len(failures)} of {checked} declarations "
              f"disagree with the leaf")
        return 1
    print(f"leaf-pin-check: PASSED: all {checked} declarations match the "
          f"leaves' newest published tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
