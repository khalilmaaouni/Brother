"""Tests for doc_numbers_check.py. Plain unittest, run directly:
python3 scripts/test_doc_numbers_check.py -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_numbers_check as dnc


class ExtractNumbers(unittest.TestCase):
    def test_every_shape(self):
        text = (
            "Percent 92% of runs passed.\n"
            "Cost $0.000022 per call, or 0.000022 USD elsewhere.\n"
            "Counted 23 of 24 items.\n"
            "Confidence 0.9 threshold.\n"
            "80 labelled items in the set.\n"
        )
        numbers = [tok for _lineno, tok in dnc.extract_numbers(text)]
        self.assertIn("92%", numbers)
        self.assertIn("$0.000022", numbers)
        self.assertIn("0.000022 USD", numbers)
        self.assertIn("23 of 24", numbers)
        self.assertIn("0.9", numbers)
        self.assertIn("80", numbers)

    def test_single_digit_not_captured(self):
        numbers = dnc.extract_numbers("1. First item in a list.\n")
        tokens = [tok for _lineno, tok in numbers]
        self.assertNotIn("1", tokens)

    def test_code_fence_ignored(self):
        text = (
            "Prose claims 42 things.\n"
            "```bash\n"
            "python3 scripts/x.py --count 99\n"
            "```\n"
            "More prose with 55 in it.\n"
        )
        tokens = [tok for _lineno, tok in dnc.extract_numbers(text)]
        self.assertIn("42", tokens)
        self.assertIn("55", tokens)
        self.assertNotIn("99", tokens)

    def test_date_line_ignored(self):
        text = "Date: 2026-09-18. Rows JEV-01 to JEV-06, run 1020.\nBody has 88 here.\n"
        tokens = [tok for _lineno, tok in dnc.extract_numbers(text)]
        self.assertNotIn("2026", tokens)
        self.assertNotIn("09", tokens)
        self.assertNotIn("18", tokens)
        self.assertNotIn("06", tokens)
        self.assertNotIn("1020", tokens)
        self.assertIn("88", tokens)

    def test_line_numbers_tracked(self):
        text = "no numbers here\nsecond line has 77\n"
        numbers = dnc.extract_numbers(text)
        self.assertEqual(numbers, [(2, "77")])


class NormalizeNumber(unittest.TestCase):
    def test_strips_dollar_and_usd(self):
        self.assertEqual(dnc.normalize_number("$0.000022"), "0.000022")
        self.assertEqual(dnc.normalize_number("0.000022 USD"), "0.000022")
        self.assertEqual(dnc.normalize_number("92%"), "92%")
        self.assertEqual(dnc.normalize_number("23 of 24"), "23 of 24")


class ParseEvidencePaths(unittest.TestCase):
    def test_reads_evidence_line(self):
        text = ("Body text.\n"
                "Evidence: benchmarks/jev_eval/README.md and docs/plan/x.json\n")
        paths = dnc.parse_evidence_paths(text)
        self.assertIn("benchmarks/jev_eval/README.md", paths)
        self.assertIn("docs/plan/x.json", paths)

    def test_no_evidence_line_returns_empty(self):
        self.assertEqual(dnc.parse_evidence_paths("just prose, no line\n"), [])


class GatherEvidence(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dnc-test-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_reads_a_file(self):
        self._write("evidence/a.md", "contains 92%\n")
        contents, unreadable = dnc.gather_evidence(["evidence/a.md"], self.root)
        self.assertIn("evidence/a.md", contents)
        self.assertEqual(unreadable, [])

    def test_walks_a_directory(self):
        self._write("evidence/a.md", "one\n")
        self._write("evidence/sub/b.md", "two\n")
        contents, unreadable = dnc.gather_evidence(["evidence/"], self.root)
        self.assertEqual(len(contents), 2)
        self.assertEqual(unreadable, [])

    def test_missing_path_is_unreadable(self):
        contents, unreadable = dnc.gather_evidence(["nope/missing.md"], self.root)
        self.assertEqual(contents, {})
        self.assertEqual(unreadable, ["nope/missing.md"])

    def test_empty_directory_is_unreadable(self):
        os.makedirs(os.path.join(self.root, "empty"))
        contents, unreadable = dnc.gather_evidence(["empty/"], self.root)
        self.assertEqual(contents, {})
        self.assertEqual(unreadable, ["empty/"])


class CheckNumbers(unittest.TestCase):
    def test_found_and_unfound(self):
        numbers = [(1, "92%"), (2, "77"), (3, "$0.5")]
        corpus = "the run measured 92% and cost 0.5 dollars\n"
        found, unfound = dnc.check_numbers(numbers, corpus)
        self.assertEqual(found, 2)
        self.assertEqual(unfound, [(2, "77")])


class Run(unittest.TestCase):
    """End-to-end over run(), the exit-code-deciding entry point."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dnc-run-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def test_pass_when_every_number_is_backed(self):
        self._write("evidence.md", "measured 92% on 23 of 24 items\n")
        doc = self._write("doc.md",
            "Body claims 92% success on 23 of 24 items.\n"
            "Evidence: evidence.md\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 0, lines)
        self.assertTrue(any("PASS" in l for l in lines))

    def test_fail_when_a_number_is_unbacked(self):
        self._write("evidence.md", "measured 92% only\n")
        doc = self._write("doc.md",
            "Body claims 92% and also 47% nobody measured.\n"
            "Evidence: evidence.md\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("47%" in l for l in lines))

    def test_number_only_in_another_doc_section_still_fails(self):
        # The doc repeats 47% in its own prose (a different section) but the
        # evidence file never carries it: DOC text is never its own evidence.
        self._write("evidence.md", "measured 92% only\n")
        doc = self._write("doc.md",
            "## Section one\nClaims 92% here.\n"
            "## Section two\nRepeats 47% here, unbacked.\n"
            "Evidence: evidence.md\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("47%" in l for l in lines))

    def test_evidence_line_ending_with_a_period_still_reads_the_last_file(self):
        # A path with a directory is the real shape: only the slash branch of
        # the path pattern could swallow the closing period.
        os.makedirs(os.path.join(self.root, "sub"), exist_ok=True)
        self._write("a.md", "has 92%\n")
        self._write(os.path.join("sub", "b.md"), "has 88%\n")
        doc = self._write("doc.md", "Claims 92% and 88%.\nEvidence: a.md; sub/b.md.\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 0, lines)

    def test_zero_numbers_is_no_data(self):
        # Evidence is written and readable, so the ONLY reason left for
        # NO-DATA is the zero-numbers rule (the orchestrator's mutation of
        # that rule survived while x.md did not exist: the test was passing
        # on the unreadable-evidence path instead).
        self._write("x.md", "readable evidence, 42 and 92%\n")
        doc = self._write("doc.md", "Nothing but words here.\nEvidence: x.md\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(any("zero number-bearing tokens" in l for l in lines), lines)

    def test_unreadable_doc_is_no_data(self):
        code, lines = dnc.run(os.path.join(self.root, "absent.md"), [], self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(any("NO-DATA" in l for l in lines))

    def test_unreadable_evidence_is_no_data(self):
        doc = self._write("doc.md",
            "Claims 92% here.\nEvidence: does/not/exist.md\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(any("NO-DATA" in l for l in lines))

    def test_no_evidence_named_is_no_data(self):
        doc = self._write("doc.md", "Claims 92% here with no evidence line.\n")
        code, lines = dnc.run(doc, [], self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(any("NO-DATA" in l for l in lines))

    def test_explicit_evidence_flag_supplements_doc_line(self):
        self._write("extra.md", "backs 47% too\n")
        doc = self._write("doc.md", "Claims 47% here.\nEvidence: nothing/here.md\n")
        code, lines = dnc.run(doc, [os.path.relpath(
            os.path.join(self.root, "extra.md"), self.root)], self.root)
        self.assertEqual(code, 0, lines)


if __name__ == "__main__":
    unittest.main()
