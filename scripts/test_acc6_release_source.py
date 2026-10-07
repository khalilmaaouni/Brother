#!/usr/bin/env python3
"""ACC6.b: the read only acceptance check for this repository's umbrella
version identity.

release_source_errors(root, expected_version) returns a list of named
errors. An empty list means the tree at root agrees with expected_version:
the canonical source and every declared version, every carrier that
scripts/version_source.py --write regenerates, the generated release note
at docs/releases/<version>.md, and the shipped runtime against the
scripts/ sources it mirrors.

A missing or unreadable input is an error here, never a pass: this is an
acceptance assertion about a tree that is about to be called ready, so
NO-DATA is reported as a named refusal rather than folded into silence.

The full shipped runtime comparison is scripts/bundle_runtime.py's own
check. That generator is a command runner and is not importable from this
acceptance module, so the runtime link reads RUNTIME-MANIFEST.json and
compares the runtime entry every install runs with its scripts/ source.

No em or en dashes.
"""
import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import version_source  # noqa: E402

MARKETPLACE_REL = ".claude-plugin/marketplace.json"
# ONE SOURCE FOR THE CARRIERS (2026-09-30): this tuple was hand-written, so when the one plugin merge added
# bundle/.antigravity-plugin/plugin.json to version_source's carriers the "consistent" fixture stopped being consistent
# and the clean case went red on the landed tree. It is derived from version_source.CARRIER_FILES_REL now.
BUNDLE_MANIFEST_RELS = tuple(r for r in version_source.CARRIER_FILES_REL
                             if r.startswith("bundle/") and r.endswith("/plugin.json"))
VERSIONING_REL = "docs/VERSIONING.md"
RELEASES_REL = "docs/releases"
SCRIPTS_REL = "scripts"
RUNTIME_REL = "bundle/runtime"
RUNTIME_MANIFEST_REL = "bundle/runtime/RUNTIME-MANIFEST.json"

#: The runtime entry scripts/bundle_runtime.py mirrors byte for byte from
#: scripts/ into bundle/runtime/. It is a floor, not the whole closure: that
#: generator's own check covers every mirrored file, and this acceptance
#: module reads the one file an install actually runs.
MIRRORED_FROM_SCRIPTS = ("brother_run.py",)

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _checked_root(root):
    """The tree root as an absolute path string.

    Hostile input is refused with this module's own ValueError and never
    reaches open(): None, a bool, an int, a float (NaN included), bytes, a
    list, a dict and a set all stop here.
    """
    if isinstance(root, str) or isinstance(root, pathlib.PurePath):
        return os.path.abspath(os.fspath(root))
    raise ValueError(
        "release_source_errors: root must be a str or a pathlib path, not %s"
        % type(root).__name__)


def _checked_version(expected_version):
    """The wanted X.Y.Z version.

    Hostile input is refused with this module's own ValueError: None, a
    bool, an int, a float, bytes, a list, an empty string and any string
    that is not exactly three dotted numbers all stop here.
    """
    if (not isinstance(expected_version, str)
            or not VERSION_RE.match(expected_version)):
        raise ValueError(
            "release_source_errors: expected_version must be X.Y.Z, not %r"
            % (expected_version,))
    return expected_version


