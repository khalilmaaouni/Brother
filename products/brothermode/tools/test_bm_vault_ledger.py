#!/usr/bin/env python3
"""Tests for the answer ledger (VB2-05): bm_vault.py's writer, bm_vault_ledger.py's reader.

A one-note synthetic vault is indexed once; every recall in this suite runs --fast (skips
the 30-75 second dense embedder load, irrelevant to what the ledger records) against it.
Load-bearing behaviors proven here:
  - a recall appends exactly one ledger row, whose hits match what the recall printed
  - show/census render that row honestly
  - replay re-hashes each hit's CURRENT source and reports MATCHES, then CHANGED SINCE
    once the fixture note is edited on disk (never against a stale index row)
  - a ledger write failure (unwritable directory) prints a warning but never blocks the
    recall it is auditing (availability over bookkeeping, per bm_vault.py's own docstring)
  - show/replay/census all report NO-DATA, never a pass, on an absent ledger

VB6-03 (immutable answer-event ids join the ledger and the telemetry), proven below in
EventIdTest:
  - a recall mints one event id, printed on stdout, stamped into both the ledger row and
    the access-audit row of that same answer
  - an answer served twice under the SAME --event-id appends to the ledger once (skipped,
    counted, reported on stderr), the dedup check keyed on event_id alone
  - a telemetry outcome (bm_vault_ledger.py outcome) joins back to its ledger row (join)
    by event_id alone; a synthetic clock skew between the two files' `ts` fields changes
    nothing about whether the join resolves

Run: python3 tools/test_bm_vault_ledger.py      (unittest output, exit 0 or 1)
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

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

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT_TOOL = os.path.join(HERE, "bm_vault.py")
LEDGER_TOOL = os.path.join(HERE, "bm_vault_ledger.py")

NOTE = """---
name: zorbtangle-overflow-fix
description: how the zorbtangle counter overflow was fixed
---
The zorbtangle counter overflow lesson: always clamp before increment.
"""

QUERY = "zorbtangle counter overflow"
ID_RE = re.compile(r"id: (n-[0-9a-f]{16}|none)")
EVENT_RE = re.compile(r"^event: ([0-9a-f]{32})$", re.M)


def run(tool, argv, env):
    p = subprocess.run([sys.executable, tool] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


class LedgerTest(unittest.TestCase):
    """Numbered on purpose: test_05 edits the fixture note on disk, so every case that
    reads the note's original content must run before it. unittest orders methods
    alphabetically, not by source position, hence the numbering."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_ledger_")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        cls.note_path = os.path.join(cls.vault, "zorbtangle.md")
        with open(cls.note_path, "w") as f:
            f.write(NOTE)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp                   # moves INDEX_PATH and the ledger path
        cls.env["BM_VAULT_ROOT"] = cls.vault
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.ledger_path = os.path.join(cls.tmp, ".claude", "bm_vault_answers.jsonl")
        code, out = run(VAULT_TOOL, ["index", "--vault", cls.vault], cls.env)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rows(self):
        with open(self.ledger_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_01_a_recall_appends_exactly_one_row_matching_its_own_printed_hits(self):
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast", "--limit", "3"],
                        self.env)
        self.assertEqual(code, 0, out)
        printed_ids = set(ID_RE.findall(out))
        self.assertTrue(printed_ids, "fixture recall printed no hit id at all:\n%s" % out)
        rows = self._rows()
        self.assertEqual(1, len(rows), "expected exactly one ledger row: %r" % rows)
        row = rows[0]
        self.assertEqual(QUERY, row["query"])
        self.assertEqual("unset", row["identity"], "BM_IDENTITY was not set for this call")
        self.assertEqual("fast", row["mode"])
        self.assertTrue(row["hits"], "row recorded zero hits though recall printed some")
        ledger_ids = {h["id"] for h in row["hits"]}
        self.assertEqual(printed_ids, ledger_ids,
                         "ledger hit ids do not match what the recall actually printed")
        for h in row["hits"]:
            self.assertEqual(self.note_path, h["path"])
            self.assertEqual(64, len(h["content_sha256"]), "not a sha256 hex digest")

    def test_01a_ledger_file_created_mode_0600(self):
        # security review MAJOR: the row carries the verbatim query text, the same
        # sensitive field bm_vault_audit.py's own mode-0600 test guards (see
        # test_bm_vault_audit.py's test_append_creates_the_audit_file_mode_0600) --
        # the ledger sat beside that file holding the identical field and had never
        # been backfilled to match, until this fix.
        mode = os.stat(self.ledger_path).st_mode & 0o777
        self.assertEqual(oct(mode), oct(0o600))

    def test_02_show_last_1_renders_that_row(self):
        code, out = run(LEDGER_TOOL, ["show", "--last", "1"], self.env)
        self.assertEqual(0, code, out)
        self.assertIn(QUERY, out)
        self.assertIn(self.note_path, out)
        self.assertIn("identity=unset", out)
        self.assertIn("mode=fast", out)

    def test_03_census_reports_one_row_and_the_identity_seen(self):
        code, out = run(LEDGER_TOOL, ["census"], self.env)
        self.assertEqual(0, code, out)
        self.assertIn("rows: 1", out)
        self.assertIn("unset", out)

    def test_04_a_second_recall_carries_the_identity_it_was_given(self):
        env2 = dict(self.env)
        env2["BM_IDENTITY"] = "cursor-agent-7"
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast"], env2)
        self.assertEqual(0, code, out)
        rows = self._rows()
        self.assertEqual(2, len(rows), rows)
        self.assertEqual("cursor-agent-7", rows[-1]["identity"])

    def test_05_replay_matches_then_changed_since_after_editing_the_source(self):
        code, out = run(LEDGER_TOOL, ["replay", "--ts", "0"], self.env)
        self.assertEqual(0, code, out)
        self.assertIn("MATCHES", out)
        self.assertNotIn("CHANGED SINCE", out)
        self.assertNotIn("GONE", out)

        with open(self.note_path, "a") as f:
            f.write("\nAppended after the recall that this row's hit hash was taken from.\n")

        code, out = run(LEDGER_TOOL, ["replay", "--ts", "0"], self.env)
        self.assertEqual(1, code, out)   # findings: a source changed
        self.assertIn("CHANGED SINCE", out)
        self.assertIn("1 of 1 source(s) changed", out)

    def test_06_replay_reports_gone_once_the_source_is_deleted(self):
        os.remove(self.note_path)
        try:
            code, out = run(LEDGER_TOOL, ["replay", "--ts", "0"], self.env)
            self.assertEqual(1, code, out)
            self.assertIn("GONE", out)
        finally:
            # test_04's recall (row index 1) is read by no later test, but restore the
            # fixture anyway: least surprise for anyone adding a case after this one.
            with open(self.note_path, "w") as f:
                f.write(NOTE)

    def test_07_replay_reports_no_data_for_an_unresolvable_ts(self):
        code, out = run(LEDGER_TOOL, ["replay", "--ts", "not-a-real-timestamp"], self.env)
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)


