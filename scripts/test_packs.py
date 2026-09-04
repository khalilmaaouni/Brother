"""Every persona pack in scripts/packs, checked generically.

One test file for thirteen packs and for the fourteenth nobody has written
yet: it enumerates scripts/packs/*.json rather than naming packs, so a pack
added by a later lane joins this gate by existing rather than by somebody
remembering to register it (the estate lesson that a tool invisible to every
registry is a tool nobody checks).

What it holds each pack to:

  * door.load_pack accepts it (so every key in PACK_REQUIRED_KEYS is there);
  * the fuller manifest of persona doc 5.1 is present (work_classes,
    risk_classes, dangerous_actions, know_how, tool_adapters, provenance,
    fixture), with a NAMED, TEMPORARY allowlist for the two packs that
    landed before that section was folded in;
  * required_evidence_families and receipt_fields are FLAT lists of names,
    the shape data-science.json carries and door.pack_union deduplicates,
    with any inline why, meaning or fill route in the parallel key beside
    them (required_evidence_families_why, receipt_fields_details) and
    describing only names the list holds;
  * the pack's own fixture actually infers it: the paths and manifest
    string it declares, under the ONE spelling below, must put its lens in
    door.infer_lenses' composed list, and the paths it declares as foreign
    must not;
  * every forcing class carries id, label and why, because receipt_door.py
    builds a parking trigger from the id and a person reads the why;
  * no dash characters (U+2014, U+2013) anywhere in the file, the estate's
    standing prose law.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import door as door_mod  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

PACKS_DIR = door_mod.PACKS_DIR

#: Persona doc 5.1's own list, beyond door.PACK_REQUIRED_KEYS: what a pack
#: declares so a reader can see what it assists with, what it refuses to
#: authorize alone, the know-how it carries, the tools it can use, where its
#: content came from, and how it proves its own detection.
FULL_MANIFEST_KEYS = (
    "work_classes",
    "risk_classes",
    "dangerous_actions",
    "know_how",
    "tool_adapters",
    "provenance",
    "fixture",
)

#: TEMPORARY, and named here so removing it is one edit and one grep: the
#: two packs that landed before persona doc 5.1's fuller manifest was folded
#: in. Every pack written after it carries all of FULL_MANIFEST_KEYS, and
#: this allowlist comes out in the follow-up row that backfills these two.
#: A pack NOT on this list is held to the full manifest with no exception.
FULL_MANIFEST_EXEMPT = ("core", "data-science")

#: The two dash characters the estate's prose law forbids everywhere, built
#: from their code points rather than typed: a file that types the character
#: it forbids is itself a hit for every other dash scan in the estate, and
#: exempting it by file name is the shape that has already let a leak
#: through here once.
FORBIDDEN_DASHES = (chr(0x2014), chr(0x2013))

#: A fixture's positive path list, its manifest string and its negative path
#: list. ONE spelling and one shape, not a family of them: the eleven pack
#: lanes each named these keys their own way (must_infer as an object,
#: must_infer_paths as a list, infers, does_not_infer), which read as
#: eleven contracts and left every fixture untested because none of them
#: matched what this file reads. The packs are normalized to the shape
#: below and this reader is deliberately strict, so the next pack cannot
#: drift back into a private spelling that silently tests nothing.
POSITIVE_KEYS = ("must_infer",)
NEGATIVE_KEYS = ("must_not_infer",)
MANIFEST_KEYS = ("manifest_string",)

#: The flat-list keys of persona doc 5.1 as data-science.json carries them
#: (the shape door.pack_union deduplicates with _field_key), each with the
#: optional parallel key holding the prose a pack wrote inline: why an
#: evidence family is required, and what a receipt field means and how it
#: is filled. Flat lists keep the union homogeneous; the parallel maps keep
#: the reasoning a reader needs.
LIST_AND_PARALLEL = (
    ("required_evidence_families", "required_evidence_families_why"),
    ("receipt_fields", "receipt_fields_details"),
)

#: The one entry a parallel map may carry that is not a name on its list: a
#: note about the map itself, which one pack already wrote and which is
#: worth keeping readable rather than exiling to a key of its own.
PARALLEL_NOTE_KEY = "note"

#: The manifest-shaped file a fixture's manifest string is written into for
#: the inference run. On door.MANIFEST_FILENAMES' own allowlist, so
#: _manifest_string_hit will actually open it.
FIXTURE_MANIFEST_FILE = "requirements.txt"


def pack_names():
    """Every pack in scripts/packs, by lens name, sorted. Raises rather than
    returning [] when nothing is found: an empty list would turn a broken
    checkout into a green run over zero packs, which is the
    NO-DATA-composed-into-a-PASS shape this estate has been bitten by."""
    names = sorted(f[: -len(".json")] for f in os.listdir(PACKS_DIR)
                   if f.endswith(".json"))
    if not names:
        raise AssertionError("no packs found in %s" % PACKS_DIR)
    return names


def _first_list(fixture, keys):
    """The first of `keys` present in `fixture` whose value is a list, as a
    list of strings; [] when none is."""
    for key in keys:
        value = fixture.get(key)
        if isinstance(value, list):
            return [str(v) for v in value]
    return []


def _manifest_strings(fixture):
    """The fixture's manifest string(s), whether it spelled the key singular
    with a string or plural with a list."""
    for key in MANIFEST_KEYS:
        value = fixture.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
    return []


def _tree_with(paths, manifest_strings):
    """(root, listed_files) for a temp tree holding `paths` as real empty
    files plus, when `manifest_strings` is non-empty, a requirements.txt
    holding them. The caller removes the root."""
    root = tempfile.mkdtemp(prefix="pack-fixture-")
    listed = []
    for rel in paths:
        clean = str(rel).replace("\\", "/").lstrip("/")
        if not clean or ".." in clean.split("/"):
            continue
        full = os.path.join(root, *clean.split("/"))
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("")
        listed.append(clean)
    if manifest_strings:
        with open(os.path.join(root, FIXTURE_MANIFEST_FILE), "w",
                 encoding="utf-8") as fh:
            fh.write("\n".join(manifest_strings) + "\n")
        listed.append(FIXTURE_MANIFEST_FILE)
    return root, sorted(listed)


class EveryPackLoads(unittest.TestCase):
    def test_load_pack_accepts_every_pack(self):
        for name in pack_names():
            with self.subTest(pack=name):
                pack = door_mod.load_pack(name)
                self.assertEqual(pack.get("lens"), name,
                                 "%s.json declares lens %r"
                                 % (name, pack.get("lens")))
                self.assertTrue(str(pack.get("version") or "").strip(),
                                "%s carries no version" % name)


class EveryPackCarriesTheFullManifest(unittest.TestCase):
    """Persona doc 5.1. The two pre-5.1 packs are exempt BY NAME and only
    until the follow-up row backfills them (FULL_MANIFEST_EXEMPT above)."""

    def test_the_five_one_keys_are_present(self):
        for name in pack_names():
            if name in FULL_MANIFEST_EXEMPT:
                continue
            pack = door_mod.load_pack(name)
            for key in FULL_MANIFEST_KEYS:
                with self.subTest(pack=name, key=key):
                    self.assertIn(key, pack,
                                  "%s is missing %r (persona doc 5.1)"
                                  % (name, key))

    def test_the_exemption_list_only_names_packs_that_exist(self):
        """An allowlist that outlives its packs silently exempts nothing and
        reads as if it still matters. This fails the day one of the two is
        renamed or removed, which is when the list should be revisited."""
        names = pack_names()
        for name in FULL_MANIFEST_EXEMPT:
            self.assertIn(name, names,
                          "FULL_MANIFEST_EXEMPT names %r, which no longer "
                          "exists: drop it from the list" % name)


class EveryPackCarriesOneEvidenceAndReceiptShape(unittest.TestCase):
    """One shape for thirteen packs. Six lanes wrote required_evidence_
    families and receipt_fields as lists of objects and five as flat lists
    of strings, so door.pack_union deduplicated a mixture and any reader of
    a composed receipt had to handle both. The shape of record is
    data-science.json's: flat lists of strings, with the inline why,
    meaning and fill route moved to the parallel key beside them."""

    def test_both_lists_are_flat_lists_of_names(self):
        for name in pack_names():
            pack = door_mod.load_pack(name)
            for key, _parallel in LIST_AND_PARALLEL:
                values = pack.get(key)
                with self.subTest(pack=name, key=key):
                    self.assertIsInstance(
                        values, list, "%s: %s is %r, not a list"
                        % (name, key, type(values).__name__))
                    self.assertTrue(values, "%s: %s is empty" % (name, key))
                    for value in values:
                        self.assertIsInstance(
                            value, str,
                            "%s: %s holds %r, not a plain name (persona doc "
                            "5.1 shape, data-science.json)"
                            % (name, key, value))
                        self.assertTrue(value.strip(),
                                        "%s: %s holds a blank entry"
                                        % (name, key))
                    self.assertEqual(len(values), len(set(values)),
                                     "%s: %s repeats a name" % (name, key))

    def test_the_parallel_key_describes_only_names_on_the_list(self):
        """A why or a detail for a name the list does not carry is prose
        nobody reaches, and a name that lost its prose in the move is
        content the normalization dropped: both are caught here."""
        for name in pack_names():
            pack = door_mod.load_pack(name)
            for key, parallel in LIST_AND_PARALLEL:
                described = pack.get(parallel)
                if described is None:
                    continue
                with self.subTest(pack=name, key=parallel):
                    self.assertIsInstance(
                        described, dict,
                        "%s: %s is %r, not an object keyed by the names in "
                        "%s" % (name, parallel, type(described).__name__,
                                key))
                    extra = sorted(set(described)
                                   - set(pack.get(key) or [])
                                   - {PARALLEL_NOTE_KEY})
                    self.assertFalse(
                        extra, "%s: %s describes %r, which %s does not list"
                        % (name, parallel, extra, key))
                    for entry, body in described.items():
                        self.assertTrue(
                            str(body or "").strip() if isinstance(body, str)
                            else bool(body),
                            "%s: %s says nothing about %r"
                            % (name, parallel, entry))


class EveryPackProvesItsOwnDetection(unittest.TestCase):
    """The fixture is the pack's own self-check (persona doc 5.1, "fixture
    suite and self-checks"): the tree it says must infer it, and the tree it
    says must not. Run through door.infer_lenses against the REAL packs
    directory, so a signal that collides with a sibling pack is found here
    rather than on somebody's repository."""

    def _fixture(self, name):
        fixture = door_mod.load_pack(name).get("fixture")
        return fixture if isinstance(fixture, dict) else None

    def test_each_fixture_infers_its_own_lens(self):
        for name in pack_names():
            fixture = self._fixture(name)
            if fixture is None:
                self.assertIn(name, FULL_MANIFEST_EXEMPT,
                              "%s carries no fixture" % name)
                continue
            positive = _first_list(fixture, POSITIVE_KEYS)
            strings = _manifest_strings(fixture)
            with self.subTest(pack=name):
                self.assertTrue(positive or strings,
                                "%s's fixture names nothing that must infer "
                                "it" % name)
                root, listed = _tree_with(positive, strings)
                try:
                    inferred = door_mod.infer_lenses(root, listed)
                finally:
                    shutil.rmtree(root, ignore_errors=True)
                names = [lens for lens, _matched in inferred]
                self.assertIn(name, names,
                              "%s's own fixture inferred %r instead"
                              % (name, names))

    def test_each_fixtures_foreign_paths_do_not_infer_it(self):
        for name in pack_names():
            fixture = self._fixture(name)
            if fixture is None:
                continue
            negative = _first_list(fixture, NEGATIVE_KEYS)
            with self.subTest(pack=name):
                self.assertTrue(negative,
                                "%s's fixture names no paths that must NOT "
                                "infer it, so its signals are untested "
                                "against a foreign tree" % name)
                root, listed = _tree_with(negative, [])
                try:
                    inferred = door_mod.infer_lenses(root, listed)
                finally:
                    shutil.rmtree(root, ignore_errors=True)
                names = [lens for lens, _matched in inferred]
                self.assertNotIn(name, names,
                                 "%s inferred itself from paths its own "
                                 "fixture says are foreign: %r"
                                 % (name, negative))


