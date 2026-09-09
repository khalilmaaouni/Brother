"""What the receipt checker must keep true.

A rendered UNVERIFIED pill is only honest if this checker actually looked
for the thing the receipt claims to name, rather than trusting the string.
These tests try to fool it: a receipt naming a file that does not exist, a
range past the end of a real file, an evidence name nothing captured, a url
with no captured copy.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import receipt_check as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FileReceipts(unittest.TestCase):
    def test_a_real_file_and_range_resolves(self):
        status, reason = R.resolve_receipt("file:scripts/decide.py:1-5", ROOT, "/no/such/home")
        self.assertEqual(status, R.RESOLVED)
        self.assertIn("scripts/decide.py", reason)

    def test_a_missing_file_is_unverified(self):
        status, _reason = R.resolve_receipt("file:scripts/nope_nope.py:1-5", ROOT, "/no/such/home")
        self.assertEqual(status, R.UNVERIFIED)

    def test_a_range_past_the_end_of_the_file_is_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as fh:
                fh.write("one\ntwo\nthree\n")
            status, reason = R.resolve_receipt("file:x.py:1-999", d, "/no/such/home")
            self.assertEqual(status, R.UNVERIFIED)
            self.assertIn("does not fit", reason)

    def test_a_malformed_range_is_unverified(self):
        status, _r = R.resolve_receipt("file:scripts/decide.py:notarange", ROOT, "/no/such/home")
        self.assertEqual(status, R.UNVERIFIED)

    def test_a_malformed_file_receipt_with_no_colon_is_unverified(self):
        status, reason = R.resolve_receipt("file:nocolonhere", ROOT, "/no/such/home")
        self.assertEqual(status, R.UNVERIFIED)
        self.assertIn("malformed", reason)


class EvidenceReceipts(unittest.TestCase):
    def test_an_existing_evidence_file_resolves(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude", "evidence"))
            with open(os.path.join(home, ".claude", "evidence", "run1.txt"), "w") as fh:
                fh.write("ok")
            status, _r = R.resolve_receipt("evidence:run1.txt", ROOT, home)
            self.assertEqual(status, R.RESOLVED)

    def test_a_missing_evidence_file_is_unverified(self):
        with tempfile.TemporaryDirectory() as home:
            status, _r = R.resolve_receipt("evidence:nope.txt", ROOT, home)
            self.assertEqual(status, R.UNVERIFIED)


class UrlReceipts(unittest.TestCase):
    def test_a_captured_url_resolves(self):
        url = "https://example.com/a"
        sha = hashlib.sha1(url.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude", "evidence"))
            with open(os.path.join(home, ".claude", "evidence", sha + ".txt"), "w") as fh:
                fh.write("captured")
            status, _r = R.resolve_receipt("url:%s" % url, ROOT, home)
            self.assertEqual(status, R.RESOLVED)

    def test_an_uncaptured_url_is_unverified(self):
        with tempfile.TemporaryDirectory() as home:
            status, _r = R.resolve_receipt("url:https://example.com/nope", ROOT, home)
            self.assertEqual(status, R.UNVERIFIED)


class MissingOrUnknownReceipts(unittest.TestCase):
    def test_a_missing_receipt_is_unverified(self):
        status, reason = R.resolve_receipt("", ROOT, "/no/such/home")
        self.assertEqual(status, R.UNVERIFIED)
        self.assertIn("no receipt", reason)

    def test_none_is_unverified(self):
        status, _r = R.resolve_receipt(None, ROOT, "/no/such/home")
        self.assertEqual(status, R.UNVERIFIED)

    def test_an_unknown_scheme_is_unverified(self):
        status, reason = R.resolve_receipt("carrier-pigeon:x", ROOT, "/no/such/home")
        self.assertEqual(status, R.UNVERIFIED)
        self.assertIn("unrecognised", reason)


class TheCLIExitsCorrectly(unittest.TestCase):
    def record(self, **over):
        rec = {"options": [{"id": "A", "sources": [{"what": "x", "receipt": "file:scripts/decide.py:1-2"}]}]}
        rec.update(over)
        return rec

    def write(self, tmp, record):
        path = os.path.join(tmp, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path

    def test_all_resolved_exits_0(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, self.record())
            self.assertEqual(R.main([path]), 0)

    def test_any_unverified_exits_1(self):
        rec = self.record(options=[{"id": "A", "sources": [{"what": "x", "receipt": ""}]}])
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, rec)
            self.assertEqual(R.main([path]), 1)

    def test_no_source_anywhere_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, {"options": [{"id": "A", "sources": []}]})
            self.assertEqual(R.main([path]), 2)

    def test_an_unreadable_record_is_NO_DATA(self):
        self.assertEqual(R.main(["/no/such/record.json"]), 2)

    def test_json_flag_prints_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, self.record())
            out = os.path.join(d, "out.txt")
            # capture stdout via redirect
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = R.main([path, "--json"])
            self.assertEqual(code, 0)
            rows = json.loads(buf.getvalue())
            self.assertEqual(rows[0]["status"], R.RESOLVED)


class ContractShapedRecords(unittest.TestCase):
    """The outcome-contract-v1 shape (docs/schema/outcome-contract-v1.json):
    a top-level receipts[] list of {id, path, verdict, ref}, no options key
    anywhere. Same resolver, same exit semantics, different source of rows."""

    def write(self, tmp, record):
        path = os.path.join(tmp, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path

    def run_cli(self, argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = R.main(argv)
        return code, buf.getvalue()

    def test_a_resolvable_contract_receipt_exits_0(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as fh:
                fh.write("one\ntwo\nthree\n")
            rec = {"receipts": [{"id": "r1", "path": "x.py lines 1-2",
                                  "verdict": "PASS", "ref": "file:x.py:1-2"}]}
            path = self.write(d, rec)
            old_root = R.ROOT
            R.ROOT = d
            try:
                code, out = self.run_cli([path])
            finally:
                R.ROOT = old_root
            self.assertEqual(code, 0)
            self.assertIn(R.RESOLVED, out)

    def test_a_contract_receipt_naming_a_missing_file_exits_1(self):
        rec = {"receipts": [{"id": "r1", "path": "nowhere",
                              "verdict": "PASS", "ref": "file:scripts/nope_nope.py:1-2"}]}
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, rec)
            code, out = self.run_cli([path])
            self.assertEqual(code, 1)
            self.assertIn(R.UNVERIFIED, out)

    def test_an_empty_receipts_list_is_NO_DATA(self):
        rec = {"schema_version": "outcome-contract-v1", "receipts": []}
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, rec)
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                code = R.main([path])
            self.assertEqual(code, 2)
            self.assertIn("empty receipts list", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
