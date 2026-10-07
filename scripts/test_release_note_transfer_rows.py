"""T1.1: the transfer ledger is read, never typed, and a corrupt one refuses the note.

Every assertion below is taken from sub unit T1.1's own specification, never
from what the code happens to do. transfer_rows and transfer_paragraph are
pure functions of their arguments, so they are driven directly against temp
directories; the build() tests drive the call site with every step that reads
the real tree or spawns a subprocess replaced, so the transfer ledger is the
only variable.

The module under test is loaded by file path, so this file carries no import
of a module that runs commands, and no import of subprocess or shutil.
"""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "release_note_from_tree.py")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "release_note_from_tree_t11_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if os.path.isfile(MODULE_PATH):
    R = _load_module()
else:
    R = None

GREEN = {"ok": True, "n": 3, "ran": 3, "skipped": 0, "nodata_skip": None,
         "tail": "OK"}
TABLE = [("claim", "scripts/release_note_from_tree.py", None)]
ROW_FILE = "scripts/release_note_from_tree.py"


@contextlib.contextmanager
def ledger_file(text):
    with tempfile.TemporaryDirectory(prefix="t11-") as d:
        p = os.path.join(d, "transfer.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        yield p


@contextlib.contextmanager
def stubbed_build(transfer_rows_result=None, transfer_rows_raises=False):
    """R.build() with every step that reads the real tree or spawns a
    subprocess replaced. Only the ledger read is left as the variable, and
    that is patched too so each test can name exactly what it feeds."""

    def fake_run_suite(rel_path):
        return dict(GREEN)

    def fake_confirm(rel_path, names):
        return list(names)

    def fake_table(claim_suites, perturb=None):
        return list(TABLE), None

    def fake_transfer_rows(path):
        if transfer_rows_raises:
            raise ValueError(
                "NO-DATA: %s carries a row the marker cannot parse" % path)
        return list(transfer_rows_result or [])

    patches = [
        mock.patch.object(R, "run_suite", fake_run_suite),
        mock.patch.object(R, "confirm_test_names", fake_confirm),
        mock.patch.object(R, "measured_file_rows", fake_table),
        mock.patch.object(R, "head_rev", lambda: "deadbeefcafe0"),
        mock.patch.object(R, "head_describe", lambda: "v1.0.0"),
        mock.patch.object(R, "manifest_version", lambda: "1.0.0"),
        mock.patch.object(R, "shims_count", lambda: 15),
        mock.patch.object(R, "export_manifest",
                          lambda: ("a  b\n", "c" * 64, 1, None)),
        mock.patch.object(R, "published_as_line",
                          lambda path=None, version=None:
                          "Published as tag v1.0.0 on example.com/x/y."),
        mock.patch.object(R, "extra_notes",
                          lambda version, releases_dir=None: ""),
        mock.patch.object(R, "previous_release_line",
                          lambda v, releases_dir=None: ""),
        mock.patch.object(R, "previous_public_tag",
                          lambda v, releases_dir=None: None),
        mock.patch.object(R, "transfer_rows", fake_transfer_rows),
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


class TransferRowsReadsTheFile(unittest.TestCase):
    """RQ-01 and RQ-02: rows come out of the ledger, in file order, and a
    ledger the reader cannot parse is refused with a ValueError."""

    def test_rows_are_read_in_file_order_and_comments_ignored(self):
        with ledger_file("- task: T-101 | scripts/b.py\n"
                         "# a comment\n"
                         "\n"
                         "- task: T-100 | scripts/a.py\n") as p:
            rows = R.transfer_rows(p)
        self.assertEqual(rows, [("T-101", "scripts/b.py"),
                                ("T-100", "scripts/a.py")])

    def test_an_absent_ledger_is_an_empty_list_not_a_refusal(self):
        with tempfile.TemporaryDirectory(prefix="t11-") as d:
            missing = os.path.join(d, "no-such-ledger.md")
            self.assertEqual(R.transfer_rows(missing), [])

    def test_an_empty_ledger_yields_an_empty_list(self):
        with ledger_file("# nothing here\n\n") as p:
            self.assertEqual(R.transfer_rows(p), [])

    def test_a_corrupt_ledger_is_refused(self):
        with ledger_file("- task: T-1 has no separator\n") as p:
            with self.assertRaises(ValueError):
                R.transfer_rows(p)

    def test_a_marked_row_with_an_empty_field_is_refused(self):
        with ledger_file("- task:  | scripts/a.py\n") as p:
            with self.assertRaises(ValueError):
                R.transfer_rows(p)

    def test_a_ledger_over_the_size_cap_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="t11-") as d:
            p = os.path.join(d, "transfer.md")
            with open(p, "wb") as fh:
                fh.write(b"#" * (R.MAX_LEDGER_BYTES + 1))
            with self.assertRaises(ValueError):
                R.transfer_rows(p)

    def test_bytes_that_are_not_utf8_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="t11-") as d:
            p = os.path.join(d, "transfer.md")
            with open(p, "wb") as fh:
                fh.write(b"- task: T-1 | \xff\xfe no\n")
            with self.assertRaises(ValueError):
                R.transfer_rows(p)

    def test_a_directory_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="t11-") as d:
            sub = os.path.join(d, "a-directory")
            os.mkdir(sub)
            with self.assertRaises(ValueError):
                R.transfer_rows(sub)

    def test_hostile_paths_are_refused_with_valueerror_never_a_crash(self):
        for bad in (None, 1234, 3.5, float("nan"), b"/tmp/x", True,
                    ["a"], {"a": 1}, (1, 2)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    R.transfer_rows(bad)


class TransferParagraphFromRows(unittest.TestCase):
    """RQ-03: an empty ledger drops the sentence entirely, so the note never
    invents a count."""

    def test_an_empty_ledger_drops_the_sentence(self):
        self.assertIsNone(R.transfer_paragraph([], "1.0.0"))
        self.assertIsNone(R.transfer_paragraph((), "1.0.0"))

    def test_a_non_empty_ledger_names_its_measured_count(self):
        rows = [("T-1", "scripts/a.py"), ("T-2", "scripts/b.py")]
        sentence = R.transfer_paragraph(rows, "1.0.0")
        self.assertIsNotNone(sentence)
        self.assertIn("2 open task(s)", sentence)
        self.assertIn(R.HANDOVER_DIR_REL, sentence)
        self.assertIn("1.0.0", sentence)

    def test_hostile_rows_are_refused_with_valueerror_never_a_crash(self):
        for bad in (None, "not a list", 5, {"a": 1}, float("nan"),
                    [("only-one",)], [("a", 1)], [("a", "b", "c")],
                    [None], [(b"a", "b")]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    R.transfer_paragraph(bad, "1.0.0")

    def test_a_hostile_version_is_refused(self):
        rows = [("T-1", "scripts/a.py")]
        for bad in (None, "", 5, b"1.0.0", ["1.0.0"], float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    R.transfer_paragraph(rows, bad)


class BuildAsksTheLedgerForItsRows(unittest.TestCase):
    """RQ-01, RQ-02 and RQ-03 at the call site: build() asks transfer_rows
    for the rows, refuses the whole note when the reader raises, and drops
    the sentence when the ledger is absent. No literal list in this source
    can satisfy these, because the count asserted is the ledger's own."""

    def test_rows_come_from_the_file_not_the_source(self):
        rows = [("T-%d" % i, ROW_FILE) for i in range(1, 6)]
        with stubbed_build(transfer_rows_result=rows):
            body, problems = R.build("1.0.0")
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        self.assertIn("5 open task(s)", body)

    def test_a_corrupt_ledger_refuses_the_whole_note(self):
        with stubbed_build(transfer_rows_raises=True):
            body, problems = R.build("1.0.0")
        self.assertIsNone(body)
        self.assertTrue(problems)
        self.assertTrue(all(p.startswith(R.NODATA) for p in problems),
                        problems)
        self.assertTrue(any("cannot parse" in p for p in problems), problems)

    def test_an_absent_ledger_drops_the_sentence_entirely(self):
        with stubbed_build(transfer_rows_result=[]):
            body, problems = R.build("1.0.0")
        self.assertEqual(problems, [], problems)
        self.assertIsNotNone(body)
        self.assertNotIn("open task(s)", body)

    def test_a_row_whose_owning_path_is_gone_is_dropped_and_never_counted(self):
        rows = [("T-1", ROW_FILE), ("T-2", "no/such/owning/path.py")]
        with stubbed_build(transfer_rows_result=rows):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                body, problems = R.build("1.0.0")
        self.assertEqual(problems, [], problems)
        self.assertIn("1 open task(s)", body)
        self.assertNotIn("2 open task(s)", body)
        self.assertIn("T-2", err.getvalue())


if __name__ == "__main__":
    unittest.main()
