#!/usr/bin/env python3
"""retire_catalogs.py: the U8 catalog edit, applied at the 1.1.0 cut.

Owner ruling 2026-09-21 (WBS U8): the brothermode, brothersbe and brotherds
marketplace entries retire when 1.1.0 is released. This script performs
exactly that edit and nothing else, so the cut (scripts/cut.py, C0 lane) can
call it and scripts/test_catalog_end_state.py can prove the result:

  .claude-plugin/marketplace.json   keeps ONE plugin entry, brother -> bundle
  .cursor-plugin/marketplace.json   keeps ONE plugin entry, brother -> bundle
  .agents/plugins/marketplace.json  already lists only brother; untouched
  products/brothermode/.claude-plugin/marketplace.json   deleted
  products/brothersbe/.claude-plugin/marketplace.json    deleted

Everything else in each catalog (name, owner, metadata, the brother entry's
own fields) is preserved byte for byte in meaning; the files are rewritten
with their existing indentation. Idempotent: a second run changes nothing
and exits 0. `--check` writes nothing and exits 1 while the edit is still
pending, 0 once it has been applied.

Not done here, on purpose: the owner runs this through the cut, never a
session by hand before the tag (the ruling says "when we release 1.1.0").

Python 3, standard library only.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donecheck_u8 as DU  # noqa: E402
import version_source as VS  # noqa: E402

KEEP = "brother"
#: Generated from the kept catalog by scripts/surface_budget.py. The apply regenerates it, because a tree whose
#: catalogs list one plugin while this file still lists four is not the end state (docs/plan/specs/OP1.md 5.4).
BUNDLE_MANIFEST = os.path.join("bundle", "MANIFEST.json")
#: The first release that carries the one plugin state (owner ruling 2026-09-21).
RETIRE_AT = (1, 1, 0)
CATALOGS = (
    os.path.join(".claude-plugin", "marketplace.json"),
    os.path.join(".cursor-plugin", "marketplace.json"),
)
#: Read and checked, never edited: it already lists only brother.
AGENTS = os.path.join(".agents", "plugins", "marketplace.json")
DELETE = (
    os.path.join("products", "brothermode", ".claude-plugin", "marketplace.json"),
    os.path.join("products", "brothersbe", ".claude-plugin", "marketplace.json"),
)
NOT_APPLICABLE = "NOT APPLICABLE below %s" % ".".join(str(n) for n in RETIRE_AT)
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def _no_duplicate_keys(pairs):
    """JSON object hook: a key named twice is corrupt input, never the last one."""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError("the key %r is named twice" % (key,))
        seen.add(key)
    return dict(pairs)


def _version_tuple(version):
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ValueError("the version must be a string like 1.1.0, not %r" % (version,))
    return tuple(int(n) for n in version.split("."))


def applies(version):
    """True only from RETIRE_AT upward. A version that is not X.Y.Z raises."""
    return _version_tuple(version) >= RETIRE_AT


def _load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ValueError as exc:
        raise ValueError("%s: not valid JSON (%s)" % (path, exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("plugins"), list):
        raise ValueError("%s: not a catalog with a plugins list" % path)
    for entry in doc["plugins"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("%s: a plugin entry without a string name" % path)
    return doc, VS.detect_indent(text), text.endswith("\n")


def _names(doc):
    return [p["name"] for p in doc["plugins"]]


def pending(root=ROOT):
    """Paths still to change, or [] when the end state holds. Raises on an
    unreadable catalog (NO-DATA blocks, never reads as done)."""
    todo = []
    for rel in CATALOGS:
        doc, _, _ = _load(os.path.join(root, rel))
        if _names(doc) != [KEEP]:
            todo.append(rel)
    for rel in DELETE:
        if os.path.exists(os.path.join(root, rel)):
            todo.append(rel)
    return todo


def end_state_problems(root=ROOT):
    """The end state of docs/plan/specs/OP1.md section 5.4, read by the ONE
    reader, donecheck_u8.catalog_problems, so the cut chain's check and the
    one plugin unit's reader cannot disagree. One line per problem; [] is the
    end state."""
    return DU.catalog_problems(root)


def _regenerate_bundle_manifest(root):
    """Rewrite bundle/MANIFEST.json from the catalogs as they now stand, with
    the tree's OWN scripts/surface_budget.py (it reads the tree it lives in).
    Returns True when the bytes changed. A tree with no bundle manifest has
    nothing to regenerate. A manifest that exists and cannot be regenerated
    raises: the catalogs would say one plugin and the manifest four."""
    path = os.path.join(root, BUNDLE_MANIFEST)
    if not os.path.exists(path):
        return False
    tool = os.path.join(root, "scripts", "surface_budget.py")
    if not os.path.isfile(tool):
        raise OSError("%s exists but %s does not, so it cannot be regenerated"
                      % (BUNDLE_MANIFEST, os.path.join("scripts", "surface_budget.py")))
    with open(path, "rb") as fh:
        before = fh.read()
    try:
        proc = subprocess.run([sys.executable, "-B", tool, "--manifest", "--write"],
                              cwd=root, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError("%s could not be regenerated (%s)" % (BUNDLE_MANIFEST, exc))
    if proc.returncode != 0:
        raise OSError("%s: surface_budget.py --manifest --write exited %d: %s"
                      % (BUNDLE_MANIFEST, proc.returncode,
                         (proc.stderr or proc.stdout).strip()[-300:]))
    with open(path, "rb") as fh:
        return fh.read() != before


def _kept(doc, rel):
    kept = [p for p in doc["plugins"] if p["name"] == KEEP]
    if len(kept) != 1:
        raise ValueError("%s: expected exactly one %s entry, found %d"
                         % (rel, KEEP, len(kept)))
    return kept


def _stamp(entry, version):
    """Write the cut version into the kept entry where it carries the fields."""
    if "version" in entry:
        entry["version"] = version
    source = entry.get("source")
    if isinstance(source, dict) and "ref" in source:
        source["ref"] = "v" + version


def _agents_problems(root):
    doc, _, _ = _load(os.path.join(root, AGENTS))
    _kept(doc, AGENTS)
    return [] if _names(doc) == [KEEP] else ["%s lists %s" % (AGENTS, ", ".join(_names(doc)))]


def apply(root=ROOT, version=None):
    """Perform the edit. Returns the list of paths changed. With `version` the
    kept entry's version and source.ref are written from it. Every catalog is
    read and validated and every new file staged before any is replaced."""
    if version is not None:
        _version_tuple(version)
        problems = _agents_problems(root)
        if problems:
            raise ValueError("; ".join(problems))
    staged = []
    for rel in CATALOGS:
        path = os.path.join(root, rel)
        doc, indent, had_nl = _load(path)
        before = json.dumps(doc, sort_keys=False)
        doc["plugins"] = _kept(doc, rel)
        if version is not None:
            _stamp(doc["plugins"][0], version)
        if json.dumps(doc, sort_keys=False) != before:
            staged.append((rel, path, doc, indent, had_nl))
    tmps = []
    try:
        for rel, path, doc, indent, had_nl in staged:
            tmp = path + ".retire-tmp"
            VS.dump_json_preserving(pathlib.Path(tmp), doc, indent, had_nl)
            tmps.append((tmp, path))
        for tmp, path in tmps:
            os.replace(tmp, path)
    finally:
        for tmp, _path in tmps:
            if os.path.exists(tmp):
                os.remove(tmp)
    changed = [rel for rel, _p, _d, _i, _n in staged]
    for rel in DELETE:
        path = os.path.join(root, rel)
        if os.path.exists(path):
            os.remove(path)
            _stage_removal(root, rel)
            changed.append(rel)
    if _regenerate_bundle_manifest(root):
        changed.append(BUNDLE_MANIFEST)
    return changed


def _stage_removal(root, rel):
    """In a git work tree, stage the deletion so `git ls-files` (which the
    product checksums.sh lists from) stops naming the removed catalog. Only
    the DELETE paths ever reach here. A failed stage raises; never silent."""
    if not os.path.exists(os.path.join(root, ".git")):
        return
    try:
        proc = subprocess.run(["git", "-C", root, "rm", "-q", "--cached",
                               "--ignore-unmatch", "--", rel],
                              capture_output=True, text=True)
    except OSError as exc:
        raise OSError("%s: deleted but its removal could not be staged (%s)" % (rel, exc))
    if proc.returncode != 0:
        raise OSError("%s: deleted but `git rm --cached` exited %d: %s"
                      % (rel, proc.returncode, proc.stderr.strip()))


def problems(root, version):
    """One line per departure from the end state for `version`. Raises on a
    catalog that cannot be read."""
    _version_tuple(version)
    found = []
    for rel in CATALOGS:
        doc, _, _ = _load(os.path.join(root, rel))
        entry = _kept(doc, rel)[0]
        if _names(doc) != [KEEP]:
            found.append("%s lists %s" % (rel, ", ".join(_names(doc))))
        if "version" in entry and entry["version"] != version:
            found.append("%s: %s version is %r, not %s" % (rel, KEEP, entry["version"], version))
        source = entry.get("source")
        if isinstance(source, dict) and "ref" in source and source["ref"] != "v" + version:
            found.append("%s: %s source.ref is %r, not v%s" % (rel, KEEP, source["ref"], version))
    found.extend(_agents_problems(root))
    for rel in DELETE:
        if os.path.exists(os.path.join(root, rel)):
            found.append("%s still exists" % rel)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--check", action="store_true",
                        help="report whether the edit is applied; write nothing")
    parser.add_argument("--apply", action="store_true",
                        help="with --version: reach the end state for that version")
    parser.add_argument("--version", default=None,
                        help="the cut version the kept entry is written from / checked against")
    args = parser.parse_args(argv)
    if args.apply and args.check:
        parser.error("--apply and --check are exclusive")
    if args.apply and args.version is None:
        parser.error("--apply needs --version")
    if args.version is not None and not (args.apply or args.check):
        parser.error("--version needs --apply or --check")
    try:
        if args.version is not None:
            if not applies(args.version):
                print("retire_catalogs: %s" % NOT_APPLICABLE)
                return 0
            if args.check:
                found = problems(args.root, args.version)
                # The chain's check reads the WHOLE end state through the one
                # reader, not only what this script edits: the bundle manifest,
                # each entry's source, the catalog's own metadata version, and
                # a tree with no bundle/ at all. Before this the reader existed
                # and nothing in the chain called it.
                found.extend(line for line in end_state_problems(args.root)
                             if line not in found)
                for line in found:
                    print("retire_catalogs: NOT DONE: %s" % line)
                if found:
                    return 1
                print("retire_catalogs: DONE: end state holds for %s" % args.version)
                return 0
            changed = apply(args.root, args.version)
            for rel in changed:
                print("retire_catalogs: %s %s" % (
                    "deleted" if rel in DELETE else "edited", rel))
            if not changed:
                print("retire_catalogs: already in end state")
            return 0
        if args.check:
            todo = pending(args.root)
            if todo:
                print("retire_catalogs: NOT DONE: %s" % ", ".join(todo))
                return 1
            print("retire_catalogs: DONE: every catalog lists only %s" % KEEP)
            return 0
        changed = apply(args.root)
    except (OSError, ValueError) as exc:
        print("retire_catalogs: NO-DATA: %s" % exc, file=sys.stderr)
        return 2
    print("retire_catalogs: %s"
          % (("changed " + ", ".join(changed)) if changed else "no changes"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
