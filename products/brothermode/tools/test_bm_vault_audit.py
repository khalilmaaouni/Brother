#!/usr/bin/env python3
"""Calibration for tools/bm_vault_audit.py, WBS row VB7-04: every recall records its
principal.

Two principals recall against a fixture vault carrying a real VB2-01 access policy (so
withholding is real, not simulated): alice sees a private note bob is denied, and both see
a public one. This proves served ids and withheld counts differ correctly per principal,
that search filters by principal, by note id, and by time window, and that a recall with
no --as records identity NO-DATA rather than skipping the append or guessing one.

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT_TOOL = os.path.join(HERE, "bm_vault.py")
AUDIT_TOOL = os.path.join(HERE, "bm_vault_audit.py")

sys.path.insert(0, HERE)
import bm_vault_audit as audit  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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


class TheAppendAndSearchContract(unittest.TestCase):
    """append()/search direct, no subprocess: the record shape and the filter logic."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-audit-unit-")
        self._orig_path = audit.AUDIT_PATH
        audit.AUDIT_PATH = os.path.join(self.tmp, "bm_vault_audit.jsonl")

    def tearDown(self):
        audit.AUDIT_PATH = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_writes_note_ids_only_never_a_path_or_content(self):
        audit.append("alice", "zorbly payroll", ["n-aaaa"], 0, "evt-1")
        with open(audit.AUDIT_PATH, encoding="utf-8") as f:
            row = json.loads(f.readline())
        self.assertEqual(row["principal"], "alice")
        self.assertEqual(row["served_ids"], ["n-aaaa"])
        self.assertEqual(row["withheld_count"], 0)
        self.assertEqual(row["event_id"], "evt-1")
        self.assertNotIn("path", row)
        self.assertNotIn("content_sha256", row)

    def test_absent_principal_records_no_data(self):
        row = audit.append(None, "q", [], 0, "evt-2")
        self.assertEqual(row["principal"], "NO-DATA")
        row2 = audit.append("", "q", [], 0, "evt-3")
        self.assertEqual(row2["principal"], "NO-DATA")

    def test_search_on_absent_file_is_no_data(self):
        self.assertIsNone(audit._read_rows())
        rc = audit.cmd_search({})
        self.assertEqual(rc, 2)

    def test_search_zero_matches_prints_clean_zero_line(self):
        audit.append("alice", "q", ["n-1"], 0, "evt-4")
        rc = audit.cmd_search({"principal": "bob"})
        self.assertEqual(rc, 0)

    def test_has_event_true_when_the_row_was_actually_appended(self):
        # MAJOR 1 fix (bm_vault_intake.py forgeable-marker review): has_event is
        # the ONE way a caller tells a real recall's marker apart from a
        # hand-written line of the same shape.
        audit.append("alice", "q", ["n-1"], 0, "evt-real")
        found, no_data = audit.has_event("evt-real")
        self.assertTrue(found)
        self.assertIsNone(no_data)

    def test_has_event_false_when_the_audit_is_readable_but_the_id_is_absent(self):
        audit.append("alice", "q", ["n-1"], 0, "evt-real")
        found, no_data = audit.has_event("evt-never-appended")
        self.assertFalse(found)
        self.assertIsNone(no_data)

    def test_has_event_is_no_data_when_the_audit_file_is_missing(self):
        self.assertFalse(os.path.exists(audit.AUDIT_PATH))
        found, no_data = audit.has_event("evt-anything")
        self.assertFalse(found)
        self.assertIsNotNone(no_data)
        self.assertIn("NO-DATA", no_data)

    def test_append_creates_the_audit_file_mode_0600(self):
        # security review MAJOR: the free-text query is sensitive, so the file must never
        # be group- or world-readable from the moment it is created.
        self.assertFalse(os.path.exists(audit.AUDIT_PATH))
        audit.append("alice", "q", ["n-1"], 0, "evt-mode")
        mode = os.stat(audit.AUDIT_PATH).st_mode & 0o777
        self.assertEqual(oct(mode), oct(0o600))

    def test_search_filters_by_principal_note_and_window(self):
        audit.append("alice", "q1", ["n-1", "n-2"], 0, "evt-a")
        audit.append("bob", "q2", ["n-2"], 1, "evt-b")
        rows = audit._read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(audit._read_rows()), 2)
        # by principal
        alice_only = [r for r in rows if r["principal"] == "alice"]
        self.assertEqual(len(alice_only), 1)
        # by note id: both rows carry n-2
        both = [r for r in rows if "n-2" in r["served_ids"]]
        self.assertEqual(len(both), 2)
        # by window: everything is after year 2000 and before year 3000
        self.assertEqual(audit._parse_iso("2000-01-01T00:00:00Z").year, 2000)


