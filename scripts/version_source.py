#!/usr/bin/env python3
"""One version and manifest source for this repository's umbrella package.

.claude-plugin/marketplace.json is the declared single source of version
truth for the umbrella (see scripts/test_version_truth.py's docstring). The
"brother" entry's own version field, and the metadata.version field of that
same file, ARE the umbrella version. Every other file that repeats that
number is a carrier: it must say exactly what the source says, and it is
never edited by hand, only regenerated from the source.

--check reads the source and prints one PASS or DRIFT line per carrier,
naming both values on a DRIFT, then exits 1 if any carrier drifted.

--write --version X.Y.Z first sets the source itself (marketplace.json:
metadata.version, the "brother" plugin entry's version, and every plugin
entry's source.ref, since all three entries in this repository are cut from
one tag), then regenerates every other carrier from that updated source.
Each JSON carrier keeps its own indentation and key order: the file is
loaded, only the version-shaped fields are mutated, and it is dumped back
with the same indent width and the same trailing newline it already had.

Product versions (products/brothermode, products/brothersbe: their own
.claude-plugin/plugin.json, .codex-plugin/plugin.json, VERSION files, and
their entries in .cursor-plugin/marketplace.json) are NOT part of the
umbrella version and are never bumped by --write. They are only re-verified
here: each is compared against ITS OWN marketplace entry version (not the
umbrella figure), the same population scripts/test_version_truth.py's
subtree_mismatches already covers, printed as its own carrier lines so a
drift in either place is visible in one run. A product carrier file that
does not exist (this repository ships no
products/brothersbe/.codex-plugin/plugin.json today) is reported NO-DATA,
never PASS or DRIFT: an absent file makes no promise to check. That
allowance is for OPTIONAL product carriers only. Every umbrella carrier
(the three bundle manifests, docs/VERSIONING.md's "Current version" line,
every marketplace source.ref, metadata.version and the brother entry's
version, plus the Cursor marketplace's metadata.version and brother entry
version, the latter two reported NO-DATA rather than DRIFT if that file is
absent) is REQUIRED: one that is missing, or whose field is missing, is
reported DRIFT with "missing" in place of a value, and exits 1, because a
required carrier that vanished is exactly the drift this check exists to
catch, not an absence of evidence.

--write is transactional across every file it touches: the bytes of each
carrier file are snapshotted before the first write, and any failure part
way (a carrier that refuses, an unreadable or unwritable file) restores
every snapshot and exits 1, so the tree is never left with some carriers
bumped and others not.

Exit 0: --check found no drift, or --write completed.
Exit 1: --check found at least one DRIFT, or --write failed and rolled back.
Exit 2: the source file itself is missing or unreadable (NO-DATA).
No em or en dashes.
"""
import argparse
import json
import re
import sys
from pathlib import Path


def repo_root():
    return Path(__file__).resolve().parent.parent


MARKETPLACE_REL = ".claude-plugin/marketplace.json"
CURSOR_MARKETPLACE_REL = ".cursor-plugin/marketplace.json"

#: Every file --write may touch apart from the source itself, named once so
#: the transactional snapshot in run_write and umbrella_carriers cannot
#: disagree about what a bump covers.
CARRIER_FILES_REL = (
    "bundle/.claude-plugin/plugin.json",
    "bundle/.codex-plugin/plugin.json",
    "bundle/.cursor-plugin/plugin.json",
    "docs/VERSIONING.md",
    CURSOR_MARKETPLACE_REL,
)


def detect_indent(text):
    """The width of the indentation already used in a JSON file's body."""
    m = re.search(r"\n( +)\"", text)
    if m:
        return len(m.group(1))
    return 2


def load_json_preserving(path):
    """Return (data, indent, had_trailing_newline) for a JSON file, order
    preserved (dict insertion order, which is what json.load already gives
    in Python 3.7+)."""
    text = path.read_text()
    data = json.loads(text)
    return data, detect_indent(text), text.endswith("\n")


def dump_json_preserving(path, data, indent, had_newline):
    text = json.dumps(data, indent=indent)
    if had_newline:
        text += "\n"
    path.write_text(text)


def read_source(root):
    """The umbrella version and the marketplace document, or None, None on
    a missing or unreadable source file."""
    path = root / MARKETPLACE_REL
    if not path.is_file():
        return None, None
    try:
        doc, indent, had_nl = load_json_preserving(path)
    except (OSError, ValueError):
        return None, None
    plugins = doc.get("plugins", [])
    brother = next((p for p in plugins if p.get("name") == "brother"), None)
    if brother is None or "version" not in brother:
        return None, None
    return brother["version"], (doc, indent, had_nl)


