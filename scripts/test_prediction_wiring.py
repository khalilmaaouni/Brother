#!/usr/bin/env python3
"""P1.c: every judging tool writes a claim, and the loop mirror cannot drift silently.

The wiring reader (wired_predictors), the mirror reader (mirror_matches) and the claim writer
(record_claim) live in scripts/loop/grade_build.py, beside the tools they read. Every fixture here is
built in a temp folder, so this suite runs in the export tree with an empty HOME.
Run: python3 -B scripts/test_prediction_wiring.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))  # noqa: E402
from grade_build import mirror_matches, record_claim, wired_predictors  # noqa: E402

LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
FIVE = {"spec_score": "spec_score.py", "grade": "grade_build.py", "probe": "probe_build.py",
        "council": "spec_council.py", "diagnostic": "diag_brief.py"}


def _load(path):
    """A ledger module loaded by path, or None when it is missing or broken: the loading grade_build used to do,
    kept here because only a test hands in a ledger from a file; deployed code imports the real one by name."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("_test_ledger_%d" % abs(hash(path)), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, SyntaxError, ValueError, AttributeError):
        return None


def _write(folder, name, text):
    """Write text to folder/name and return the full path."""
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class WiringIsReadFromTheSource(unittest.TestCase):
    def test_a_source_that_calls_the_ledger_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            a = _write(d, "probe_build.py", "def judge():\n    predict(subject='B1.a')\n")
            b = _write(d, "grade_build.py", "def judge():\n    return 1\n")
            self.assertEqual(wired_predictors([a, b]), {"probe": a, "grade": ""})

    def test_a_source_that_never_calls_the_ledger_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            c = _write(d, "spec_council.py", "# this seat judges and writes nothing\n")
            self.assertEqual(wired_predictors([c]), {"council": ""})

    def test_a_source_that_cannot_be_read_is_never_wired(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(wired_predictors([os.path.join(d, "spec_score.py")]), {"spec_score": ""})
            binary = os.path.join(d, "diag_brief.py")
            with open(binary, "wb") as fh:
                fh.write(b"\xff\xfe\x00")
            self.assertEqual(wired_predictors([binary]), {"diagnostic": ""})

    @unittest.skipUnless(all(os.path.isfile(os.path.join(LOOP, n)) for n in FIVE.values()),
                         "the five loop tools are not in this tree")
    def test_the_five_judging_tools_are_wired(self):
        got = wired_predictors([os.path.join(LOOP, n) for n in FIVE.values()])
        self.assertEqual(sorted(got), sorted(FIVE))
        for predictor, call_site in sorted(got.items()):
            self.assertTrue(call_site, "%s judges and writes no claim" % predictor)


class MirrorMatchesNamesEveryDrift(unittest.TestCase):
    def test_identical_trees_are_an_empty_list(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write(a, "spec_score.py", "same bytes\n")
            _write(b, "spec_score.py", "same bytes\n")
            self.assertEqual(mirror_matches(a, b), [])

    def test_a_same_size_different_byte_names_the_file(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write(a, "probe_build.py", "one\n")
            _write(b, "probe_build.py", "two\n")
            self.assertEqual(mirror_matches(a, b), ["probe_build.py"])

    def test_a_file_present_on_one_side_only_is_named(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write(a, "diag_brief.py", "one\n")
            self.assertEqual(mirror_matches(a, b), ["diag_brief.py"])


class AClaimIsWrittenOnlyWhenTheLedgerAccepts(unittest.TestCase):
    def test_a_missing_ledger_is_false_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(record_claim("probe", "B1.a", "this build is clean", 1.0,
                                          _load(os.path.join(d, "no_ledger_here.py"))))

    def test_a_working_ledger_receives_the_row(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "rows.txt")
            source = ("import json\n"
                      "def predict(predictor, subject, claim, confidence=None):\n"
                      "    with open(%r, 'a', encoding='utf-8') as fh:\n"
                      "        fh.write(json.dumps([predictor, subject, claim, confidence]) + '\\n')\n") % out
            ledger = _write(d, "ledger_ok.py", source)
            self.assertTrue(record_claim("probe", "B1.a", "this build is clean", 0.5, _load(ledger)))
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(json.loads(fh.read().strip()), ["probe", "B1.a", "this build is clean", 0.5])

    def test_a_ledger_with_no_predict_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = _write(d, "ledger_empty.py", "VALUE = 1\n")
            self.assertFalse(record_claim("probe", "B1.a", "this build is clean", None, _load(ledger)))

    def test_a_broken_ledger_is_false_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            broken = _write(d, "ledger_broken.py", "raise ValueError('broken at import')\n")
            self.assertFalse(record_claim("probe", "B1.a", "this build is clean", None, _load(broken)))
            refusing = _write(d, "ledger_refuses.py",
                              "def predict(predictor, subject, claim, confidence=None):\n"
                              "    raise ValueError('refused')\n")
            self.assertFalse(record_claim("probe", "B1.a", "this build is clean", 0.5, _load(refusing)))


class TheRealLedgerIsImportedByName(unittest.TestCase):
    def test_grade_build_has_no_file_path_loader_the_freeze_cannot_trace(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "loop", "grade_build.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("spec_from_file_location", source)
        self.assertIn("import prediction_ledger as _PREDICTION_LEDGER", source)


class HostileInputIsRefused(unittest.TestCase):
    def test_a_wrong_type_for_paths_is_a_value_error(self):
        for bad in (None, 3, 2.5, b"x", "scripts/loop/spec_score.py", {"a": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    wired_predictors(bad)
        for bad in ([None], [""], [7], [b"x"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    wired_predictors(bad)

    def test_a_wrong_type_for_a_directory_is_a_value_error(self):
        for bad in (None, 3, b"/tmp", []):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    mirror_matches(bad, "/tmp")
                with self.assertRaises(ValueError):
                    mirror_matches("/tmp", bad)
        with self.assertRaises(ValueError):
            mirror_matches("/tmp", os.path.join("/tmp", "p1c-not-a-directory-9f3a"))

    def test_a_hostile_claim_argument_is_a_value_error(self):
        for bad in (None, "", 3, b"x", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    record_claim(bad, "B1.a", "clean")
                with self.assertRaises(ValueError):
                    record_claim("probe", bad, "clean")
                with self.assertRaises(ValueError):
                    record_claim("probe", "B1.a", bad)
        for bad in (True, False, "0.5", float("nan"), 2.0, -0.1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    record_claim("probe", "B1.a", "clean", bad)



class ALostClaimIsSaidOutLoud(unittest.TestCase):
    """2026-09-30: every caller discards record_claim's False, so the loss is surfaced inside record_claim."""

    def test_a_ledger_that_refuses_the_write_prints_no_data_on_stderr(self):
        import contextlib, io

        class Refusing:
            @staticmethod
            def predict(**kw):
                raise OSError("disk full")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(record_claim("probe", "B1.a", "this build is clean", 0.5, Refusing))
        self.assertIn("NO-DATA", err.getvalue())
        self.assertIn("B1.a", err.getvalue())

if __name__ == "__main__":
    unittest.main()
