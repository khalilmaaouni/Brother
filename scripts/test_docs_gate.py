"""D13.4 docs gate tests: generated record freshness and NO-DATA state line."""
import os
import tempfile
import unittest

import system_doc


class TestDocsGate(unittest.TestCase):
    def test_docs_uptodate_passes(self):
        body = system_doc.build()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "SYSTEM.md")
            if body is None:
                self.assertFalse(system_doc.check_system_doc_uptodate(out))
                return
            with open(out, "wb") as fh:
                fh.write(body.encode("utf-8"))
            self.assertTrue(system_doc.check_system_doc_uptodate(out))

    def test_docs_missing_battery_blocks_pass(self):
        old = system_doc.build
        try:
            system_doc.build = lambda: None
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "SYSTEM.md")
                self.assertFalse(system_doc.check_system_doc_uptodate(out))
        finally:
            system_doc.build = old

    def test_docs_stale_refused(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "SYSTEM.md")
            with open(out, "wb") as fh:
                fh.write(b"stale generated record\n")
            self.assertFalse(system_doc.check_system_doc_uptodate(out))

    def test_docs_missing_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(system_doc.check_system_doc_uptodate(os.path.join(d, "absent.md")))

    def test_docs_directory_path_refused_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(system_doc.check_system_doc_uptodate(d))

    def test_docs_state_line_groups_and_nd(self):
        line = system_doc.docs_state_line(system_doc.SCRIPTS)
        self.assertIn("independent groups", line)
        self.assertIn("NO-DATA", line)

    def test_docs_state_line_missing_dir_is_nd(self):
        with tempfile.TemporaryDirectory() as d:
            line = system_doc.docs_state_line(os.path.join(d, "absent"))
            self.assertIn("NO-DATA", line)

    def test_hostile_inputs_refused(self):
        for bad in (None, True, 123, 1.5, float("nan"), "", [], {}, b"x"):
            with self.assertRaises(ValueError):
                system_doc.check_system_doc_uptodate(bad)
            with self.assertRaises(ValueError):
                system_doc.docs_state_line(bad)


if __name__ == "__main__":
    unittest.main()
