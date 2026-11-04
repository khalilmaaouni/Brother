#!/usr/bin/env python3
"""U8's done check: this repository publishes exactly ONE plugin, named brother, from five catalogs that have been
rewritten down to the three that survive the 1.1.0 cut.

usage (repo root): python3 -B scripts/donecheck_u8.py [CATALOG_FILE | TREE_DIR]
Exit 0 when every catalog is in the end state, 1 when one is not, 2 when one cannot be read (never a pass).
Handed a file, that one catalog is judged by its plugin names. Handed a directory (the cut chain hands it the
rehearsed tree), every marketplace.json under it is judged by its plugin names, and when the directory carries a
bundle/MANIFEST.json the whole end state of section 5.4 is read too by catalog_problems. Bare, with no argument,
the tree of this checkout is the directory, so the bare command reads every catalog, never only Claude.

WHY IT EXISTS. U8 ("remove old plugin dependencies and marketplace entries") belongs to the earlier unification
plan, docs/plan/UNIFY-1.1.0-WBS.json, where EVERY ONE of its 32 units carries state None: that plan was never
state tracked. Six units of the launch plan (L5, L5a, L5b, L5c, L5e, L5f) name U8 as a dependency, so from
inside the launch board they read as blocked by something the board cannot see and no one can close. Measured
2026-09-21: that is most of wave 4.

The dependency is REAL and was not removed to make a board look green. All six are AUDITS of the shipped
surface (security, reliability, test integrity, documentation accuracy, supply chain), and auditing four
plugins when one is meant to ship audits the wrong thing. What was wrong was not the dependency but its
INVISIBILITY, so U8 becomes a unit of this plan with a check anyone can run.

THE RETIREMENT ITSELF IS THE OWNER'S. Removing a published marketplace entry changes what people can install,
which is not a call this loop makes. This check only says, mechanically, whether it has happened.

catalog_problems is the ONE reader of the end state of section 5.4 (OP1.e). retire_catalogs.end_state_problems
calls it, so the cut chain's check and this unit's reader cannot disagree."""
import json
import os
import sys
import typing

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MARKET = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
KEEP = "brother"


def _no_duplicate_keys(pairs):
    """JSON object hook: a document that names one key twice is corrupt input, never the last one."""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError("the marketplace names the key %r twice" % (key,))
        seen.add(key)
    return dict(pairs)


def plugins(path: str = MARKET) -> typing.List[str]:
    """Every plugin name the marketplace publishes. Raises on anything unreadable, because a marketplace that
    cannot be parsed is NO-DATA and must never read as an empty list, which would pass this check."""
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("the marketplace path must be a string or path-like object, not %s" % type(path).__name__)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("the marketplace could not be read (%s)" % exc) from exc
    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except ValueError as exc:
        raise ValueError("the marketplace is not valid JSON (%s)" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("the marketplace is not a JSON object")
    rows = data.get("plugins")
    if not isinstance(rows, list):
        raise ValueError("the marketplace has no plugins list")
    names = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("a plugin entry is not an object")
        name = row.get("name")
        if not isinstance(name, str):
            raise ValueError("a plugin entry has no string name")
        if name in names:
            raise ValueError("the marketplace publishes the plugin %r more than once" % (name,))
        names.append(name)
    return names


CATALOG_NAME = "marketplace.json"
SKIP_DIRS = frozenset((".git", "node_modules", "__pycache__"))


def catalogs(root: str) -> typing.List[str]:
    """Every marketplace.json under root, sorted, git internals skipped: ENUMERATED from the tree, never a list
    that can miss one. H1 (attack 2026-09-30 on F10): the cut step read one catalog while the Cursor catalog
    still published three plugins; the architecture review counted five catalogs in this tree."""
    if not isinstance(root, (str, os.PathLike)):
        raise ValueError("the root must be a string or path-like object, not %s" % type(root).__name__)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        # H3 (second attack 2026-09-30): a subdirectory carrying its own .git (a nested worktree or clone, file or
        # folder) is ANOTHER checkout, never part of this tree; the owner's checkout holds 41 of them, and reading
        # their catalogs made the real cut refuse ("NOT DONE 144 of 180") while the clean rehearsal read READY.
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not os.path.lexists(os.path.join(dirpath, d, ".git")))
        if CATALOG_NAME in filenames:
            found.append(os.path.join(dirpath, CATALOG_NAME))
    return sorted(found)


