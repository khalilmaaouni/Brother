#!/usr/bin/env python3
"""Tests for bm_vault_retention: a real deletion in a scratch copy of a vault.

The fixture is built here, indexed by the REAL bm_vault.py index command as a
subprocess (HOME moved, so the index is a throwaway), then a note is really
deleted from disk. The census must name the stale row, propagate --apply must
remove it from every derived table, and the census must read clean after. The
guard is calibrated the other way too: propagate on a still-live note refuses.

Run: python3 tools/test_bm_vault_retention.py      (unittest output, exit 0 or 1)
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault_retention.py")
INDEXER = os.path.join(HERE, "bm_vault.py")

NOTE_A = """---
name: note-alpha
description: a live lesson that links to the doomed note
---
Lesson alpha cites BetaThing.swift and links [[note-beta]] for context.
"""

NOTE_B = """---
name: note-beta
description: the note this suite deletes for real
---
Lesson beta about BetaThing.swift retention behavior.
"""


def run(tool_args, env, tool=TOOL):
    p = subprocess.run([sys.executable, tool] + tool_args, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


class RetentionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_retention_")
        cls.vault = os.path.join(cls.tmp, "vault")
        failures = os.path.join(cls.vault, "40-Failures")
        os.makedirs(failures)
        cls.path_a = os.path.join(cls.vault, "note-alpha.md")
        cls.path_b = os.path.join(cls.vault, "note-beta.md")
        with open(cls.path_a, "w") as f:
            f.write(NOTE_A)
        with open(cls.path_b, "w") as f:
            f.write(NOTE_B)
        cls.failures_index = os.path.join(failures, "Failures-Index.md")
        with open(cls.failures_index, "w") as f:
            f.write("# Failures\n- [[note-beta]] the beta failure, hand curated\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp          # moves the index to a throwaway path
        cls.env["BM_VAULT_ROOT"] = cls.vault
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        code, out = run(["index", "--vault", cls.vault], cls.env, tool=INDEXER)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]
        cls.db = os.path.join(cls.tmp, ".claude", "bm_vault_index.sqlite3")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _note_id(self, path):
        con = sqlite3.connect(self.db)
        try:
            row = con.execute("SELECT id FROM notes WHERE path=?", (path,)).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def test_01_census_clean_on_a_fresh_index(self):
        code, out = run(["census"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)

    def test_02_a_real_deletion_is_a_named_stale_row(self):
        os.remove(self.path_b)
        code, out = run(["census"], self.env)
        self.assertEqual(code, 1, out)
        self.assertIn(self.path_b, out)
        self.assertIn("gone", out)

    def test_03_propagate_refuses_a_note_still_live_on_disk(self):
        code, out = run(["propagate", "--note", self.path_a], self.env)
        self.assertEqual(code, 1, out)
        self.assertIn("REFUSED", out)

    def test_04_dry_run_reports_and_changes_nothing(self):
        nid = self._note_id(self.path_b)
        self.assertIsNotNone(nid, "deleted note should still be indexed pre-propagate")
        code, out = run(["propagate", "--note", self.path_b], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("would remove", out)
        self.assertIn("DRY RUN", out)
        self.assertIn(self.path_a, out)               # inbound wikilink named
        self.assertIn("Failures-Index.md", out)       # hand-curated line named
        self.assertIn("hand-curated", out)
        # D13/VB2-04: caches this tool cannot reach are named as MANUAL follow-ups,
        # never silently skipped, so the erasure claim never overreaches.
        self.assertIn("MANUAL follow-up", out)
        self.assertIn(".vault_recall_seen", out)
        self.assertIn("bm_vault_distill.py", out)
        self.assertIsNotNone(self._note_id(self.path_b), "dry run must not delete")

    def test_04b_propagate_names_the_answer_ledger_as_manual(self):
        # VB2-05: the answer ledger is append-only history of what a past recall
        # actually read. propagate must NAME it as a MANUAL follow-up, the same
        # report-only treatment as the hand-curated Failures-Index lines, and
        # must never create, touch or delete the ledger file itself -- erasing
        # or editing a past row would falsify what that recall actually read.
        ledger = os.path.join(self.tmp, ".claude", "bm_vault_answers.jsonl")
        self.assertFalse(os.path.exists(ledger), "no ledger should exist in this fixture yet")
        code, out = run(["propagate", "--note", self.path_b], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("bm_vault_answers.jsonl", out)
        self.assertIn("append-only history", out)
        self.assertFalse(os.path.exists(ledger),
                         "propagate must never create or touch the answer ledger")

    def test_05_apply_removes_every_derived_row_and_census_reads_clean(self):
        nid = self._note_id(self.path_b)
        with open(self.failures_index) as f:
            before = f.read()
        code, out = run(["propagate", "--note", self.path_b, "--apply"], self.env)
        self.assertEqual(code, 0, out)
        con = sqlite3.connect(self.db)
        try:
            for table, key in (("notes", "id"), ("anchors", "note_id"),
                               ("links", "note_id"), ("vectors", "note_id"),
                               ("supersessions", "by_note_id")):
                c = con.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (table, key),
                                (nid,)).fetchone()[0]
                self.assertEqual(c, 0, "%s still holds rows for the removed note" % table)
            c = con.execute("SELECT COUNT(*) FROM notes_fts WHERE rowid=?", (nid,)).fetchone()[0]
            self.assertEqual(c, 0, "notes_fts still holds the removed note")
        finally:
            con.close()
        with open(self.failures_index) as f:
            self.assertEqual(before, f.read(), "hand-curated file must never be auto-edited")
        code, out = run(["census"], self.env)
        self.assertEqual(code, 0, out)

    def test_06_a_row_pointing_into_a_superseded_folder_is_revoked(self):
        # The revoked shape, from the estate's own history: rows indexed BEFORE
        # bm_vault grew its superseded/archive walk filter kept answering
        # recalls (35 duplicate titles, 2026-08-28). The file still exists, so
        # "gone" cannot catch it; only the directory policy can. Simulated by
        # inserting the pre-filter row directly, which is exactly the state
        # such a row is in.
        revoked_dir = os.path.join(self.vault, "superseded-old")
        os.makedirs(revoked_dir)
        gamma = os.path.join(revoked_dir, "note-gamma.md")
        with open(gamma, "w") as f:
            f.write("old superseded content\n")
        con = sqlite3.connect(self.db)
        try:
            cur = con.execute(
                "INSERT INTO notes (path,title,descr,source,kind,mtime,body) "
                "VALUES (?,?,?,?,?,?,?)",
                (gamma, "note gamma", "", "vault", "lesson", 1.0, "old superseded content"))
            con.execute("INSERT INTO notes_fts (rowid,title,descr,body) VALUES (?,?,?,?)",
                        (cur.lastrowid, "note gamma", "", "old superseded content"))
            con.commit()
        finally:
            con.close()
        code, out = run(["census"], self.env)
        self.assertEqual(code, 1, out)
        self.assertIn("revoked", out)
        code, out = run(["propagate", "--note", gamma, "--apply"], self.env)
        self.assertEqual(code, 0, out)
        code, out = run(["census"], self.env)
        self.assertEqual(code, 0, out)  # note-alpha is still live, so clean

    def test_07_absent_index_is_no_data_never_a_pass(self):
        env = dict(self.env)
        empty_home = os.path.join(self.tmp, "empty-home")
        os.makedirs(empty_home, exist_ok=True)
        env["HOME"] = empty_home
        code, out = run(["census"], env)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        code, out = run(["propagate", "--note", "anything.md"], env)
        self.assertEqual(code, 2, out)

    def test_08_an_underscore_in_the_token_never_cross_matches_a_hyphen(self):
        # The suffix lookup used to run a raw SQL LIKE '%'||token, where "_"
        # is a single-char wildcard: a typo'd --note lessons_a.md (underscore)
        # could therefore cross-match an unrelated lessons-a.md (hyphen)
        # already in the index, and --apply would delete the WRONG note's
        # rows. The index here holds only the hyphenated name.
        lessons_path = os.path.join(self.vault, "40-Failures", "lessons-a.md")
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                "INSERT INTO notes (path,title,descr,source,kind,mtime,body) "
                "VALUES (?,?,?,?,?,?,?)",
                (lessons_path, "lessons a", "", "vault", "lesson", 1.0, "content"))
            con.commit()
        finally:
            con.close()
        code, out = run(["propagate", "--note", "lessons_a.md"], self.env)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)


class GhostVectorTest(unittest.TestCase):
    """VB2-04: soft deletion is reconstructible ("ghost vectors"), so erasure
    must be physical. This probe recovers a deleted note from the raw index
    file three ways after a real propagate --apply, using the REAL bm_vault.py
    indexer to build the fixture. It is calibrated: run it against the tool
    as it stood before this fix (git show <prior-sha>:tools/bm_vault_retention.py)
    and (a) the raw-byte scan and (b) the fts5 MATCH scan both recover the
    marker; only (c) vectors was already clean. Against the fixed tool below,
    all three must come back empty.
    """

    MARKER_WORD = "Xk9Qzvorpal7"
    MARKER = MARKER_WORD + " the ghost vector marker sentence lives here uniquely"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_ghost_")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        self.path = os.path.join(self.vault, "note-ghost.md")
        with open(self.path, "w") as f:
            f.write("---\nname: note-ghost\ndescription: doomed\n---\n%s\n" % self.MARKER)
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BM_VAULT_ROOT"] = self.vault
        os.makedirs(os.path.join(self.tmp, ".claude"))
        code, out = run(["index", "--vault", self.vault], self.env, tool=INDEXER)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]
        self.db = os.path.join(self.tmp, ".claude", "bm_vault_index.sqlite3")
        con = sqlite3.connect(self.db)
        try:
            self.nid = con.execute(
                "SELECT id FROM notes WHERE path=?", (self.path,)).fetchone()[0]
            # No embed machine is present in a bare checkout (bm-embed-bge needs a
            # .venv-embed this worktree does not carry), so cmd_index leaves vectors
            # empty; a live vault would have populated it. Insert the row it would
            # have written, so the erasure path under test still has a vectors row
            # to actually delete.
            con.execute("INSERT OR REPLACE INTO vectors (note_id, v) VALUES (?, X'000000')",
                        (self.nid,))
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _raw_bytes_leak(self):
        with open(self.db, "rb") as f:
            return self.MARKER.encode("utf-8") in f.read()

    def _fts_match_survivors(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
                               (self.MARKER_WORD,)).fetchall()
        finally:
            con.close()

    def _vectors_rows(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT COUNT(*) FROM vectors WHERE note_id=?",
                               (self.nid,)).fetchone()[0]
        finally:
            con.close()

    def test_apply_seals_all_three_recovery_paths(self):
        os.remove(self.path)
        code, out = run(["propagate", "--note", self.path, "--apply"], self.env)
        self.assertEqual(code, 0, out)
        self.assertFalse(self._raw_bytes_leak(),
                          "marker sentence recovered from raw db bytes after --apply")
        self.assertEqual(self._fts_match_survivors(), [],
                          "fts5 MATCH still finds the deleted note's own words")
        self.assertEqual(self._vectors_rows(), 0, "vectors row survived --apply")


class CensusJson(unittest.TestCase):
    """VB7-02: --json on census. Prose stays byte-identical when --json is
    absent (proven by every RetentionTest prose assertion above still
    passing unchanged); this class covers what --json adds."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_retention_json_")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        cls.path = os.path.join(cls.vault, "note.md")
        with open(cls.path, "w") as f:
            f.write("---\nname: note\ndescription: a note\n---\nBody.\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        code, out = run(["index", "--vault", cls.vault], cls.env, tool=INDEXER)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pass_json_matches_prose_and_exit_code(self):
        pcode, pout = run(["census"], self.env)
        self.assertEqual(pcode, 0, pout)
        self.assertIn("clean", pout)
        jcode, jout = run(["census", "--json"], self.env)
        self.assertEqual(jcode, 0, jout)
        data = json.loads(jout)
        self.assertEqual(data["verdict"], "PASS", jout)
        self.assertEqual(data["findings"], [], jout)
        self.assertEqual(data["counts"]["indexed"], 1, jout)

    def test_fail_json_matches_prose_stale_count(self):
        os.remove(self.path)
        try:
            pcode, pout = run(["census"], self.env)
            self.assertEqual(pcode, 1, pout)
            jcode, jout = run(["census", "--json"], self.env)
            self.assertEqual(jcode, 1, jout)
            data = json.loads(jout)
            self.assertEqual(data["verdict"], "FAIL", jout)
            self.assertEqual(data["counts"]["stale"], 1, jout)
            self.assertEqual(len(data["findings"]), 1, jout)
            self.assertEqual(data["findings"][0]["path"], self.path, jout)
        finally:
            with open(self.path, "w") as f:
                f.write("---\nname: note\ndescription: a note\n---\nBody.\n")

    def test_no_index_is_no_data_json(self):
        empty_home = tempfile.mkdtemp(prefix="bm_retention_json_nohome_")
        try:
            env = dict(os.environ)
            env["HOME"] = empty_home
            env["BM_VAULT_ROOT"] = self.vault
            code, out = run(["census", "--json"], env)
            self.assertEqual(code, 2, out)
            data = json.loads(out)
            self.assertEqual(data["verdict"], "NO-DATA", out)
        finally:
            shutil.rmtree(empty_home, ignore_errors=True)




class ForgetPlanTest(unittest.TestCase):
    """VB3-08: forget-plan names every derived object class a live note holds,
    calibrated by adding one more object per class; forget-execute honors a
    legal hold, then leaves no orphan row and a content-free receipt."""

    SECRET = "Xk9SecretMarker7Zq"
    SUBJECT_ID = "n-2222222222222222"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_forget_")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "40-Failures"))
        cls.subject_path = os.path.join(cls.vault, "note-subject.md")
        with open(cls.subject_path, "w") as f:
            f.write("---\n")
            f.write("name: note-subject\n")
            f.write("description: the note this suite forgets\n")
            f.write("id: %s\n" % cls.SUBJECT_ID)
            f.write("entity: person-role\n")
            f.write("---\n")
            f.write("Lesson body. %s\n" % cls.SECRET)
            f.write("claim: the subject exists [evidence: path:note-subject.md]\n")
        cls.linker_path = os.path.join(cls.vault, "note-linker.md")
        with open(cls.linker_path, "w") as f:
            f.write("---\nname: note-linker\ndescription: links to the subject\n---\n"
                   "See [[note-subject]] for context.\n")
        cls.digest_path = os.path.join(cls.vault, "40-Failures", "digest-one.md")
        with open(cls.digest_path, "w") as f:
            f.write("---\nname: digest-one\ndescription: unrelated digest\n---\n"
                   "We learned something from note-subject today.\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        code, out = run(["index", "--vault", cls.vault], cls.env, tool=INDEXER)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]
        cls.db = os.path.join(cls.tmp, ".claude", "bm_vault_index.sqlite3")
        cls.plan_out = os.path.join(cls.tmp, "plan.json")
        cls.systemdir = os.path.join(cls.vault, "99-System")
        os.makedirs(cls.systemdir, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _nid(self, path):
        con = sqlite3.connect(self.db)
        try:
            row = con.execute("SELECT id FROM notes WHERE path=?", (path,)).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def _plan(self):
        code, out = run(["forget-plan", "--vault", self.vault, "--id", self.SUBJECT_ID,
                        "--out", self.plan_out, "--json"], self.env)
        self.assertEqual(code, 0, out)
        return json.loads(out)

    def _counts(self, plan):
        return dict((c["name"], c["count"]) for c in plan["classes"])

    def test_01_baseline_names_every_class_the_fixture_actually_holds(self):
        plan = self._plan()
        self.assertEqual(plan["entity_subject"], self.SUBJECT_ID)
        counts = self._counts(plan)
        self.assertEqual(counts["index"], 1, counts)
        self.assertEqual(counts["vectors"], 0, counts)
        self.assertEqual(counts["fts"], 1, counts)
        self.assertEqual(counts["edges_inbound"], 1, counts)
        self.assertEqual(counts["citations"], 0, counts)
        self.assertEqual(counts["assertions"], 0, counts)
        self.assertEqual(counts["resolutions"], 0, counts)
        self.assertEqual(counts["events"], 0, counts)
        self.assertEqual(counts["evidence"], 1, counts)
        self.assertEqual(counts["summaries"], 1, counts)
        enumerable = dict((c["name"], c["enumerable"]) for c in plan["classes"])
        self.assertFalse(enumerable["caches"])
        self.assertFalse(enumerable["exports"])
        reasons = dict((c["name"], c.get("reason", "")) for c in plan["classes"])
        self.assertIn("query text", reasons["caches"])
        self.assertIn("caller-named", reasons["exports"])

    def test_02_calibration_adding_one_object_grows_exactly_that_class(self):
        nid = self._nid(self.subject_path)
        self.assertIsNotNone(nid)

        def add_vector():
            con = sqlite3.connect(self.db)
            con.execute("INSERT OR REPLACE INTO vectors (note_id, v) VALUES (?, X'0000')", (nid,))
            con.commit()
            con.close()

        def add_anchor():
            con = sqlite3.connect(self.db)
            con.execute("INSERT INTO anchors (note_id, anchor) VALUES (?, ?)",
                       (nid, "NewAnchor.swift"))
            con.commit()
            con.close()

        def add_edge_outbound():
            con = sqlite3.connect(self.db)
            con.execute("INSERT INTO links (note_id, target) VALUES (?, ?)",
                       (nid, "somewhere-else"))
            con.commit()
            con.close()

        def add_supersession():
            con = sqlite3.connect(self.db)
            con.execute("INSERT INTO supersessions (stem, by_note_id) VALUES (?, ?)",
                       ("some-old-note", nid))
            con.commit()
            con.close()

        def add_edge_inbound():
            con = sqlite3.connect(self.db)
            cur = con.execute(
                "INSERT INTO notes (path,title,descr,source,kind,mtime,body) "
                "VALUES (?,?,?,?,?,?,?)",
                (os.path.join(self.vault, "note-linker2.md"), "note linker2", "",
                "vault", "lesson", 1.0, "second linker"))
            con.execute("INSERT INTO links (note_id, target) VALUES (?, ?)",
                       (cur.lastrowid, "note-subject"))
            con.commit()
            con.close()

        def add_citation():
            rec = dict(note_id=self.SUBJECT_ID, content_sha256="ab" * 32,
                      lifecycle="candidate", by="wbs-extra")
            with open(os.path.join(self.systemdir, "citations.jsonl"), "a") as f:
                f.write(json.dumps(rec) + "\n")

        def add_assertion():
            rec = dict(id="as-extra0000000a", subject=self.SUBJECT_ID, predicate="status",
                      value="ok", authority="stated", lifecycle="candidate",
                      source_locator="test", recorded_at="2026-08-31")
            with open(os.path.join(self.systemdir, "assertions.jsonl"), "a") as f:
                f.write(json.dumps(rec) + "\n")

        def add_resolution():
            rec = dict(id="cr-extra0000000a", subject=self.SUBJECT_ID, predicate="status",
                      winner="as-extra0000000a", scope="global", valid_from="2026-01-01",
                      valid_to=None, approval=dict(approver="k"), recorded_at="2026-08-31")
            with open(os.path.join(self.systemdir, "resolutions.jsonl"), "a") as f:
                f.write(json.dumps(rec) + "\n")

        def add_event():
            rec = dict(event_key="ek-extra-1", kind="upsert", ref=self.SUBJECT_ID,
                      occurred_at="2026-08-31T00:00:00Z", recorded_at="2026-08-31T00:00:00Z")
            d = os.path.join(self.vault, ".vault")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "events.jsonl"), "a") as f:
                f.write(json.dumps(rec) + "\n")

        def add_summary():
            with open(os.path.join(self.vault, "40-Failures", "digest-two.md"), "w") as f:
                f.write("---\nname: digest-two\ndescription: another digest\n---\n"
                       "Another mention of note-subject here.\n")

        def add_evidence():
            with open(self.subject_path, "a") as f:
                f.write("claim: a second fact [evidence: path:note-subject.md]\n")

        calibrations = [
            ("vectors", add_vector),
            ("anchors", add_anchor),
            ("edges_outbound", add_edge_outbound),
            ("supersessions", add_supersession),
            ("edges_inbound", add_edge_inbound),
            ("citations", add_citation),
            ("assertions", add_assertion),
            ("resolutions", add_resolution),
            ("events", add_event),
            ("summaries", add_summary),
            ("evidence", add_evidence),
        ]

        before = self._counts(self._plan())
        for name, setup in calibrations:
            setup()
            after = self._counts(self._plan())
            diff = dict((k, after[k] - before[k]) for k in after)
            expected = dict((k, 0) for k in after)
            expected[name] = 1
            self.assertEqual(diff, expected, "calibrating %s: got %s" % (name, diff))
            before = after

    def test_03_legal_hold_blocks_execution_naming_the_hold(self):
        code, out = run(["legal-hold", "--vault", self.vault, "--target", self.SUBJECT_ID,
                        "--by", "khalil", "--reason", "litigation hold pending"], self.env)
        self.assertEqual(code, 0, out)
        plan = self._plan()
        self.assertEqual(plan["legal_hold"]["status"], "active")
        code, out = run(["forget-execute", "--vault", self.vault, "--plan", self.plan_out],
                        self.env)
        self.assertEqual(code, 1, out)
        self.assertIn("REFUSED", out)
        self.assertIn("legal hold", out)
        self.assertIn("litigation hold pending", out)
        self.assertTrue(os.path.exists(self.subject_path),
                        "a held note must not be touched")
        nid = self._nid(self.subject_path)
        self.assertIsNotNone(nid, "a held note must stay indexed too")

    def test_04_release_then_execute_leaves_no_orphan_and_a_content_free_receipt(self):
        code, out = run(["legal-hold", "--vault", self.vault, "--target", self.SUBJECT_ID,
                        "--by", "khalil", "--release"], self.env)
        self.assertEqual(code, 0, out)
        plan = self._plan()
        self.assertEqual(plan["legal_hold"]["status"], "none")
        nid = plan["note"]["index_id"]
        code, out = run(["forget-execute", "--vault", self.vault, "--plan", self.plan_out],
                        self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("applied", out)
        self.assertFalse(os.path.exists(self.subject_path), "the note file must be gone")

        con = sqlite3.connect(self.db)
        try:
            for table, key in (("notes", "id"), ("anchors", "note_id"), ("links", "note_id"),
                              ("vectors", "note_id"), ("supersessions", "by_note_id")):
                c = con.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (table, key),
                               (nid,)).fetchone()[0]
                self.assertEqual(c, 0, "%s still holds a row for the forgotten note" % table)
            c = con.execute("SELECT COUNT(*) FROM notes_fts WHERE rowid=?",
                           (nid,)).fetchone()[0]
            self.assertEqual(c, 0, "notes_fts still holds the forgotten note")
        finally:
            con.close()

        receipts_dir = os.path.join(self.systemdir, "forget-receipts")
        files = [f for f in os.listdir(receipts_dir) if f.endswith(".json")]
        self.assertEqual(len(files), 1, files)
        with open(os.path.join(receipts_dir, files[0])) as f:
            receipt_text = f.read()
        self.assertNotIn(self.SECRET, receipt_text,
                         "the receipt must never hold the erased note's own content")
        self.assertNotIn("Lesson body", receipt_text)
        receipt = json.loads(receipt_text)
        self.assertEqual(len(receipt["content_hash"]), 64)
        stored_hash = receipt["content_hash"]
        recomputed_input = dict(receipt)
        del recomputed_input["content_hash"]
        recomputed = hashlib.sha256(
            json.dumps(recomputed_input, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(stored_hash, recomputed,
                         "content_hash must cover exactly the receipt's own other fields")
        self.assertGreaterEqual(len(receipt["manual_followups"]), 1)

    def test_05_a_replan_after_execute_finds_zero_remaining(self):
        code, out = run(["forget-plan", "--vault", self.vault, "--id", self.SUBJECT_ID],
                        self.env)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)


class ForgetPlanNoIdTest(unittest.TestCase):
    """A note with no declared stable id: citations/assertions/resolutions/events all
    report NOT-ENUMERABLE naming the missing key, never a guessed zero."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_forget_noid_")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        cls.path = os.path.join(cls.vault, "plain-note.md")
        with open(cls.path, "w") as f:
            f.write("---\nname: plain-note\ndescription: no id, no entity\n---\nJust text.\n")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        code, out = run(["index", "--vault", cls.vault], cls.env, tool=INDEXER)
        assert code == 0 and "indexed" in out, "fixture index failed: %s" % out[:300]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_stable_id_is_not_enumerable_for_the_four_id_keyed_classes(self):
        code, out = run(["forget-plan", "--vault", self.vault, "--id", "plain-note.md",
                        "--json"], self.env)
        self.assertEqual(code, 0, out)
        plan = json.loads(out)
        self.assertIsNone(plan["entity_subject"])
        by_name = dict((c["name"], c) for c in plan["classes"])
        for name in ("citations", "assertions", "resolutions", "events"):
            self.assertFalse(by_name[name]["enumerable"], name)
            self.assertIn("no stable id" if name in ("citations", "events") else "not a "
                         "declared entity", by_name[name]["reason"])

    def test_unresolvable_id_is_no_data(self):
        code, out = run(["forget-plan", "--vault", self.vault, "--id", "n-9999999999999999"],
                        self.env)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_forget_execute_refuses_a_missing_plan_file(self):
        code, out = run(["forget-execute", "--vault", self.vault, "--plan",
                        os.path.join(self.tmp, "no-such-plan.json")], self.env)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