def _read_json(path):
    """The JSON object at path, or None.

    None covers every unreadable case (absent, a directory, a permission
    refusal, bytes that are not UTF-8, text that is not JSON, or JSON that
    is not an object) and is an error at every call site below, never a
    pass.
    """
    try:
        with open(path, "rb") as handle:
            doc = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def _read_bytes(path):
    """(bytes, error): the file's bytes, or (None, a named reason)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(), None
    except OSError as exc:
        return None, "%s: unreadable (%s)" % (path, exc)


def _declared_versions(root):
    """(metadata.version, the brother entry's version) from the canonical
    source. Either is None when that file or that field is absent."""
    doc = _read_json(os.path.join(root, MARKETPLACE_REL))
    if doc is None:
        return None, None
    metadata = doc.get("metadata")
    metadata_version = metadata.get("version") if isinstance(metadata, dict) else None
    brother_version = None
    plugins = doc.get("plugins")
    if isinstance(plugins, list):
        for entry in plugins:
            if isinstance(entry, dict) and entry.get("name") == "brother":
                brother_version = entry.get("version")
    return metadata_version, brother_version


def _source_version_errors(root):
    """version_source --check's own verdict, captured rather than printed:
    its nonzero exit and the carrier lines it named become this check's
    errors, so a mixed carrier set is refused by name."""
    stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            code = version_source.run_check(pathlib.Path(root))
    except (OSError, ValueError) as exc:
        return ["scripts/version_source.py --check could not read %s (%s)"
                % (root, exc)]
    if code == 0:
        return []
    detail = [line for line in stream.getvalue().splitlines()
              if line.startswith("DRIFT") or line.startswith("NO-DATA")]
    named = "; ".join(detail[:4]) if detail else "no detail line"
    return ["scripts/version_source.py --check exited %d for %s: %s"
            % (code, root, named)]


def _release_note_errors(root, want):
    """The generated release note must exist and must name the version it
    describes."""
    rel = os.path.join(RELEASES_REL, "%s.md" % want)
    data, error = _read_bytes(os.path.join(root, rel))
    if error is not None:
        return ["no readable release note at %s, so the release has no "
                "generated description to check" % rel]
    if want.encode("utf-8") not in data:
        return ["release note %s never names %s" % (rel, want)]
    return []


def _shipped_runtime_errors(root):
    """The shipped runtime must still equal the scripts/ sources it mirrors.

    scripts/bundle_runtime.py performs the whole closure comparison; it is a
    command runner and cannot be imported here, so this composes the runtime
    manifest instead: the manifest must be readable, it must write down the
    entry named in MIRRORED_FROM_SCRIPTS, and that entry must be present in
    both trees with identical bytes. A manifest that is absent or unreadable
    is an error, never a pass.
    """
    manifest = _read_json(os.path.join(root, RUNTIME_MANIFEST_REL))
    if manifest is None or not isinstance(manifest.get("files"), list):
        return ["%s is missing or unreadable, so the shipped runtime cannot "
                "be compared with the scripts/ sources it mirrors"
                % RUNTIME_MANIFEST_REL]
    listed = []
    for entry in manifest["files"]:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            listed.append(entry["path"])
    errors = []
    for name in MIRRORED_FROM_SCRIPTS:
        if name not in listed:
            errors.append("%s is not written down in %s, so its shipped "
                          "bytes are not attested"
                          % (name, RUNTIME_MANIFEST_REL))
            continue
        source = os.path.join(root, SCRIPTS_REL, name)
        shipped = os.path.join(root, RUNTIME_REL, name)
        source_bytes, source_error = _read_bytes(source)
        shipped_bytes, shipped_error = _read_bytes(shipped)
        if source_error is not None or shipped_error is not None:
            errors.append("%s: not readable in both %s and %s (%s)"
                          % (name, SCRIPTS_REL, RUNTIME_REL,
                             source_error or shipped_error))
            continue
        if source_bytes != shipped_bytes:
            errors.append("%s: bundle/runtime copy does not match its "
                          "scripts/ source" % name)
    return errors


def release_source_errors(root, expected_version):
    """Every named disagreement between the tree at root and
    expected_version. An empty list means the tree is consistent.

    Nothing here writes a carrier or any other file: this is a reader, and
    it never mutates the tree it is handed.
    """
    root = _checked_root(root)
    want = _checked_version(expected_version)
    errors = []

    metadata_version, brother_version = _declared_versions(root)
    if metadata_version is None:
        errors.append("%s names no metadata.version" % MARKETPLACE_REL)
    elif metadata_version != want:
        errors.append("%s metadata.version is %s, expected %s"
                      % (MARKETPLACE_REL, metadata_version, want))
    if brother_version is None:
        errors.append("%s carries no brother plugin entry version"
                      % MARKETPLACE_REL)
    elif brother_version != want:
        errors.append("%s brother entry version is %s, expected %s"
                      % (MARKETPLACE_REL, brother_version, want))

    errors.extend(_source_version_errors(root))
    errors.extend(_release_note_errors(root, want))
    errors.extend(_shipped_runtime_errors(root))
    return errors


def _write_text(path, text):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _marketplace_doc(version):
    return {
        "name": "brother",
        "owner": {"name": "owner"},
        "metadata": {"description": "umbrella", "version": version},
        "plugins": [
            {
                "name": "brothermode",
                "source": {"source": "git-subdir",
                           "url": "https://example.invalid/r",
                           "path": "products/brothermode",
                           "ref": "v" + version},
                "description": "product", "version": "3.4.4",
                "author": {"name": "owner"}, "category": "productivity",
            },
            {
                "name": "brothersbe",
                "source": {"source": "git-subdir",
                           "url": "https://example.invalid/r",
                           "path": "products/brothersbe",
                           "ref": "v" + version},
                "description": "product", "version": "3.7.4",
                "author": {"name": "owner"}, "category": "engineering",
            },
            {
                "name": "brother",
                "source": {"source": "git-subdir",
                           "url": "https://example.invalid/r",
                           "path": "bundle",
                           "ref": "v" + version},
                "description": "umbrella", "version": version,
                "author": {"name": "owner"}, "category": "productivity",
            },
        ],
    }


def build_fixture(root, version="1.1.0"):
    """A whole umbrella tree in a temp folder, so every case below runs on
    its own bytes and never on this repository's working tree."""
    _write_text(os.path.join(root, MARKETPLACE_REL),
                json.dumps(_marketplace_doc(version), indent=2) + "\n")
    # Every product carrier version_source re-verifies (its product_carriers rule): the three plugin.json files of
    # each product the marketplace lists, and that product's cursor marketplace entry, all at the product's own
    # marketplace version, so the fixture stays consistent with the rule rather than with a hand list.
    products = [e for e in _marketplace_doc(version)["plugins"] if e.get("name") in ("brothermode", "brothersbe")]
    for e in products:
        for sub in (".claude-plugin", ".codex-plugin", ".cursor-plugin"):
            _write_text(os.path.join(root, "products", e["name"], sub, "plugin.json"),
                        json.dumps({"name": e["name"], "version": e["version"]}, indent=2) + "\n")
    _write_text(os.path.join(root, version_source.CURSOR_MARKETPLACE_REL),
                json.dumps({"metadata": {"version": version},
                            "plugins": [{"name": "brother", "version": version}]
                                       + [{"name": e["name"], "version": e["version"]} for e in products]},
                           indent=2) + "\n")
    for rel in BUNDLE_MANIFEST_RELS:
        _write_text(os.path.join(root, rel),
                    json.dumps({"name": "brother", "version": version},
                               indent=2) + "\n")
    _write_text(os.path.join(root, VERSIONING_REL),
                "# Versioning\n\nCurrent version: %s.\n\nMore prose.\n" % version)
    _write_text(os.path.join(root, RELEASES_REL, "%s.md" % version),
                "# Brother %s\n\nThe release notes for %s.\n" % (version, version))
    _write_text(os.path.join(root, "products", "brothermode",
                             ".claude-plugin", "plugin.json"),
                json.dumps({"name": "brothermode", "version": "3.4.4"},
                           indent=2) + "\n")
    _write_text(os.path.join(root, "products", "brothermode",
                             ".codex-plugin", "plugin.json"),
                json.dumps({"name": "brothermode", "version": "3.4.4"},
                           indent=2) + "\n")
    _write_text(os.path.join(root, "products", "brothermode", "VERSION"),
                "3.4.4\n")
    _write_text(os.path.join(root, "products", "brothersbe",
                             ".claude-plugin", "plugin.json"),
                json.dumps({"name": "brothersbe", "version": "3.7.4"},
                           indent=2) + "\n")
    _write_text(os.path.join(root, "products", "brothersbe", "VERSION"),
                "3.7.4\n")
    engine = "# fixture engine\n"
    _write_text(os.path.join(root, SCRIPTS_REL, "brother_run.py"), engine)
    _write_text(os.path.join(root, RUNTIME_REL, "brother_run.py"), engine)
    _write_text(os.path.join(root, RUNTIME_MANIFEST_REL),
                json.dumps({"files": [{"path": "brother_run.py",
                                        "sha256": "0" * 64}]},
                           indent=2) + "\n")


class ReleaseSourceErrors(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="acc6b-release-source-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        build_fixture(self.root)

    def test_consistent_fixture_at_1_1_0_is_clean(self):
        self.assertEqual([], release_source_errors(self.root, "1.1.0"))

    def test_mixed_carrier_version_is_named(self):
        _write_text(os.path.join(self.root, "bundle", ".codex-plugin",
                                 "plugin.json"),
                    json.dumps({"name": "brother", "version": "1.0.20"},
                               indent=2) + "\n")
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(
            any("bundle/.codex-plugin/plugin.json" in e for e in errors),
            errors)

    def test_a_consistent_reversion_to_1_0_20_is_refused(self):
        build_fixture(self.root, version="1.0.20")
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(errors)
        self.assertTrue(any("1.0.20" in e for e in errors), errors)

    def test_release_note_missing_is_named(self):
        os.unlink(os.path.join(self.root, RELEASES_REL, "1.1.0.md"))
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(any("release note" in e for e in errors), errors)

    def test_release_note_that_never_names_the_version_is_named(self):
        _write_text(os.path.join(self.root, RELEASES_REL, "1.1.0.md"),
                    "# Brother\n\nno figure here\n")
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(any("never names" in e for e in errors), errors)

    def test_missing_runtime_manifest_is_an_error_not_a_pass(self):
        os.unlink(os.path.join(self.root, RUNTIME_MANIFEST_REL))
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(
            any("RUNTIME-MANIFEST.json" in e for e in errors), errors)

    def test_drifted_runtime_copy_is_named(self):
        _write_text(os.path.join(self.root, RUNTIME_REL, "brother_run.py"),
                    "# moved after the manifest was written\n")
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(any("brother_run.py" in e for e in errors), errors)

    def test_unreadable_marketplace_is_an_error_not_a_pass(self):
        _write_text(os.path.join(self.root, MARKETPLACE_REL),
                    "not json at all\n")
        errors = release_source_errors(self.root, "1.1.0")
        self.assertTrue(errors)

    def test_hostile_roots_are_refused(self):
        for bad in (None, True, False, 7, 1.5, float("nan"), b"tree", [], {},
                    set()):
            with self.assertRaises(ValueError):
                release_source_errors(bad, "1.1.0")

    def test_hostile_versions_are_refused(self):
        for bad in (None, True, 110, 1.1, float("nan"), b"1.1.0", "", "1.1",
                    "1.1.0.0", "v1.1.0", []):
            with self.assertRaises(ValueError):
                release_source_errors(self.root, bad)


class ActualCheckout(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(os.path.join(ROOT, MARKETPLACE_REL)),
                         "this tree carries no umbrella marketplace manifest")
    def test_actual_checkout_carriers_agree(self):
        self.assertEqual([], release_source_errors(ROOT, "1.1.0"))


if __name__ == "__main__":
    unittest.main()
