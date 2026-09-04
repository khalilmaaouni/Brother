#!/usr/bin/env python3
"""Calibration for tools/bm_vault_realdata.py: GLEIF-derived real business
data (17 Toyota-group legal entities, 16 consolidation edges) as a golden
set exercising bm_vault_shapes.py, bm_vault_closure.py and
bm_vault_crosswalk.py together. The property under test is not the parsers
(those already have their own calibration) but the business answer: the
fixture's own three-level tree resolves through the real models to the
real GLEIF ancestor, and each model's guard fires when the fixture is
corrupted, never only when it is clean.

No em or en dashes anywhere in this file.
"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_closure as cl       # noqa: E402
import bm_vault_crosswalk as xw     # noqa: E402
import bm_vault_realdata as rd      # noqa: E402
import bm_vault_shapes as sh        # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "gleif-toyota-group.json")
ROOT = "5493006W3QUS5LMH6R84"
ROOT_NAME = "トヨタ自動車株式会社"
FS = "353800WDOBRSAV97BA75"
AU = "3UKPTDP5PGQRH8AUK042"


def run(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue() + err.getvalue()


def load_data():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def brute_force_ancestor_pairs(data):
    """{(ancestor, descendant), ...} computed directly from data["edges"],
    independent of bm_vault_closure.py's own walk, for test C to compare
    the tool's stored table against."""
    parent_of = {}
    for e in data["edges"]:
        parent_of.setdefault(e["child"], []).append(e["parent"])
    pairs = set()
    for descendant in data["records"]:
        current, seen = descendant, set()
        while True:
            parents = parent_of.get(current, [])
            if not parents:
                break
            parent = parents[0]
            if parent in seen:
                break
            pairs.add((parent, descendant))
            seen.add(current)
            current = parent
    return pairs


class TestFixtureIntegrity(unittest.TestCase):
    """Test (a): the fixture itself is what the brief claims it is, before
    any model ever touches it."""

    def setUp(self):
        self.data = load_data()

    def test_record_and_edge_counts(self):
        self.assertEqual(len(self.data["records"]), 17)
        self.assertEqual(len(self.data["edges"]), 16)

    def test_every_edge_endpoint_is_a_record(self):
        for e in self.data["edges"]:
            self.assertIn(e["parent"], self.data["records"])
            self.assertIn(e["child"], self.data["records"])

    def test_provenance_fields_present(self):
        self.assertTrue(self.data.get("fetched_at"))
        self.assertTrue(self.data.get("source"))
        for lei, rec in self.data["records"].items():
            self.assertTrue(rec.get("url"), "record %s has no url" % lei)

    def test_root_is_the_japanese_parent_byte_exact(self):
        self.assertEqual(self.data["root"], ROOT)
        self.assertEqual(self.data["records"][ROOT]["legalName"], ROOT_NAME)


class VaultFixture(unittest.TestCase):
    """Common tempdir plumbing for tests that need a built vault on disk."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-realdata-test-")
        self.data = load_data()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, data=None):
        return rd.build_vault(data or self.data, self.tmp)


class TestBuildAndResolve(VaultFixture):
    """Test (b): build produces a vault bm_vault_shapes.py itself calls
    clean, and the AU entity's hierarchy resolves to the root."""

    def test_shapes_check_passes_on_the_built_vault(self):
        self.build()
        rc, out = run(sh.cmd_check, self.tmp)
        self.assertEqual(rc, 0, out)

    def test_au_resolves_through_fs_to_root(self):
        self.build()
        as_of = rd.datetime.date.today()
        rc, out = run(sh.cmd_resolve_hierarchy, self.tmp, AU, "legal", as_of)
        self.assertEqual(rc, 0, out)
        self.assertIn("%s -> %s" % (FS, ROOT), out)