class Carrier(object):
    """One file (or one field within it) that repeats the umbrella version."""

    def __init__(self, label, get, set_=None):
        self.label = label
        self.get = get  # () -> (value_or_None, exists)
        self.set_ = set_  # (version) -> None, or None if not writable here


def umbrella_carriers(root):
    """Carriers whose value must equal the umbrella version. Excludes the
    source file itself (marketplace.json), which IS the version, not a
    repetition of it."""
    carriers = []

    def bundle_json_carrier(rel):
        path = root / rel

        def get():
            if not path.is_file():
                return None, False
            try:
                doc, _, _ = load_json_preserving(path)
            except (OSError, ValueError):
                return None, False
            return doc.get("version"), True

        def set_(version):
            doc, indent, had_nl = load_json_preserving(path)
            doc["version"] = version
            dump_json_preserving(path, doc, indent, had_nl)

        carriers.append(Carrier(rel + ":version", get, set_))

    bundle_json_carrier("bundle/.claude-plugin/plugin.json")
    bundle_json_carrier("bundle/.codex-plugin/plugin.json")
    bundle_json_carrier("bundle/.cursor-plugin/plugin.json")

    versioning_path = root / "docs" / "VERSIONING.md"
    pattern = re.compile(r"Current version: ([0-9]+\.[0-9]+\.[0-9]+)\.")

    def get_versioning():
        if not versioning_path.is_file():
            return None, False
        text = versioning_path.read_text()
        m = pattern.search(text)
        if not m:
            return None, False
        return m.group(1), True

    def set_versioning(version):
        text = versioning_path.read_text()
        new_text, count = pattern.subn("Current version: %s." % version, text)
        if count == 0:
            raise SystemExit(
                'docs/VERSIONING.md: "Current version: X." line not found, '
                "refusing to bump silently"
            )
        versioning_path.write_text(new_text)

    carriers.append(Carrier("docs/VERSIONING.md:Current version", get_versioning, set_versioning))

    return carriers


def marketplace_ref_carriers(root):
    """Every plugin entry's source.ref, which is the tag every plugin in
    this repository is cut from, so it must read v<umbrella version>
    regardless of that plugin's own version field."""
    path = root / MARKETPLACE_REL
    carriers = []

    def make(plugin_name):
        def get():
            if not path.is_file():
                return None, False
            try:
                doc, _, _ = load_json_preserving(path)
            except (OSError, ValueError):
                return None, False
            plugin = next((p for p in doc.get("plugins", []) if p.get("name") == plugin_name), None)
            if plugin is None:
                return None, False
            src = plugin.get("source")
            if not isinstance(src, dict) or "ref" not in src:
                return None, False
            return src["ref"], True

        def set_(version):
            doc, indent, had_nl = load_json_preserving(path)
            plugin = next((p for p in doc.get("plugins", []) if p.get("name") == plugin_name), None)
            if plugin is None:
                raise SystemExit(
                    "%s: plugin entry %r not found, refusing to bump silently" % (MARKETPLACE_REL, plugin_name)
                )
            src = plugin.get("source")
            if isinstance(src, dict) and "ref" in src:
                src["ref"] = "v" + version
            dump_json_preserving(path, doc, indent, had_nl)

        return Carrier("marketplace:%s.source.ref" % plugin_name, get, set_)

    for name in ("brother", "brothermode", "brothersbe"):
        carriers.append(make(name))
    return carriers


def marketplace_metadata_carrier(root):
    path = root / MARKETPLACE_REL

    def get():
        if not path.is_file():
            return None, False
        try:
            doc, _, _ = load_json_preserving(path)
        except (OSError, ValueError):
            return None, False
        meta = doc.get("metadata") or {}
        if "version" not in meta:
            return None, False
        return meta["version"], True

    def set_(version):
        doc, indent, had_nl = load_json_preserving(path)
        doc.setdefault("metadata", {})["version"] = version
        dump_json_preserving(path, doc, indent, had_nl)

    return Carrier("marketplace:metadata.version", get, set_)


