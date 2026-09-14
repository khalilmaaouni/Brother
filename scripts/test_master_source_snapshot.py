import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "master_source_snapshot.py")

sys.path.insert(0, HERE)
import master_source_snapshot as mss  # noqa: E402

WORKBOOK_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
    <sheet name="Sheet2" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>
"""

RELS_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>
"""


def sheet_xml(row_count):
    rows = "".join(
        '<row r="%d"><c r="A%d" t="s"><v>0</v></c><c r="B%d" t="s"><v>1</v></c></row>' % (i, i, i)
        for i in range(1, row_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:B%d"/><sheetData>%s</sheetData></worksheet>' % (row_count, rows)
    ).encode("utf-8")


SHARED_STRINGS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="2">'
    '<si><t>x</t></si><si><t>y</t></si></sst>'
).encode("utf-8")


def make_fixture_xlsx(path, sheet1_rows=3, sheet2_rows=5, include_shared_strings=True):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", WORKBOOK_XML)
        zf.writestr("xl/_rels/workbook.xml.rels", RELS_XML)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml(sheet1_rows))
        zf.writestr("xl/worksheets/sheet2.xml", sheet_xml(sheet2_rows))
        if include_shared_strings:
            zf.writestr("xl/sharedStrings.xml", SHARED_STRINGS_XML)


class BuildSnapshotTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_fixture_passes_with_correct_row_counts_and_hash(self):
        path = os.path.join(self.tmpdir, "generic-source.xlsx")
        make_fixture_xlsx(path, sheet1_rows=3, sheet2_rows=5)
        record = mss.build_snapshot(path, source_system_id="test-system")

        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(record["row_counts"], {"Sheet1": 3, "Sheet2": 5})
        self.assertEqual(record["schema_version"], "sheets:2")
        self.assertEqual(record["source_system_identifier"], "test-system")
        self.assertEqual(record["shared_strings"], {"count": "4", "unique_count": "2"})

        with open(path, "rb") as handle:
            expected_hash = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(record["fingerprint"], expected_hash)
        self.assertEqual(record["file_size_bytes"], os.path.getsize(path))

    def test_missing_file_is_no_data(self):
        record = mss.build_snapshot(os.path.join(self.tmpdir, "does-not-exist.xlsx"))
        self.assertEqual(record["verdict"], "NO-DATA")
        self.assertEqual(record["row_counts"], {})
        self.assertEqual(record["fingerprint"], "NO-DATA")

    def test_corrupt_non_zip_file_is_fail(self):
        path = os.path.join(self.tmpdir, "corrupt.xlsx")
        with open(path, "wb") as handle:
            handle.write(b"this is not a zip file at all")
        record = mss.build_snapshot(path)
        self.assertEqual(record["verdict"], "FAIL")
        self.assertIn("BadZipFile", record["verdict_reason"])

    def test_zip_without_workbook_xml_is_fail(self):
        path = os.path.join(self.tmpdir, "not-really-xlsx.xlsx")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "not an office document")
        record = mss.build_snapshot(path)
        self.assertEqual(record["verdict"], "FAIL")

    def test_notes_pass_through_verbatim_and_default_empty(self):
        path = os.path.join(self.tmpdir, "generic-source.xlsx")
        make_fixture_xlsx(path)
        empty = mss.build_snapshot(path)
        self.assertEqual(empty["quality_observations"], [])

        noted = mss.build_snapshot(path, notes=["placeholder observation x"])
        self.assertEqual(noted["quality_observations"], ["placeholder observation x"])

    def test_fixed_boundary_strings(self):
        path = os.path.join(self.tmpdir, "generic-source.xlsx")
        make_fixture_xlsx(path)
        record = mss.build_snapshot(path)
        self.assertEqual(record["access_boundary"], "private, vault-only, never public")
        self.assertIn("approved environment", record["data_retention_restriction"])

    def test_never_reads_shared_string_text_content(self):
        # The record must carry only the sst count attributes, never the <t> text.
        path = os.path.join(self.tmpdir, "generic-source.xlsx")
        make_fixture_xlsx(path)
        record = mss.build_snapshot(path)
        dumped = json.dumps(record)
        self.assertNotIn(">x<", dumped)
        self.assertNotIn(">y<", dumped)


class CLITests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT] + list(args),
            capture_output=True,
            text=True,
        )

    def test_cli_exit_code_pass(self):
        path = os.path.join(self.tmpdir, "generic-source.xlsx")
        make_fixture_xlsx(path)
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 0)
        record = json.loads(result.stdout)
        self.assertEqual(record["verdict"], "PASS")

    def test_cli_exit_code_no_data(self):
        result = self.run_cli(os.path.join(self.tmpdir, "missing.xlsx"))
        self.assertEqual(result.returncode, 2)
        record = json.loads(result.stdout)
        self.assertEqual(record["verdict"], "NO-DATA")

    def test_cli_exit_code_fail(self):
        path = os.path.join(self.tmpdir, "corrupt.xlsx")
        with open(path, "wb") as handle:
            handle.write(b"garbage")
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 1)
        record = json.loads(result.stdout)
        self.assertEqual(record["verdict"], "FAIL")

    def test_cli_out_flag_writes_file(self):
        path = os.path.join(self.tmpdir, "generic-source.xlsx")
        make_fixture_xlsx(path)
        out_path = os.path.join(self.tmpdir, "record.json")
        result = self.run_cli(path, "--out", out_path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        with open(out_path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["verdict"], "PASS")

    def test_tool_never_imports_network_modules(self):
        with open(SCRIPT, "r", encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("import requests", "import urllib", "import http.client", "import socket"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
