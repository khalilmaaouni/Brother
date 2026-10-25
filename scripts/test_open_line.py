#!/usr/bin/env python3
"""open_line.py writes the release-invariant declaration when a release line opens, and only then.

Each case runs the entry point, main(), against a fixture expectations file (open_line.PATH repointed), never the
repository's own file. Run: python3 -B scripts/test_open_line.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import open_line as OL  # noqa: E402


class OpenLine(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.mkdtemp(prefix="open-line-")
        self.addCleanup(shutil.rmtree, self.box, True)
        self.path = os.path.join(self.box, "BATTERY-EXPECTATIONS.json")
        self.other = {"class": "expected_unavailable", "recorded": "2026-10-01", "review_by": "2026-11-01"}
        self.write({"_comment": "fixture", "checks": {"gpg-sign": self.other}, "critical": {}})
        saved = OL.PATH
        OL.PATH = self.path
        self.addCleanup(setattr, OL, "PATH", saved)

    def write(self, doc):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")

    def run_main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = OL.main(list(argv))
        return code, buf.getvalue()

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def test_a_new_line_gets_the_declaration(self):
        code, out = self.run_main("--version", "1.1.9", "--today", "2026-10-24")
        self.assertEqual(code, 0, out)
        self.assertIn("DECLARED", out)
        entry = json.loads(self.read())["checks"]["release-invariant"]
        self.assertEqual(entry["recorded"], "2026-10-24")
        self.assertEqual(entry["review_by"], "2026-11-07")
        self.assertIn("the 1.1.9 cut removes this entry", entry["removal_condition"])
        self.assertEqual(entry["class"], "expected_unavailable")

    def test_every_other_entry_is_left_alone_and_the_format_matches_the_cut(self):
        self.run_main("--version", "1.1.9", "--today", "2026-10-24")
        text = self.read()
        doc = json.loads(text)
        self.assertEqual(doc["checks"]["gpg-sign"], self.other)
        self.assertEqual(doc["_comment"], "fixture")
        self.assertEqual(text, json.dumps(doc, indent=2) + "\n")

    def test_an_existing_declaration_is_never_rewritten(self):
        self.run_main("--version", "1.1.9", "--today", "2026-10-24")
        before = self.read()
        code, out = self.run_main("--version", "1.2.0", "--today", "2026-12-01")
        self.assertEqual(code, 0, out)
        self.assertIn("ALREADY", out)
        self.assertEqual(self.read(), before)

    def test_a_version_that_is_not_x_y_z_writes_nothing(self):
        before = self.read()
        for bad in ("1.1", "v1.1.9", "1.1.9-dev", ""):
            code, out = self.run_main("--version", bad, "--today", "2026-10-24")
            self.assertEqual(code, 2, (bad, out))
            self.assertIn("NO-DATA", out)
        self.assertEqual(self.read(), before)

    def test_an_unreadable_file_refuses(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        code, out = self.run_main("--version", "1.1.9", "--today", "2026-10-24")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertEqual(self.read(), "{ not json")

    def test_a_file_without_checks_refuses(self):
        self.write({"critical": {}})
        code, out = self.run_main("--version", "1.1.9", "--today", "2026-10-24")
        self.assertEqual(code, 2, out)
        self.assertNotIn("release-invariant", self.read())

    def test_a_bad_date_refuses(self):
        code, out = self.run_main("--version", "1.1.9", "--today", "24/10/2026")
        self.assertEqual(code, 2, out)
        self.assertNotIn("release-invariant", self.read())


if __name__ == "__main__":
    unittest.main()
