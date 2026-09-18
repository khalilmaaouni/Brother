#!/usr/bin/env python3
"""Tests for scripts/fixture_classes.py. Plain unittest, run directly:
python3 scripts/test_fixture_classes.py -v
"""

import json
import os
import shutil
import tempfile
import unittest

import fixture_classes

# Pinned per the worker contract's test rule: enumerate the classes against
# a fixed expected set rather than just checking membership, so a class
# silently added to or dropped from SCHEMA.json fails this test.
EXPECTED_CLASSES = frozenset((
    "frontend", "mobile", "api_contract", "data_pipeline",
    "dependency_update", "refactor", "matching_logic",
))


def _write_manifest(corpus_root, fixture_id, klass, seeded_defect,
                     extra_files=None):
    fixture_dir = os.path.join(corpus_root, fixture_id)
    os.makedirs(fixture_dir)
    manifest = {"class": klass, "seeded_defect": seeded_defect}
    with open(os.path.join(fixture_dir, "fixture.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh)
    for name, content in (extra_files or {}).items():
        with open(os.path.join(fixture_dir, name), "w",
                  encoding="utf-8") as fh:
            fh.write(content)
    return fixture_dir


class ClassListTest(unittest.TestCase):

    def test_classes_match_pinned_set(self):
        self.assertEqual(fixture_classes.CLASSES, EXPECTED_CLASSES)


class ScanEmptyCorpusTest(unittest.TestCase):

    def test_missing_corpus_root_is_empty_not_an_error(self):
        missing = os.path.join(tempfile.gettempdir(),
                                "fixture-classes-test-does-not-exist")
        self.assertFalse(os.path.exists(missing))
        self.assertEqual(fixture_classes.scan_corpus(missing), ())

    def test_empty_corpus_dir_is_empty(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertEqual(fixture_classes.scan_corpus(tmp), ())
        finally:
            shutil.rmtree(tmp)

    def test_default_corpus_root_ships_empty(self):
        # DOM-30.02's own note: no real fixture ships tonight, so the
        # committed default corpus reports no fixtures at all. This test
        # would need updating the day a real fixture is added, which is
        # the point: it proves nothing fabricated snuck in under corpus/.
        self.assertEqual(fixture_classes.scan_corpus(None), ())


class ScanValidCorpusTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_one_fixture_per_class_is_fully_covered(self):
        for i, klass in enumerate(sorted(EXPECTED_CLASSES)):
            _write_manifest(self.tmp, "fx-%02d-%s" % (i, klass), klass,
                             "seeded defect for %s" % (klass,))
        report = fixture_classes.coverage_report(self.tmp)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["missing_classes"], ())
        for klass in EXPECTED_CLASSES:
            self.assertEqual(len(report["classes"][klass]), 1)

    def test_two_fixtures_same_class_both_counted(self):
        _write_manifest(self.tmp, "fx-a", "frontend", "defect a")
        _write_manifest(self.tmp, "fx-b", "frontend", "defect b")
        report = fixture_classes.coverage_report(self.tmp)
        self.assertEqual(report["classes"]["frontend"], ("fx-a", "fx-b"))
        self.assertIn("frontend", [
            k for k, ids in report["classes"].items() if ids])
        self.assertNotEqual(report["verdict"], "PASS")  # 6 classes still missing


class CoverageNoDataTest(unittest.TestCase):

    def test_empty_corpus_reports_no_data_naming_every_class(self):
        tmp = tempfile.mkdtemp()
        try:
            report = fixture_classes.coverage_report(tmp)
            self.assertEqual(report["verdict"], "NO-DATA")
            self.assertEqual(report["missing_classes"],
                              tuple(sorted(EXPECTED_CLASSES)))
        finally:
            shutil.rmtree(tmp)

    def test_default_corpus_reports_no_data(self):
        # Proves the harness never turns "nothing shipped yet" into a
        # fabricated pass, against the real committed (empty) corpus root.
        report = fixture_classes.coverage_report(None)
        self.assertEqual(report["verdict"], "NO-DATA")

    def test_missing_class_never_counted_as_covered(self):
        # THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a coverage_report()
        # that only checked "at least one class has a fixture" instead of
        # "every class has a fixture" would read this six-of-seven corpus
        # as covered. This test catches exactly that regression.
        tmp = tempfile.mkdtemp()
        try:
            classes = sorted(EXPECTED_CLASSES)
            for i, klass in enumerate(classes[:-1]):  # all but one
                _write_manifest(tmp, "fx-%02d" % (i,), klass, "seeded")
            report = fixture_classes.coverage_report(tmp)
            self.assertEqual(report["verdict"], "NO-DATA")
            self.assertEqual(report["missing_classes"], (classes[-1],))
        finally:
            shutil.rmtree(tmp)


class MalformedCorpusRaisesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_directory_without_manifest_raises(self):
        os.makedirs(os.path.join(self.tmp, "no-manifest-here"))
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_non_directory_entry_raises(self):
        with open(os.path.join(self.tmp, "stray.txt"), "w") as fh:
            fh.write("not a fixture directory")
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_invalid_json_manifest_raises(self):
        fixture_dir = os.path.join(self.tmp, "fx-bad-json")
        os.makedirs(fixture_dir)
        with open(os.path.join(fixture_dir, "fixture.json"), "w") as fh:
            fh.write("{not valid json")
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_manifest_not_an_object_raises(self):
        fixture_dir = os.path.join(self.tmp, "fx-list")
        os.makedirs(fixture_dir)
        with open(os.path.join(fixture_dir, "fixture.json"), "w") as fh:
            json.dump(["not", "an", "object"], fh)
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_missing_required_field_raises(self):
        fixture_dir = os.path.join(self.tmp, "fx-no-class")
        os.makedirs(fixture_dir)
        with open(os.path.join(fixture_dir, "fixture.json"), "w") as fh:
            json.dump({"seeded_defect": "something"}, fh)
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_unrecognised_class_raises(self):
        _write_manifest(self.tmp, "fx-weird", "backend-but-not-really",
                         "some defect")
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_blank_seeded_defect_raises(self):
        _write_manifest(self.tmp, "fx-blank", "frontend", "   ")
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)

    def test_missing_seeded_defect_raises(self):
        fixture_dir = os.path.join(self.tmp, "fx-no-defect")
        os.makedirs(fixture_dir)
        with open(os.path.join(fixture_dir, "fixture.json"), "w") as fh:
            json.dump({"class": "frontend"}, fh)
        with self.assertRaises(fixture_classes.FixtureCorpusError):
            fixture_classes.scan_corpus(self.tmp)


class MainCliTest(unittest.TestCase):

    def test_main_exits_nonzero_on_empty_corpus(self):
        tmp = tempfile.mkdtemp()
        try:
            rc = fixture_classes.main(["--corpus-root", tmp])
            self.assertEqual(rc, 1)
        finally:
            shutil.rmtree(tmp)

    def test_main_exits_zero_when_all_classes_covered(self):
        tmp = tempfile.mkdtemp()
        try:
            for i, klass in enumerate(sorted(EXPECTED_CLASSES)):
                _write_manifest(tmp, "fx-%02d" % (i,), klass, "seeded")
            rc = fixture_classes.main(["--corpus-root", tmp])
            self.assertEqual(rc, 0)
        finally:
            shutil.rmtree(tmp)

    def test_main_exits_2_on_corpus_defect(self):
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "broken"))
            rc = fixture_classes.main(["--corpus-root", tmp])
            self.assertEqual(rc, 2)
        finally:
            shutil.rmtree(tmp)

    def test_main_default_corpus_root_reports_no_data(self):
        rc = fixture_classes.main([])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