def marketplace_brother_version_carrier(root):
    """The brother plugin entry's own version field: this IS the umbrella
    version's home, but it is still written explicitly by --write so a
    caller can move the source forward, and re-checked like any carrier."""
    path = root / MARKETPLACE_REL

    def get():
        if not path.is_file():
            return None, False
        try:
            doc, _, _ = load_json_preserving(path)
        except (OSError, ValueError):
            return None, False
        plugin = next((p for p in doc.get("plugins", []) if p.get("name") == "brother"), None)
        if plugin is None or "version" not in plugin:
            return None, False
        return plugin["version"], True

    def set_(version):
        doc, indent, had_nl = load_json_preserving(path)
        plugin = next((p for p in doc.get("plugins", []) if p.get("name") == "brother"), None)
        if plugin is None:
            raise SystemExit("%s: brother plugin entry not found, refusing to bump silently" % MARKETPLACE_REL)
        plugin["version"] = version
        dump_json_preserving(path, doc, indent, had_nl)

    return Carrier("marketplace:brother.version", get, set_)


def cursor_marketplace_umbrella_carriers(root):
    """Umbrella carriers in .cursor-plugin/marketplace.json: metadata.version
    and the brother entry's version. If the file is absent, get() returns
    (None, False) and run_check reports NO-DATA for these labels instead of
    calling them. If the file exists but the brother entry is missing,
    set_() refuses to bump silently."""
    path = root / CURSOR_MARKETPLACE_REL
    carriers = []

    def metadata_get():
        if not path.is_file():
            return None, False
        try:
            doc, _, _ = load_json_preserving(path)
        except (OSError, ValueError):
            return None, False
        meta = doc.get("metadata") or {}
        if "version" not in meta:
            return None, False
        return meta["version"], True

    def metadata_set(version):
        if not path.is_file():
            return
        doc, indent, had_nl = load_json_preserving(path)
        doc.setdefault("metadata", {})["version"] = version
        dump_json_preserving(path, doc, indent, had_nl)

    carriers.append(Carrier("cursor-marketplace:metadata.version", metadata_get, metadata_set))

    def brother_get():
        if not path.is_file():
            return None, False
        try:
            doc, _, _ = load_json_preserving(path)
        except (OSError, ValueError):
            return None, False
        plugin = next((p for p in doc.get("plugins", []) if p.get("name") == "brother"), None)
        if plugin is None or "version" not in plugin:
            return None, False
        return plugin["version"], True

    def brother_set(version):
        if not path.is_file():
            return
        doc, indent, had_nl = load_json_preserving(path)
        plugin = next((p for p in doc.get("plugins", []) if p.get("name") == "brother"), None)
        if plugin is None:
            raise SystemExit(
                "%s: brother plugin entry not found, refusing to bump silently" % CURSOR_MARKETPLACE_REL
            )
        plugin["version"] = version
        dump_json_preserving(path, doc, indent, had_nl)

    carriers.append(Carrier("cursor-marketplace:brother.version", brother_get, brother_set))

    return carriers


def product_carriers(root):
    """Re-verify-only carriers: each product file's own version, compared
    against ITS OWN marketplace entry (never the umbrella figure). Never
    written by this script's --write."""
    src_path = root / MARKETPLACE_REL
    if not src_path.is_file():
        return []
    try:
        doc, _, _ = load_json_preserving(src_path)
    except (OSError, ValueError):
        return []
    entries = {p.get("name"): p for p in doc.get("plugins", [])}

    carriers = []
    for product in ("brothermode", "brothersbe"):
        want = entries.get(product, {}).get("version")
        if want is None:
            continue
        for rel in (
            "products/%s/.claude-plugin/plugin.json" % product,
            "products/%s/.codex-plugin/plugin.json" % product,
            "products/%s/.cursor-plugin/plugin.json" % product,
        ):
            path = root / rel

            def get(path=path):
                if not path.is_file():
                    return None, False
                try:
                    doc2, _, _ = load_json_preserving(path)
                except (OSError, ValueError):
                    return None, False
                return doc2.get("version"), True

            carriers.append((Carrier("%s:version (vs %s entry)" % (rel, product), get), want))

        version_path = root / "products" / product / "VERSION"

        def get_version_file(path=version_path):
            if not path.is_file():
                return None, False
            return path.read_text().strip(), True

        carriers.append((Carrier("products/%s/VERSION (vs %s entry)" % (product, product), get_version_file), want))

        cursor_path = root / CURSOR_MARKETPLACE_REL

        def get_cursor(path=cursor_path, product=product):
            if not path.is_file():
                return None, False
            try:
                doc2, _, _ = load_json_preserving(path)
            except (OSError, ValueError):
                return None, False
            plugin = next((p for p in doc2.get("plugins", []) if p.get("name") == product), None)
            if plugin is None or "version" not in plugin:
                return None, True
            return plugin["version"], True

        carriers.append((Carrier("cursor-marketplace:%s.version" % product, get_cursor), want))

    return carriers


