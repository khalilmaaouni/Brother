#!/usr/bin/env python3
"""Drive prevented_word_gate both ways. No em or en dashes anywhere."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prevented_word_gate as G  # noqa: E402

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


class ItBlocksAnUnbackedPreventWord(unittest.TestCase):
    def test_a_bare_prevented_is_blocked(self):
        hits = G.scan_text("Brother prevented 3 recurrences last week.")
        self.assertEqual(len(hits), 1)

    def test_every_inflection_is_caught(self):
        for w in ("prevent", "prevents", "prevented", "prevention", "preventing"):
            self.assertEqual(len(G.scan_text("it %s things" % w)), 1, w)

    def test_a_prevent_word_with_a_run_citation_on_the_line_passes(self):
        hits = G.scan_text("prevented 3 (prevented_fraction run 2026-09-03-a).")
        self.assertEqual(hits, [])

    def test_a_citation_on_the_line_above_passes(self):
        text = "See prevented_fraction run r7 for the number.\nIt prevented 3."
        self.assertEqual(G.scan_text(text), [])

    def test_a_citation_two_lines_above_does_not_count(self):
        text = "prevented_fraction run r7\nsome other line\nit prevented 3"
        self.assertEqual(len(G.scan_text(text)), 1)

    def test_ordinary_prose_with_no_prevent_word_is_clean(self):
        self.assertEqual(G.scan_text("The receipt binds the claim to the commit."), [])


class ExemptFilesNameTheWordByNecessity(unittest.TestCase):
    def test_the_design_document_is_exempt_by_path(self):
        v, payload = G.scan_file("/x/y/PREVENTION-CONTROL-ARM-DESIGN.md")
        # exempt returns CLEAN with no hits without reading the file
        self.assertEqual((v, payload), ("CLEAN", []))

    def test_the_gate_source_is_exempt(self):
        self.assertEqual(G.scan_file("/a/prevented_word_gate.py"), ("CLEAN", []))


class UnreadableIsNoData(unittest.TestCase):
    def test_a_missing_file_is_NO_DATA_not_clean(self):
        v, reason = G.scan_file("/no/such/file/here.md")
        self.assertEqual(v, "NO-DATA")

    def test_strict_makes_no_data_a_failure(self):
        code = G.main(["/no/such/file.md", "--strict"])
        self.assertEqual(code, 2)


class TheCliVerdicts(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _write(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_a_clean_file_exits_zero(self):
        p = self._write("board.md", "The receipt count is three.")
        self.assertEqual(G.main([p]), 0)

    def test_an_unbacked_word_exits_one(self):
        p = self._write("readme.md", "Brother prevented failures.")
        self.assertEqual(G.main([p]), 1)

    def test_a_backed_word_exits_zero(self):
        p = self._write("note.md", "Brother prevented 3 (prevented_fraction run r9).")
        self.assertEqual(G.main([p]), 0)


if __name__ == "__main__":
    unittest.main()
