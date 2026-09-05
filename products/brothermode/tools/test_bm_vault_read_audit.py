#!/usr/bin/env python3
"""Calibration for tools/bm_vault_read_audit.py, the V5 immutable read-audit trail.

WHY THIS SUITE EXISTS. The chain's whole point is to PROVE tampering, not merely to
avoid it, so the cases that matter most are the ones where someone edits the log
after the fact: a field changed in place, a line removed outright. A suite that only
checks the happy path (three reads, verify OK) would never catch a chain whose
"tamper detection" is decorative -- fields present but never actually recomputed
and compared. Every write below uses tempfile only; nothing here touches the real
vault or ~/.claude.
"""
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "bm_vault_read_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("bm_vault_read_audit_under_test",
                                                   MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ra = load_module()


def _log_path(vault_dir):
    return os.path.join(vault_dir, "bm_vault_read_audit.jsonl")


def _rows(log_path):
    with open(log_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class ThreeReadsChainAndVerify(unittest.TestCase):
    def test_three_reads_produce_three_chained_lines_and_verify_exits_0(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_") as vault_dir:
            log_path = _log_path(vault_dir)
            ra.record_read(note="note-a.md", surface="bm_vault_recall",
                           session="sess-1", query="what broke last time", path=log_path)
            ra.record_read(note="note-b.md", surface="recall_hook",
                           session="sess-1", query="tools/foo.py", path=log_path)
            ra.record_read(note="note-c.md", surface="bm_vault_recall",
                           session="sess-2", query="another symptom", path=log_path)

            rows = _rows(log_path)
            self.assertEqual(3, len(rows))
            # Chained: each row's prev_hash is the one before it's hash; the first is genesis.
            self.assertEqual(ra.GENESIS_HASH, rows[0]["prev_hash"])
            self.assertEqual(rows[0]["hash"], rows[1]["prev_hash"])
            self.assertEqual(rows[1]["hash"], rows[2]["prev_hash"])
            # The query text itself never appears verbatim, only its sha256.
            for row in rows:
                self.assertNotIn("query", row)
                self.assertEqual(64, len(row["query_sha256"]))

            out = io.StringIO()
            with redirect_stdout(out):
                code = ra.cmd_verify([vault_dir])
            self.assertEqual(0, code)
            self.assertIn("3 read event", out.getvalue())


class TamperedLineBreaksVerify(unittest.TestCase):
    def test_editing_a_middle_lines_note_id_makes_verify_exit_1_naming_that_line(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_tamper_") as vault_dir:
            log_path = _log_path(vault_dir)
            for i in range(3):
                ra.record_read(note="note-%d.md" % i, surface="bm_vault_recall",
                               query="q%d" % i, path=log_path)
            rows = _rows(log_path)
            rows[1]["note"] = "an-attacker-substituted-note.md"   # hash left stale on purpose
            with open(log_path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, sort_keys=True) + "\n")

            out = io.StringIO()
            with redirect_stdout(out):
                code = ra.cmd_verify([vault_dir])
            self.assertEqual(1, code)
            self.assertIn("line 2", out.getvalue())


class DeletedLineBreaksVerify(unittest.TestCase):
    def test_deleting_a_middle_line_makes_verify_exit_1(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_delete_") as vault_dir:
            log_path = _log_path(vault_dir)
            for i in range(3):
                ra.record_read(note="note-%d.md" % i, surface="bm_vault_recall",
                               query="q%d" % i, path=log_path)
            rows = _rows(log_path)
            del rows[1]
            with open(log_path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, sort_keys=True) + "\n")

            out = io.StringIO()
            with redirect_stdout(out):
                code = ra.cmd_verify([vault_dir])
            self.assertEqual(1, code)
            self.assertIn("line 2", out.getvalue())
            self.assertIn("prev_hash", out.getvalue())


class EmptyVaultIsNoData(unittest.TestCase):
    def test_empty_vault_gives_verify_exit_2(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_empty_") as vault_dir:
            out = io.StringIO()
            with redirect_stdout(out):
                code = ra.cmd_verify([vault_dir])
            self.assertEqual(2, code)
            self.assertIn("NO-DATA", out.getvalue())


class UnwritableRootDegrades(unittest.TestCase):
    def test_unwritable_audit_root_leaves_the_read_working_and_prints_no_data(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_locked_") as vault_dir:
            os.chmod(vault_dir, stat.S_IRUSR | stat.S_IXUSR)   # read+execute, no write
            log_path = _log_path(vault_dir)
            try:
                err = io.StringIO()
                with redirect_stderr(err):
                    # The call itself must not raise: a read audit that can break the
                    # read it is auditing would be worse than no audit.
                    ra.record_read(note="note-x.md", surface="bm_vault_recall",
                                   query="q", path=log_path)
                self.assertIn("NO-DATA", err.getvalue())
                self.assertFalse(os.path.exists(log_path))
            finally:
                os.chmod(vault_dir, stat.S_IRWXU)   # restore so TemporaryDirectory can clean up


class WhoReadListsTheRightEvents(unittest.TestCase):
    def test_who_read_lists_only_events_for_the_named_note(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_whoread_") as vault_dir:
            log_path = _log_path(vault_dir)
            ra.record_read(note="note-a.md", surface="bm_vault_recall",
                           session="sess-1", query="q1", path=log_path)
            ra.record_read(note="note-b.md", surface="recall_hook",
                           session="sess-1", query="q2", path=log_path)
            ra.record_read(note="note-a.md", surface="bm_vault_recall",
                           session="sess-2", query="q3", path=log_path)

            out = io.StringIO()
            with redirect_stdout(out):
                code = ra.cmd_who_read(["note-a.md", "--root", vault_dir])
            self.assertEqual(0, code)
            text = out.getvalue()
            self.assertIn("2 record(s)", text)
            self.assertIn("sess-1", text)
            self.assertIn("sess-2", text)
            self.assertNotIn("note-b.md", text)

    def test_who_read_on_a_note_never_seen_returns_zero_records(self):
        with tempfile.TemporaryDirectory(prefix="bm_read_audit_whoread_zero_") as vault_dir:
            log_path = _log_path(vault_dir)
            ra.record_read(note="note-a.md", surface="bm_vault_recall",
                           query="q1", path=log_path)
            out = io.StringIO()
            with redirect_stdout(out):
                code = ra.cmd_who_read(["nope.md", "--root", vault_dir])
            self.assertEqual(0, code)
            self.assertIn("0 record(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