CLAUDE_CATALOG = ".claude-plugin/marketplace.json"
CURSOR_CATALOG = ".cursor-plugin/marketplace.json"
AGENTS_CATALOG = ".agents/plugins/marketplace.json"
REQUIRED_CATALOGS = (CLAUDE_CATALOG, CURSOR_CATALOG, AGENTS_CATALOG)
MANIFEST_REL = "bundle/MANIFEST.json"
PLUGIN_MANIFEST_REL = "bundle/.claude-plugin/plugin.json"


def _load_document(path):
    """The full JSON object of one file. Raises ValueError on anything unreadable, duplicate-keyed or not an
    object, because a document that cannot be read is NO-DATA and must never read as clean."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("could not be read (%s)" % exc) from exc
    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except ValueError as exc:
        raise ValueError("is not valid JSON (%s)" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("is not a JSON object")
    return data


def _umbrella_version(root):
    """The version the end state carries, from ONE source: the bundle's own host manifest. Raises ValueError
    when it cannot be read or names no version string. The catalogs are judged AGAINST this value, so a catalog
    can never vouch for its own version, and a version nobody could read is a problem, never a skipped check."""
    doc = _load_document(os.path.join(root, PLUGIN_MANIFEST_REL))
    version = doc.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("names no version string")
    return version


def _only_entry(doc):
    """The single plugin entry of a catalog whose names have already been read, or None when there is not one."""
    rows = doc.get("plugins")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    return rows[0]


def _source_problems(rel, doc, wanted_path, string_source):
    """The surviving entry must name the bundle. Claude and the agents catalog carry the path in a source object;
    Cursor carries the path as a bare string. An entry with no source is a problem, never skipped."""
    entry = _only_entry(doc)
    if entry is None:
        return []
    source = entry.get("source")
    if string_source:
        if source != wanted_path:
            return ["%s: %s source is %r, not %r" % (rel, KEEP, source, wanted_path)]
        return []
    if not isinstance(source, dict):
        return ["%s: %s source is %r, not an object carrying a path" % (rel, KEEP, source)]
    if source.get("path") != wanted_path:
        return ["%s: %s source path is %r, not %r" % (rel, KEEP, source.get("path"), wanted_path)]
    return []


def _version_problems(rel, doc, version):
    """The version and the v<version> ref, checked where the format carries one (Claude and Cursor). The agents
    catalog is checked for its plugin list and source only (CV1 owner decision 1: the Codex schema is not proven
    to take a version field, so the reference draft's Codex-no-version case is dropped)."""
    entry = _only_entry(doc)
    if entry is None:
        return []
    problems = []
    if entry.get("version") != version:
        problems.append("%s: %s version is %r, not %r" % (rel, KEEP, entry.get("version"), version))
    source = entry.get("source")
    if isinstance(source, dict) and source.get("ref") != "v" + version:
        problems.append("%s: %s source.ref is %r, not %r" % (rel, KEEP, source.get("ref"), "v" + version))
    if rel == CLAUDE_CATALOG:
        metadata = doc.get("metadata")
        stated = metadata.get("version") if isinstance(metadata, dict) else None
        if stated != version:
            problems.append("%s: metadata.version is %r, not %r" % (rel, stated, version))
    return problems


def _manifest_problems(root):
    """bundle/MANIFEST.json shipped_plugins must read ["brother"]. Anything unreadable is a problem, never clean."""
    path = os.path.join(root, MANIFEST_REL)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return ["%s could not be read (%s)" % (MANIFEST_REL, exc)]
    try:
        doc = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except ValueError as exc:
        return ["%s is not valid JSON (%s)" % (MANIFEST_REL, exc)]
    if not isinstance(doc, dict):
        return ["%s is not a JSON object" % MANIFEST_REL]
    shipped = doc.get("shipped_plugins")
    if shipped != [KEEP]:
        return ["%s shipped_plugins is %r, not [%r]" % (MANIFEST_REL, shipped, KEEP)]
    return []


def catalog_problems(root: str) -> typing.List[str]:
    """Every way the five catalogs of section 5.4 can be wrong, in the end state. ONE reader for the cut chain's
    retire_catalogs.end_state_problems and for this module's own main, so the chain's check and OP1.e cannot
    disagree. Returns one line per problem; [] is the end state. A catalog that cannot be read or parsed, a
    missing required catalog, an extra catalog, a name other than brother, a source that is not the bundle, a
    missing or wrong version or v<version> ref on Claude and Cursor, and a shipped_plugins that is not
    ["brother"] are each a problem, never skipped. An unknown, unreadable or corrupt input is a problem, never
    NO-DATA that reads clean. `root` is a repository root or a rehearsed tree."""
    if not isinstance(root, (str, os.PathLike)):
        raise ValueError("the root must be a string or path-like object, not %s" % type(root).__name__)
    root = os.fspath(root)
    problems = []
    found = {}
    for path in catalogs(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        found[rel] = path
    for rel in sorted(found):
        if rel not in REQUIRED_CATALOGS:
            problems.append("extra catalog %s: the end state carries exactly the three known catalogs" % rel)
    try:
        version = _umbrella_version(root)
    except ValueError as exc:
        version = None
        problems.append("%s %s, so no catalog version or ref could be judged" % (PLUGIN_MANIFEST_REL, exc))
    for rel in REQUIRED_CATALOGS:
        path = found.get(rel)
        if path is None:
            problems.append("required catalog %s is missing" % rel)
            continue
        try:
            names = plugins(path)
        except (OSError, ValueError, TypeError) as exc:
            problems.append("%s: %s" % (rel, exc))
            continue
        if names != [KEEP]:
            problems.append("%s publishes %s, not exactly %r" % (rel, ", ".join(names) or "no plugin", KEEP))
            continue
        try:
            doc = _load_document(path)
        except ValueError as exc:
            problems.append("%s: %s" % (rel, exc))
            continue
        if rel == AGENTS_CATALOG:
            problems.extend(_source_problems(rel, doc, "./bundle", string_source=False))
            continue
        problems.extend(_source_problems(rel, doc, "bundle", string_source=(rel == CURSOR_CATALOG)))
        if version is not None:
            problems.extend(_version_problems(rel, doc, version))
    problems.extend(_manifest_problems(root))
    return problems


def judge(path) -> int:
    """One catalog: 0 done, 1 not done, 2 unreadable (NO-DATA, never a pass). Prints its lines."""
    try:
        names = plugins(path)
    except (OSError, ValueError, TypeError) as exc:
        print("NO-DATA: the marketplace could not be read (%s); this is not a pass" % exc)
        return 2
    extra = [n for n in names if n != KEEP]
    print("MARKETPLACE %s" % path)
    print("PLUGINS     %d: %s" % (len(names), ", ".join(str(n) for n in names)))
    if KEEP not in names:
        print("NOT DONE    the surviving plugin must be named %r, and it is not listed at all" % KEEP)
        return 1
    if extra:
        print("NOT DONE    %d entr(y/ies) still to retire: %s" % (len(extra), ", ".join(str(n) for n in extra)))
        print("            Retiring a published entry changes what people can install, so it is the owner's")
        print("            call, not this loop's. This check only reports whether it has happened.")
        return 1
    print("DONE        exactly one plugin, named %s" % KEEP)
    return 0


def main(argv=None):
    if argv is not None and not isinstance(argv, (list, tuple)):
        raise ValueError("the argument list must be a list or tuple, not %s" % type(argv).__name__)
    path = (argv or sys.argv[1:] or [ROOT])[0]
    if not isinstance(path, (str, os.PathLike)) or not os.path.isdir(path):
        return judge(path)
    # A directory: every catalog in it must be in the end state. One unreadable catalog is NO-DATA for the
    # whole answer, and one catalog left behind is NOT DONE, whatever the others say.
    found = catalogs(path)
    if not found:
        print("NO-DATA: no %s under %s; a tree that publishes nothing is not the end state" % (CATALOG_NAME, path))
        return 2
    codes = [judge(c) for c in found]
    worst = max(codes)
    # The end state reader: it reads the whole tree, not only the catalog the walk happens to look like. A tree
    # that carries a bundle/ directory IS a plugin tree, so the full reader applies and a bundle manifest that
    # was deleted is reported by it, never the switch that turns the check off. A tree with no bundle/ at all
    # (one catalog in a folder) gets the plugin-name walk only; the cut chain does not rest on this branch for
    # such a tree, because retire_catalogs.py --check --version calls the reader unconditionally. lexists, not
    # isdir: a bundle that is a file or a dangling link is something to report, never a reason to skip.
    if os.path.lexists(os.path.join(path, "bundle")):
        try:
            end_problems = catalog_problems(path)
        except (OSError, ValueError, TypeError) as exc:
            print("NO-DATA: the end state could not be read (%s); this is not a pass" % exc)
            return 2
        if end_problems:
            for line in end_problems:
                print("NOT DONE    %s" % line)
            return 1
    if worst == 0:
        print("DONE        all %d catalog(s) publish exactly one plugin, named %s" % (len(found), KEEP))
    else:
        print("%s %d of %d catalog(s) are not in the end state" % ("NO-DATA    " if worst == 2 else "NOT DONE   ",
                                                                   sum(1 for c in codes if c), len(found)))
    return worst


if __name__ == "__main__":
    sys.exit(main())