class LedgerWriteFailureTest(unittest.TestCase):
    """Own fixture, own tmp home: this suite chmods the ledger's directory read-only, so
    it must never share state (or a teardown ordering) with LedgerTest above."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_ledger_fail_")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        with open(os.path.join(cls.vault, "note.md"), "w") as f:
            f.write(NOTE)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        # A separate, writable freshness-state db: only the LEDGER's directory (HOME/.claude)
        # is made read-only below, and freshness revalidation commits to its own db in that
        # same directory by default -- pinning it elsewhere isolates the one failure this
        # test means to cause from an unrelated one.
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.claude_dir = os.path.join(cls.tmp, ".claude")
        os.makedirs(cls.claude_dir)
        code, out = run(VAULT_TOOL, ["index", "--vault", cls.vault], cls.env)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]

    @classmethod
    def tearDownClass(cls):
        os.chmod(cls.claude_dir, 0o700)   # must be writable again before rmtree can clean it
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ledger_write_failure_warns_but_never_blocks_the_recall(self):
        os.chmod(self.claude_dir, stat.S_IRUSR | stat.S_IXUSR)   # read+execute, no write
        try:
            code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast"], self.env)
        finally:
            os.chmod(self.claude_dir, 0o700)
        self.assertEqual(0, code, "an unwritable ledger directory must never fail the "
                                  "recall it is auditing:\n%s" % out)
        self.assertIn("ledger write failed", out)
        self.assertIn("zorbtangle", out.lower())   # the actual answer still got served
        self.assertFalse(os.path.exists(os.path.join(self.claude_dir, "bm_vault_answers.jsonl")))


class EventIdTest(unittest.TestCase):
    """VB6-03: immutable answer-event ids join the ledger and the telemetry. Own fixture,
    own tmp home, independent of LedgerTest above: numbered for the same reason
    (unittest orders by name, and test_02/03 force specific --event-id values a later
    case's assertions depend on)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_ledger_event_")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        with open(os.path.join(cls.vault, "zorbtangle.md"), "w") as f:
            f.write(NOTE)
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.ledger_path = os.path.join(cls.tmp, ".claude", "bm_vault_answers.jsonl")
        cls.audit_path = os.path.join(cls.tmp, ".claude", "bm_vault_audit.jsonl")
        cls.outcome_path = os.path.join(cls.tmp, ".claude", "bm_vault_outcomes.jsonl")
        code, out = run(VAULT_TOOL, ["index", "--vault", cls.vault], cls.env)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rows(self, path):
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_01_recall_mints_one_event_id_shared_by_ledger_and_audit(self):
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast"], self.env)
        self.assertEqual(0, code, out)
        m = EVENT_RE.search(out)
        self.assertTrue(m, "recall did not print its event id:\n%s" % out)
        event_id = m.group(1)
        ledger_row = self._rows(self.ledger_path)[-1]
        audit_row = self._rows(self.audit_path)[-1]
        self.assertEqual(event_id, ledger_row["event_id"])
        self.assertEqual(event_id, audit_row["event_id"])

    def test_02_an_answer_served_twice_appends_once(self):
        forced = "deadbeefdeadbeefdeadbeefdeadbeef"
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast",
                                     "--event-id", forced], self.env)
        self.assertEqual(0, code, out)
        matches = [r for r in self._rows(self.ledger_path) if r["event_id"] == forced]
        self.assertEqual(1, len(matches))
        audit_before = len(self._rows(self.audit_path))

        # Genuine retry: SAME query, same forced id. Both stores must stay unchanged --
        # this is the MAJOR fix under test: the old code skipped the ledger append but
        # still wrote a new audit row under the same event_id, diverging the two stores.
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast",
                                     "--event-id", forced], self.env)
        self.assertEqual(0, code, out)   # a duplicate never fails the recall itself
        self.assertIn("duplicate append skipped", out)
        matches = [r for r in self._rows(self.ledger_path) if r["event_id"] == forced]
        self.assertEqual(1, len(matches), "an answer served twice must append once")
        self.assertEqual(audit_before, len(self._rows(self.audit_path)),
                         "a genuine retry must leave the audit store unchanged too")

    def test_02b_a_collision_on_the_same_id_with_a_different_query_refuses_both_appends(self):
        forced = "deadbeefdeadbeefdeadbeefdeadbeef"   # already recorded by test_02, above
        ledger_before = self._rows(self.ledger_path)
        audit_before = self._rows(self.audit_path)

        code, out = run(VAULT_TOOL, ["recall", "--query", "zorbtangle counter overflow lesson",
                                     "--fast", "--event-id", forced], self.env)
        self.assertEqual(0, code, out)   # a collision never fails the recall itself
        self.assertIn("REFUSED-COLLISION", out)
        self.assertNotIn("event: %s" % forced, out,
                         "a collided id must never be handed back as this answer's own")
        self.assertEqual(ledger_before, self._rows(self.ledger_path),
                         "a collision must leave the ledger byte-for-byte unchanged")
        self.assertEqual(len(audit_before), len(self._rows(self.audit_path)),
                         "a collision must leave the audit store unchanged")

    def test_02c_driven_backwards(self):
        """Documents the regression this guards, run manually (never as part of CI): revert
        cmd_recall to the old id-only-skip shape -- skip _append_ledger when the event_id is
        already present, but call _append_audit unconditionally regardless of query match --
        and test_02 (audit unchanged on retry) or test_02b (REFUSED-COLLISION / ledger and
        audit both unchanged on collision) fails. Purge tools/__pycache__ between swaps: a
        stale .pyc of bm_vault.py would let the reverted source pass on the old bytecode."""
        pass

    def test_03_telemetry_outcome_joins_by_event_id_despite_a_synthetic_clock_skew(self):
        event_id = "cafefeedcafefeedcafefeedcafefeed"
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast",
                                     "--event-id", event_id], self.env)
        self.assertEqual(0, code, out)

        code, out = run(LEDGER_TOOL, ["outcome", "--event-id", event_id,
                                      "--result", "accepted"], self.env)
        self.assertEqual(0, code, out)

        # Synthetic clock skew: force the outcome row's own ts far from the ledger row's,
        # then confirm the join still resolves -- the join key is event_id alone, never
        # a timestamp comparison.
        rows = self._rows(self.outcome_path)
        rows[-1]["ts"] = "1999-01-01T00:00:00+00:00"
        with open(self.outcome_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")

        code, out = run(LEDGER_TOOL, ["join", "--event-id", event_id], self.env)
        self.assertEqual(0, code, out)
        self.assertIn("ledger row:", out)
        self.assertIn("outcome: ts=1999-01-01T00:00:00+00:00 result=accepted", out)

    def test_03a_outcome_file_created_mode_0600(self):
        # A rejection `result` can itself quote the sensitive query it is about, so it
        # gets the same owner-only mode as the ledger and audit files it sits beside.
        mode = os.stat(self.outcome_path).st_mode & 0o777
        self.assertEqual(oct(mode), oct(0o600))

    def test_03b_join_returns_all_outcomes_for_one_event_id(self):
        event_id = "0ff5e77e0ff5e77e0ff5e77e0ff5e77e"
        code, out = run(VAULT_TOOL, ["recall", "--query", QUERY, "--fast",
                                     "--event-id", event_id], self.env)
        self.assertEqual(0, code, out)

        for result in ("accepted", "rejected", "accepted"):
            code, out = run(LEDGER_TOOL, ["outcome", "--event-id", event_id,
                                          "--result", result], self.env)
            self.assertEqual(0, code, out)
        outcomes = [r for r in self._rows(self.outcome_path) if r["event_id"] == event_id]
        self.assertEqual(3, len(outcomes), "fixture setup: expected 3 outcome rows")

        code, out = run(LEDGER_TOOL, ["join", "--event-id", event_id], self.env)
        self.assertEqual(0, code, out)
        self.assertEqual(3, out.count("outcome: ts="),
                         "join must print every outcome row, not just the first: %s" % out)
        self.assertEqual(2, out.count("result=accepted"))
        self.assertEqual(1, out.count("result=rejected"))

    def test_04_join_is_no_data_for_an_event_in_neither_store(self):
        code, out = run(LEDGER_TOOL, ["join", "--event-id", "0" * 32], self.env)
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)

    def test_05_outcome_needs_both_flags(self):
        code, out = run(LEDGER_TOOL, ["outcome", "--event-id", "onlyid"], self.env)
        self.assertEqual(2, code, out)


class LedgerAbsentIsNoData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_ledger_absent_")
        os.makedirs(os.path.join(self.tmp, ".claude"))
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_show_is_no_data_on_an_absent_ledger(self):
        code, out = run(LEDGER_TOOL, ["show"], self.env)
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)

    def test_replay_is_no_data_on_an_absent_ledger(self):
        code, out = run(LEDGER_TOOL, ["replay", "--ts", "0"], self.env)
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)

    def test_census_is_no_data_on_an_absent_ledger(self):
        code, out = run(LEDGER_TOOL, ["census"], self.env)
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
