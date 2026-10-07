#!/usr/bin/env python3
"""What vault_anchor_backfill.py must keep true: it proposes only paths that
resolve in the tree, never touches a note that already declares applies_to or
is an index, writes nothing in a dry run, and on --apply adds exactly one
frontmatter line and leaves the body byte for byte. One fixture per guard."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault_anchor_backfill as B  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


BODY = "\n# t\n\nBroke in scripts/real.py and docs/ghost.md, see /abs/scripts/real.py.\n"


class Backfill(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.tree = os.path.join(self.root, "repo")
        self.vault = os.path.join(self.root, "vault")
        self.folder = os.path.join(self.vault, "40-Failures")
        _write(os.path.join(self.tree, "scripts", "real.py"), "")
        _write(os.path.join(self.tree, "scripts", "home.py"), "")
        _write(os.path.join(self.folder, "plain.md"), "---\ntype: failure\n---" + BODY)

    def run_main(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = B.main(["--vault", self.vault, "--tree", self.tree] + list(extra))
        return code, buf.getvalue()

    def test_only_paths_that_resolve_in_the_tree_are_proposed(self):
        self.assertEqual(B.propose(BODY, self.tree), ["scripts/real.py"])

    def test_a_path_named_twice_is_proposed_once(self):
        self.assertEqual(B.propose("scripts/real.py then scripts/real.py", self.tree),
                         ["scripts/real.py"])

    def test_a_dot_slash_path_is_proposed_without_it(self):
        self.assertEqual(B.propose("see ./scripts/real.py", self.tree), ["scripts/real.py"])

    def test_a_path_escaping_the_tree_is_never_proposed(self):
        _write(os.path.join(self.root, "outside", "z.py"), "")
        self.assertEqual(B.propose("see ../outside/z.py", self.tree), [])

    def test_absolute_and_home_paths_are_never_proposed(self):
        self.assertEqual(B.propose("see ~/scripts/home.py and /x/scripts/home.py",
                                   self.tree), [])

    def test_a_note_that_declares_applies_to_is_skipped(self):
        _write(os.path.join(self.folder, "plain.md"),
               "---\ntype: failure\napplies_to: []\n---" + BODY)
        self.assertEqual(B.scan(self.vault, self.tree), [("plain", "has applies_to", [])])

    def test_an_index_note_is_skipped(self):
        _write(os.path.join(self.folder, "plain.md"), "---\ntype: index\n---" + BODY)
        self.assertEqual(B.scan(self.vault, self.tree), [("plain", "index note", [])])

    def test_the_failures_index_file_is_not_scanned(self):
        _write(os.path.join(self.folder, "Failures-Index.md"), "---\ntype: failure\n---" + BODY)
        self.assertEqual([r[0] for r in B.scan(self.vault, self.tree)], ["plain"])

    def test_a_note_without_frontmatter_is_reported_never_written(self):
        _write(os.path.join(self.folder, "plain.md"), BODY)
        code, _ = self.run_main("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(_read(os.path.join(self.folder, "plain.md")), BODY)
        self.assertEqual(B.scan(self.vault, self.tree), [("plain", "no frontmatter", [])])

    def test_dry_run_prints_the_table_and_writes_nothing(self):
        before = _read(os.path.join(self.folder, "plain.md"))
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("plain | scripts/real.py", out)
        self.assertIn("notes scanned: 1; with a proposal: 1 (anchors proposed: 1)", out)
        self.assertIn("DRY RUN", out)
        self.assertEqual(_read(os.path.join(self.folder, "plain.md")), before)

    def test_apply_adds_only_the_frontmatter_line(self):
        code, out = self.run_main("--apply")
        self.assertEqual(code, 0)
        self.assertIn("APPLIED to 1 note(s)", out)
        self.assertEqual(_read(os.path.join(self.folder, "plain.md")),
                         "---\ntype: failure\napplies_to: [scripts/real.py]\n---" + BODY)

    def test_apply_refuses_a_note_that_gained_applies_to_since_the_scan(self):
        path = os.path.join(self.folder, "plain.md")
        _write(path, "---\ntype: failure\napplies_to: [x]\n---" + BODY)
        self.assertEqual(B.apply_one(path, ["scripts/real.py"]), "changed since the scan")
        self.assertIn("applies_to: [x]\n", _read(path))

    def test_apply_keeps_crlf_and_every_other_byte(self):
        path = os.path.join(self.folder, "plain.md")
        crlf = ("---\ntype: failure\n---" + BODY).replace("\n", "\r\n").encode()
        with open(path, "wb") as fh:
            fh.write(crlf)
        self.assertIsNone(B.apply_one(path, ["scripts/real.py"]))
        with open(path, "rb") as fh:
            got = fh.read()
        self.assertEqual(got, crlf.replace(b"\r\n---\r\n# t",
                                           b"\r\napplies_to: [scripts/real.py]\r\n---\r\n# t"))
        self.assertNotIn(b"\n\n", got.replace(b"\r\n", b""))  # no bare LF slipped in

    def test_apply_leaves_no_temp_file_behind(self):
        self.run_main("--apply")
        self.assertEqual(sorted(os.listdir(self.folder)), ["plain.md"])

    def test_a_non_utf8_note_is_skipped_and_named_never_a_crash(self):
        path = os.path.join(self.folder, "bad.md")
        with open(path, "wb") as fh:
            fh.write(b"---\ntype: failure\n---\nscripts/real.py \xff\n")
        code, out = self.run_main("--apply")
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED bad: unreadable or not UTF-8", out)
        self.assertIn("unreadable or not UTF-8: 1", out)
        with open(path, "rb") as fh:
            self.assertTrue(fh.read().endswith(b"\xff\n"))
        self.assertEqual(B.apply_one(path, ["scripts/real.py"]),
                         "unreadable or not UTF-8 (UnicodeDecodeError)")

    def test_a_missing_folder_is_NO_DATA_exit_2(self):
        shutil.rmtree(self.folder)
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_a_missing_tree_is_NO_DATA_exit_2(self):
        shutil.rmtree(self.tree)
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
