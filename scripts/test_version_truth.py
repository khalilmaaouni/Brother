#!/usr/bin/env python3
"""E3.2: one source of version truth for this repository's shipped plugins.

WHY THIS EXISTS. An external audit found three disagreeing BrotherSBE version
numbers inside this repository at once: the README table said 3.5.1, the
umbrella manifest said 3.7.0, and the plugin that actually installed was
3.7.1. No file was internally inconsistent with itself, so nothing here
noticed. .claude-plugin/marketplace.json is declared THE single source of
version truth (E3.2); every other shipped version string must repeat what it
says, never carry its own number.

This only checks strings that exist to REPEAT a plugin's version, not every
digit sequence in the file. A version string is one that appears within a
short distance of the plugin's own name (brothermode, brothersbe), the same
shape both README.md sites already use.

THE BLIND SPOT THIS FILE ONCE HAD, found by an outside audit at v0.9.9 and
closed here. Everything above compares README.md against the manifest, and
nothing compared the manifest against the thing the INSTALLER actually
resolves. So when products/brothersbe was re-synced to upstream 3.7.1 while
the manifest went on promising 3.7.0, this checker exited 0 over a real
mismatch that `sh scripts/bundle-install-smoke.sh` failed on in both modes.
A version-truth checker that never reads what installs is checking the
paperwork against itself. It now also compares, for every plugin whose
manifest source is a products/ subtree of this repository, the manifest's
version against that subtree's own .claude-plugin/plugin.json, which is the
file the resolver reads.

E32: a second field in that same plugin.json went unchecked the same way.
The shipped products/brothersbe manifest named a retired repository
(BrotherSBE) while the marketplace had already moved to installing it from
this repository (Brother), and nothing compared a subtree's own repository
field against the marketplace source url that resolves it, so a stranger
reading the installed manifest was pointed at a repository nobody ships
from anymore. It now also compares, for every plugin whose manifest source
names a git-subdir url, that subtree's plugin.json repository field against
the marketplace source url.

Exit 0 PASS, every found mention agrees with the manifest.
Exit 1 FAIL, at least one disagrees; every disagreement is named.
No em or en dashes.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".claude-plugin" / "marketplace.json"
README = ROOT / "README.md"

#: EVAD run 5 trial 6 widened the population: version strings that exist to
#: repeat a plugin's version also live in these two, and both were stale at
#: v0.9.10 while this checker read only README.md.
TEXT_FILES = (
    README,
    ROOT / "docs" / "VERSIONING.md",
    ROOT / "docs" / "plan" / "PROJECT.public.md",
)

# Word-bounded on both sides: "brother" must not match inside "brothermode"
# or "brothersbe", which is what a plain substring search does.
VERSION_NEAR_NAME = "(?<![A-Za-z]){name}(?![A-Za-z])[^0-9]{{0,60}}(\\d+\\.\\d+\\.\\d+)"


def manifest_plugins():
    return json.loads(MANIFEST.read_text())["plugins"]


def manifest_versions(plugins):
    return {p["name"]: p["version"] for p in plugins}


def subtree_path(plugin):
    """The products/ directory this plugin installs from, or None.

    The source form is read from the manifest rather than assumed: a
    git-subdir source carries its subtree in source["path"], while the
    bundle's source is a plain relative string and owns no subtree.
    """
    src = plugin.get("source")
    if not isinstance(src, dict):
        return None
    path = src.get("path", "")
    if not path.startswith("products/"):
        return None
    return ROOT / path


def subtree_mismatches(plugins):
    """Every (name, manifest_version, plugin_json_version, file) where the
    manifest's promise disagrees with the plugin.json the installer resolves.

    A declared subtree with no .claude-plugin/plugin.json is reported the same
    way, with the resolved version given as MISSING: the manifest promising a
    version for a subtree that carries no manifest of its own is the same
    class of untruth, and it must never read as agreement.
    """
    problems = []
    for plugin in plugins:
        root = subtree_path(plugin)
        if root is None:
            continue
        leaf = root / ".claude-plugin" / "plugin.json"
        if not leaf.is_file():
            problems.append((plugin["name"], plugin["version"], "MISSING", leaf))
            continue
        got = json.loads(leaf.read_text()).get("version")
        if got != plugin["version"]:
            problems.append((plugin["name"], plugin["version"], got, leaf))
    return problems


def subtree_repository_mismatches(plugins):
    """Every (name, marketplace_url, plugin_json_repository, file) where a
    shipped plugin.json's repository field disagrees with the marketplace
    git-subdir source url that actually installs it.

    E32: a retired repository was baked into a shipped plugin.json's own
    repository field, and nothing here ever read that field, so the
    marketplace could move a plugin to install from a different repository
    while the installed manifest went on naming where it used to live. A
    missing repository field is reported as MISSING: an absent promise is
    not agreement, the same rule subtree_mismatches already applies to a
    missing version.
    """
    problems = []
    for plugin in plugins:
        root = subtree_path(plugin)
        if root is None:
            continue
        src = plugin.get("source")
        url = src.get("url") if isinstance(src, dict) else None
        if url is None:
            continue
        leaf = root / ".claude-plugin" / "plugin.json"
        if not leaf.is_file():
            continue  # already reported by subtree_mismatches
        got = json.loads(leaf.read_text()).get("repository", "MISSING")
        if got != url:
            problems.append((plugin["name"], url, got, leaf))
    return problems


def find_mismatches(text, versions):
    """Every (line_no, name, found, want) where a version-shaped string near
    a plugin's name disagrees with the manifest's version for that plugin."""
    problems = []
    for name, want in versions.items():
        pattern = re.compile(VERSION_NEAR_NAME.format(name=re.escape(name)), re.IGNORECASE)
        for m in pattern.finditer(text):
            got = m.group(1)
            if got != want:
                line_no = text.count("\n", 0, m.start()) + 1
                problems.append((line_no, name, got, want))
    return problems


def main():
    plugins = manifest_plugins()
    versions = manifest_versions(plugins)

    failed = False

    subtree = subtree_mismatches(plugins)
    if subtree:
        print("FAILED: the manifest promises a version the installed subtree does not carry")
        for name, want, got, leaf in subtree:
            rel = leaf.relative_to(ROOT)
            print(f"  {name}: manifest says {want}, {rel} says {got}")
        failed = True

    repo_mismatches = subtree_repository_mismatches(plugins)
    if repo_mismatches:
        print("FAILED: a shipped plugin.json's repository field disagrees "
              "with the marketplace source url that installs it")
        for name, want, got, leaf in repo_mismatches:
            rel = leaf.relative_to(ROOT)
            print(f"  {name}: marketplace source url is {want}, {rel} says {got}")
        failed = True

    for path in TEXT_FILES:
        if not path.is_file():
            print(f"FAILED: {path.relative_to(ROOT)} is missing, so its version "
                  f"strings cannot be checked; absence is not agreement")
            failed = True
            continue
        problems = find_mismatches(path.read_text(), versions)
        if problems:
            rel = path.relative_to(ROOT)
            print(f"FAILED: shipped version strings in {rel} disagree with "
                  f".claude-plugin/marketplace.json")
            for line_no, name, got, want in problems:
                print(f"  {rel} line {line_no}: {name} shown as {got}, manifest says {want}")
            failed = True

    # The marketplace's own metadata.version must repeat the bundle's version:
    # it read 0.9.8 against a shipped 0.9.10 at EVAD run 5 trial 6.
    doc = json.loads(MANIFEST.read_text())
    meta_version = (doc.get("metadata") or {}).get("version")
    bundle_version = versions.get("brother")
    if meta_version != bundle_version:
        print(f"FAILED: marketplace metadata.version says {meta_version}, the "
              f"brother bundle plugin says {bundle_version}")
        failed = True

    # The stage claim must match the tree: with product code shipping from
    # products/, a front page still saying router-only is the stale text an
    # external critic disproved by listing the repository.
    if (ROOT / "products" / "brothermode").is_dir():
        readme_text = README.read_text() if README.is_file() else ""
        if "marketplace and router only" in readme_text:
            print("FAILED: README.md still claims this repository is the "
                  "marketplace and router only, while products/ ships product "
                  "code")
            failed = True

    if failed:
        return 1

    checked = [p["name"] for p in plugins if subtree_path(p) is not None]
    shown = ", ".join(f"{k}={v}" for k, v in versions.items())
    files = ", ".join(str(p.relative_to(ROOT)) for p in TEXT_FILES)
    print(f"PASSED: every shipped version string in {files} agrees with "
          f".claude-plugin/marketplace.json ({shown}), metadata.version matches the "
          f"bundle, the manifest agrees with the plugin.json the installer "
          f"resolves for {', '.join(checked)}, and every plugin.json repository "
          f"field agrees with the marketplace source url for {', '.join(checked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