class TestClosureMatchesBruteForce(VaultFixture):
    """Test (c): closure rebuild+verify exit clean, and the stored table's
    (ancestor, descendant) pairs equal an independently computed brute
    force transitive closure over data["edges"], exactly, not a subset."""

    def setUp(self):
        super().setUp()
        self._orig_store = cl.STORE_PATH
        cl.STORE_PATH = os.path.join(self.tmp, "closure-store.json")

    def tearDown(self):
        cl.STORE_PATH = self._orig_store
        super().tearDown()

    def test_stored_pairs_equal_brute_force(self):
        self.build()
        as_of = rd.datetime.date.today()
        rc, out = run(cl.cmd_rebuild, self.tmp, as_of)
        self.assertEqual(rc, 0, out)
        rc, out = run(cl.cmd_verify, self.tmp)
        self.assertEqual(rc, 0, out)

        with open(cl.STORE_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
        stored_pairs = set()
        for r in stored["rows"]:
            stored_pairs.add((r["ancestor"], r["descendant"]))
        expected = brute_force_ancestor_pairs(self.data)
        self.assertEqual(stored_pairs, expected)


class TestBackwardsCycle(VaultFixture):
    """Test (d), driven backwards: a cycle edge (root made a child of the
    AU entity it already descends from) must make closure rebuild refuse,
    never silently store a partial or wrong table."""

    def setUp(self):
        super().setUp()
        self._orig_store = cl.STORE_PATH
        cl.STORE_PATH = os.path.join(self.tmp, "closure-store.json")

    def tearDown(self):
        cl.STORE_PATH = self._orig_store
        super().tearDown()

    def test_cycle_refuses_rebuild(self):
        cyclic = copy.deepcopy(self.data)
        bad_edge = dict(parent=AU, child=ROOT, rel="TEST_CYCLE")
        cyclic["edges"].append(bad_edge)
        self.build(cyclic)
        rc, out = run(cl.cmd_rebuild, self.tmp, rd.datetime.date.today())
        self.assertNotEqual(rc, 0, out)


class TestBackwardsCrosswalkDuplicate(VaultFixture):
    """Test (e), driven backwards: claiming one entity's LEI source_id on a
    second note must make crosswalk check fail, never silently resolve to
    whichever note happened to be read first."""

    def test_duplicate_source_id_fails_crosswalk_check(self):
        entities_dir = self.build()
        au_path = os.path.join(entities_dir, "%s.md" % AU)
        other_lei = [lei for lei in self.data["records"] if lei != AU][0]
        other_path = os.path.join(entities_dir, "%s.md" % other_lei)
        with open(au_path, encoding="utf-8") as fh:
            au_text = fh.read()
        self.assertIn("plugin:lei:%s" % AU, au_text)
        with open(other_path, "a", encoding="utf-8") as fh:
            fh.write("\n<!-- duplicate claim below, appended for test (e) -->\n")
        with open(other_path, encoding="utf-8") as fh:
            other_text = fh.read()
        patched = other_text.replace(
            "source_ids: [plugin:lei:%s]" % other_lei,
            "source_ids: [plugin:lei:%s, plugin:lei:%s]" % (other_lei, AU))
        self.assertNotEqual(patched, other_text, "source_ids line was not found to patch")
        with open(other_path, "w", encoding="utf-8") as fh:
            fh.write(patched)
        rc, out = run(xw.cmd_check, self.tmp)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("already claimed", out)


class TestBackwardsDanglingReference(VaultFixture):
    """Test (f), driven backwards: removing a record that is a declared
    hierarchy parent, while its edges survive, must make shapes check
    report the dangling parent as a FINDING (FAIL), never a silent PASS."""

    def test_removed_parent_record_is_dangling(self):
        broken = copy.deepcopy(self.data)
        del broken["records"][FS]
        self.build(broken)
        rc, out = run(sh.cmd_check, self.tmp)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("DANGLING", out)


class TestJapaneseRoundTrip(VaultFixture):
    """Test (g): the root note carries the Japanese legal name byte-exact,
    and reading the legal_name field back off disk equals the fixture."""

    def test_root_note_carries_the_japanese_name(self):
        entities_dir = self.build()
        root_path = os.path.join(entities_dir, "%s.md" % ROOT)
        with open(root_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(ROOT_NAME, text)
        needle = 'legal_name: "%s"' % ROOT_NAME
        self.assertIn(needle, text)
        for line in text.splitlines():
            if line.startswith("legal_name: "):
                value = line[len('legal_name: "'):-1]
                self.assertEqual(value, ROOT_NAME)
                break
        else:
            self.fail("no legal_name line found in %s" % root_path)


class TestNoDataFixture(unittest.TestCase):
    """Test (h): checking a nonexistent fixture path prints NO-DATA naming
    the path, at a nonzero exit, never a silent empty pass."""

    def test_missing_fixture_is_nodata(self):
        bogus = "/no/such/path/gleif-toyota-group.json"
        rc, out = run(rd.main, ["check", "--fixture", bogus])
        self.assertNotEqual(rc, 0)
        self.assertIn("NO-DATA", out)
        self.assertIn(bogus, out)


if __name__ == "__main__":
    unittest.main()