def run_check(root):
    version, _ = read_source(root)
    if version is None:
        print("NO-DATA: %s is missing or unreadable, cannot check drift" % MARKETPLACE_REL)
        return 2

    umbrella = umbrella_carriers(root) + marketplace_ref_carriers(root) + [
        marketplace_metadata_carrier(root),
        marketplace_brother_version_carrier(root),
    ]
    cursor_path = root / CURSOR_MARKETPLACE_REL
    if cursor_path.is_file():
        umbrella += cursor_marketplace_umbrella_carriers(root)
    else:
        for label in ("cursor-marketplace:metadata.version", "cursor-marketplace:brother.version"):
            print("NO-DATA: %s" % label)

    failed = False
    for carrier in umbrella:
        want = ("v" + version) if carrier.label.endswith(".source.ref") else version
        got, exists = carrier.get()
        if not exists:
            print("DRIFT: %s (source=%s, carrier=missing: a required umbrella "
                  "carrier file or field is absent)" % (carrier.label, want))
            failed = True
            continue
        if got == want:
            print("PASS: %s (%s == %s)" % (carrier.label, got, want))
        else:
            print("DRIFT: %s (source=%s, carrier=%s)" % (carrier.label, want, got))
            failed = True

    for carrier, want in product_carriers(root):
        got, exists = carrier.get()
        if not exists:
            print("NO-DATA: %s" % carrier.label)
            continue
        if got == want:
            print("PASS: %s (%s == %s)" % (carrier.label, got, want))
        else:
            print("DRIFT: %s (source=%s, carrier=%s)" % (carrier.label, want, got))
            failed = True

    return 1 if failed else 0


def run_write(root, version):
    src_path = root / MARKETPLACE_REL
    if not src_path.is_file():
        print("NO-DATA: %s is missing or unreadable, cannot write" % MARKETPLACE_REL)
        return 2

    # Every file the carriers below may touch, snapshotted byte for byte
    # BEFORE the first write. A failure anywhere below (a carrier whose
    # field is missing raises SystemExit, an unreadable or unwritable file
    # raises OSError or ValueError) restores all of them, so the tree never
    # holds a half bump: some carriers at the new version, others at the
    # old one, which only a later --check would notice.
    touched = [src_path] + [root / rel for rel in CARRIER_FILES_REL]
    snapshot = {}
    for path in touched:
        try:
            snapshot[path] = path.read_bytes() if path.is_file() else None
        except OSError as exc:
            print("NO-DATA: cannot read %s before writing (%s), nothing written"
                  % (path.relative_to(root), exc))
            return 2

    def restore():
        for path, data in snapshot.items():
            try:
                if data is None:
                    if path.is_file():
                        path.unlink()
                else:
                    path.write_bytes(data)
            except OSError as exc:  # sbe: allow-silent reader-only: a restore that cannot write is reported on the line below, and the caller already exits 1
                print("ROLLBACK FAILED for %s: %s" % (path.relative_to(root), exc))

    try:
        # 1. the source itself: metadata.version, brother.version, every ref.
        marketplace_metadata_carrier(root).set_(version)
        marketplace_brother_version_carrier(root).set_(version)
        for carrier in marketplace_ref_carriers(root):
            carrier.set_(version)

        # 2. the Cursor marketplace's umbrella fields, if that file exists.
        for carrier in cursor_marketplace_umbrella_carriers(root):
            carrier.set_(version)

        # 3. every other umbrella carrier, regenerated from the now-updated source.
        for carrier in umbrella_carriers(root):
            carrier.set_(version)
    except (SystemExit, OSError, ValueError) as exc:
        restore()
        reason = exc.code if isinstance(exc, SystemExit) else exc
        print("FAIL: --write stopped part way (%s); every carrier file was "
              "restored to its bytes before this run, nothing is bumped" % reason)
        return 1

    print("wrote version %s to %s and every umbrella carrier" % (version, MARKETPLACE_REL))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--version", help="X.Y.Z, required with --write")
    parser.add_argument("--root", default=None, help="repository root (default: this script's own repo)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else repo_root()

    if args.write:
        if not args.version:
            parser.error("--write requires --version X.Y.Z")
        if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", args.version):
            parser.error("--version must be X.Y.Z")
        return run_write(root, args.version)

    return run_check(root)


if __name__ == "__main__":
    sys.exit(main())