class EveryForcingClassIsReadable(unittest.TestCase):
    """receipt_door.py builds a parking trigger from each forcing class's
    id, and a person reads its why. An entry missing either is a class that
    parks a unit and cannot say what for."""

    def test_id_label_and_why_are_all_present(self):
        for name in pack_names():
            pack = door_mod.load_pack(name)
            classes = pack.get("forcing_classes") or []
            with self.subTest(pack=name):
                self.assertTrue(classes,
                                "%s declares no forcing classes" % name)
            for entry in classes:
                with self.subTest(pack=name, entry=entry):
                    self.assertIsInstance(entry, dict)
                    for key in ("id", "label", "why"):
                        self.assertTrue(str(entry.get(key) or "").strip(),
                                        "%s: a forcing class is missing %r"
                                        % (name, key))

    def test_every_forcing_class_builds_a_trigger(self):
        """The whole point of the id: door.forcing_class_triggers turns it
        into the word-bounded pattern receipt_door parks on. An id that
        produces no pattern is a class that can never fire."""
        for name in pack_names():
            pack = door_mod.load_pack(name)
            triggers = door_mod.forcing_class_triggers(pack)
            with self.subTest(pack=name):
                self.assertEqual(
                    len(triggers), len(pack.get("forcing_classes") or []),
                    "%s: a forcing class produced no trigger pattern" % name)


class NoDashesAnywhere(unittest.TestCase):
    def test_no_pack_file_holds_an_em_or_en_dash(self):
        for name in pack_names():
            path = os.path.join(PACKS_DIR, "%s.json" % name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for dash in FORBIDDEN_DASHES:
                with self.subTest(pack=name, dash=hex(ord(dash))):
                    self.assertNotIn(
                        dash, text,
                        "%s holds %s at character %d"
                        % (name, hex(ord(dash)), text.find(dash)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