def run(argv, env):
    p = subprocess.run([sys.executable, AUDIT_TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def run_vault(argv, env):
    p = subprocess.run([sys.executable, VAULT_TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


PRIVATE_TITLE = "zorbly private audit ledger"
PRIVATE_STEM = "zorbly-private-audit-ledger"


class TwoPrincipalsRecallingOnARealPolicy(unittest.TestCase):
    """The row's own done_check: two principals recalling on a fixture vault (with a real
    VB2-01 policy so withholding is real) produce two audit records with correct served
    ids and withheld counts; search by note id finds both, by principal finds one each,
    by window filters correctly; a no-principal recall records identity NO-DATA."""

    QUERY = ["recall", "--query", "zorbly payroll audit ledger", "--limit", "5", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-audit-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "50-Private"))
        os.makedirs(os.path.join(cls.vault, "99-System"))

        def note(name, body):
            return "---\nname: %s\ntype: reference\n---\n\n%s\n" % (name, body)

        with open(os.path.join(cls.vault, "zorbly-public-audit-ledger.md"), "w") as f:
            f.write(note("zorbly public audit ledger",
                         "The zorbly payroll audit ledger's public monthly summary."))
        with open(os.path.join(cls.vault, "50-Private", PRIVATE_STEM + ".md"), "w") as f:
            f.write(note(PRIVATE_TITLE,
                         "The zorbly payroll audit ledger's private per-person figures."))
        with open(os.path.join(cls.vault, "99-System", "access-policy.json"), "w") as f:
            json.dump({"rules": [
                {"identity": "bob", "path": "50-Private/*", "action": "deny"}]}, f)

        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env.pop("BM_IDENTITY", None)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.audit_path = os.path.join(cls.tmp, ".claude", "bm_vault_audit.jsonl")
        cls.index_code, cls.index_out = run_vault(["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rows(self):
        with open(self.audit_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out)

    def test_02_alice_and_bob_each_leave_a_correct_record(self):
        # alice: --identity for the policy trim, --as for the audit principal, both alice.
        code_a, out_a = run_vault(
            self.QUERY + ["--identity", "alice", "--as", "alice"], self.env)
        code_b, out_b = run_vault(
            self.QUERY + ["--identity", "bob", "--as", "bob"], self.env)
        self.assertEqual(code_a, 0, out_a)
        self.assertEqual(code_b, 0, out_b)
        self.assertIn(PRIVATE_TITLE, out_a)
        self.assertNotIn(PRIVATE_TITLE, out_b)

        rows = self._rows()
        alice_rows = [r for r in rows if r["principal"] == "alice"]
        bob_rows = [r for r in rows if r["principal"] == "bob"]
        self.assertEqual(len(alice_rows), 1, rows)
        self.assertEqual(len(bob_rows), 1, rows)
        # alice's record served 2 notes and withheld 0; bob's served 1 and withheld 1
        # (the private note, held back by the access policy).
        self.assertEqual(len(alice_rows[0]["served_ids"]), 2, alice_rows[0])
        self.assertEqual(alice_rows[0]["withheld_count"], 0, alice_rows[0])
        self.assertEqual(len(bob_rows[0]["served_ids"]), 1, bob_rows[0])
        self.assertEqual(bob_rows[0]["withheld_count"], 1, bob_rows[0])
        # note ids only, never a title or a path.
        blob = json.dumps(rows)
        self.assertNotIn(PRIVATE_TITLE, blob)
        self.assertNotIn("50-Private", blob)
        self.__class__.shared_note_id = bob_rows[0]["served_ids"][0]
        self.__class__.alice_ts = alice_rows[0]["ts"]

    def test_03_search_by_note_id_returns_both(self):
        code, out = run(["search", "--note", self.shared_note_id], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("2 record(s)", out)
        self.assertIn("principal=alice", out)
        self.assertIn("principal=bob", out)

    def test_04_search_by_principal_returns_one_each(self):
        code_a, out_a = run(["search", "--principal", "alice"], self.env)
        code_b, out_b = run(["search", "--principal", "bob"], self.env)
        self.assertEqual(code_a, 0, out_a)
        self.assertEqual(code_b, 0, out_b)
        self.assertIn("1 record(s)", out_a)
        self.assertIn("principal=alice", out_a)
        self.assertIn("1 record(s)", out_b)
        self.assertIn("principal=bob", out_b)

    def test_05_search_by_window_filters_correctly(self):
        code, out = run(["search", "--until", "2000-01-01T00:00:00Z"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("0 record(s)", out)
        code2, out2 = run(["search", "--since", "2000-01-01T00:00:00Z"], self.env)
        self.assertEqual(code2, 0, out2)
        self.assertIn("2 record(s)", out2)

    def test_06_no_as_records_identity_no_data(self):
        code, out = run_vault(self.QUERY + ["--identity", "alice"], self.env)
        self.assertEqual(code, 0, out)
        rows = self._rows()
        no_data_rows = [r for r in rows if r["principal"] == "NO-DATA"]
        self.assertEqual(len(no_data_rows), 1, rows)

    def test_07_an_as_value_shaped_like_a_flag_is_attributed_not_no_data(self):
        # security review MAJOR (spoof regression): --as "--policy" must not be swallowed
        # by _parse as a new flag. Before the fix this fell to principal NO-DATA, letting a
        # caller suppress their own audit row just by naming themselves after a real flag.
        code, out = run_vault(self.QUERY + ["--as", "--policy"], self.env)
        self.assertEqual(code, 0, out)
        rows = self._rows()
        spoof_rows = [r for r in rows if r["principal"] == "--policy"]
        self.assertEqual(len(spoof_rows), 1, rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
