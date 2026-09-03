"""Calibration for tools/bm_consolidate.py, in both directions (F8.4).

Every test builds its own isolated tempfile fixture -- an isolated vault index db, an isolated
freshness state db, and real note files on disk under a tempdir -- and never touches the real
~/.claude/bm_vault_index.sqlite3 or ~/.claude/bm_freshness_state.sqlite3 (same discipline
tools/test_bm_freshness.py already follows, for the same reason: this file must never write a
consolidation proposal or summary that carries real vault note content).
"""
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_bc_spec = importlib.util.spec_from_file_location("bm_consolidate", os.path.join(HERE, "bm_consolidate.py"))
bc = importlib.util.module_from_spec(_bc_spec)
_bc_spec.loader.exec_module(bc)

_bv_spec = importlib.util.spec_from_file_location("bm_vault", os.path.join(HERE, "bm_vault.py"))
bv = importlib.util.module_from_spec(_bv_spec)
_bv_spec.loader.exec_module(bv)


def _build_vault_index(db_path, notes):
    """notes: [(path, anchors_set)]. Same helper tools/test_bm_freshness.py uses: builds the
    fixture db with bm_vault.py's own real schema (_schema()), so it cannot drift from the
    columns stale_tail actually reads."""
    con = sqlite3.connect(db_path)
    bv._schema(con)
    for path, anchors in notes:
        cur = con.execute(
            "INSERT INTO notes (path,title,descr,source,kind,mtime,body) "
            "VALUES (?,?,?,?,?,?,?)", (path, path, "", "vault", "lesson", 0.0, ""))
        nid = cur.lastrowid
        con.executemany("INSERT INTO anchors (note_id,anchor) VALUES (?,?)",
                        [(nid, a) for a in anchors])
    con.commit()
    con.close()


class Contract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.coderoot = os.path.join(self.root, "coderoot")
        os.makedirs(self.coderoot)
        self.notes_dir = os.path.join(self.root, "notes")
        os.makedirs(self.notes_dir)
        self.index_db = os.path.join(self.root, "vault_index.sqlite3")
        self.state_db = os.path.join(self.root, "state.sqlite3")
        self.proposals_dir = os.path.join(self.root, "proposals")
        self.approved_dir = os.path.join(self.root, "approved")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_note(self, name, body, tag=None, pinned=False):
        path = os.path.join(self.notes_dir, name)
        front = ["---"]
        if tag:
            front.append("tags: [%s]" % tag)
        if pinned:
            front.append("pinned: true")
        front.append("---")
        text = "\n".join(front) + "\n\n" + body + "\n" if len(front) > 2 else body
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    # -- F8.4.1: candidate selection excludes fresh notes --------------------------

    def test_candidates_exclude_fresh(self):
        with open(os.path.join(self.coderoot, "thing.py"), "w") as fh:
            fh.write("# real file\n")
        fresh1 = self._write_note("fresh1.md", "cites thing.py")
        fresh2 = self._write_note("fresh2.md", "also cites thing.py")
        stale1 = self._write_note("stale1.md", "cites missing.py", tag="grp")
        stale2 = self._write_note("stale2.md", "also cites missing.py", tag="grp")
        stale3 = self._write_note("stale3.md", "also cites missing.py", tag="grp")
        _build_vault_index(self.index_db, [
            (fresh1, {"thing.py"}), (fresh2, {"thing.py"}),
            (stale1, {"missing.py"}), (stale2, {"missing.py"}), (stale3, {"missing.py"}),
        ])
        out = bc.stale_tail([self.coderoot], index_path=self.index_db, state_db=self.state_db)
        got_paths = set(c["path"] for c in out)
        self.assertEqual(got_paths, {stale1, stale2, stale3})
        for c in out:
            self.assertEqual(c["status"], "stale")

    def test_candidates_exclude_pinned(self):
        stale1 = self._write_note("stale1.md", "cites missing.py", tag="grp")
        pinned1 = self._write_note("pinned1.md", "cites missing.py too", tag="grp", pinned=True)
        _build_vault_index(self.index_db, [
            (stale1, {"missing.py"}), (pinned1, {"missing.py"}),
        ])
        out = bc.stale_tail([self.coderoot], index_path=self.index_db, state_db=self.state_db)
        got_paths = set(c["path"] for c in out)
        self.assertEqual(got_paths, {stale1})

    def test_no_index_is_NO_DATA(self):
        self.assertIsNone(bc.stale_tail([self.coderoot], index_path=self.index_db,
                                        state_db=self.state_db))

    # -- verify_immutable() in isolation ---------------------------------------------

    def test_verify_immutable_flags_exactly_the_changed_path(self):
        before = {"a.md": "hash-a", "b.md": "hash-b", "c.md": "hash-c"}
        after = {"a.md": "hash-a", "b.md": "CHANGED", "c.md": "hash-c"}
        self.assertEqual(bc.verify_immutable(before, after), ["b.md"])

    def test_verify_immutable_reports_no_change(self):
        before = {"a.md": "hash-a", "b.md": "hash-b"}
        self.assertEqual(bc.verify_immutable(before, dict(before)), [])

    # -- F8.4.2: propose then approve end to end, both directions -------------------

    def _seed_three_stale(self):
        stale1 = self._write_note("stale1.md", "cites missing.py", tag="grp")
        stale2 = self._write_note("stale2.md", "also cites missing.py", tag="grp")
        stale3 = self._write_note("stale3.md", "also cites missing.py", tag="grp")
        _build_vault_index(self.index_db, [
            (stale1, {"missing.py"}), (stale2, {"missing.py"}), (stale3, {"missing.py"}),
        ])
        return stale1, stale2, stale3

    def test_propose_then_approve_unmutated_passes(self):
        stale1, stale2, stale3 = self._seed_three_stale()
        candidates = bc.stale_tail([self.coderoot], index_path=self.index_db,
                                   state_db=self.state_db)
        proposals = bc.draft_summary(candidates)
        self.assertEqual(len(proposals), 1)
        proposal_path = bc.write_proposal(proposals[0], self.proposals_dir)
        self.assertTrue(os.path.isfile(proposal_path))

        before_bytes = {}
        for p in (stale1, stale2, stale3):
            with open(p, "rb") as fh:
                before_bytes[p] = fh.read()

        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            approved_path = bc.approve(proposal_path, "wbs-test", [self.coderoot],
                                       out_dir=self.approved_dir, index_path=self.index_db,
                                       state_db=self.state_db)
        finally:
            sys.stdout = old
        self.assertIn("byte-identical: PASS", out.getvalue())
        self.assertTrue(os.path.isfile(approved_path))

        for p, before in before_bytes.items():
            with open(p, "rb") as fh:
                after = fh.read()
            self.assertEqual(after, before, "%s was mutated by propose/approve" % p)

    def test_approve_refuses_and_names_the_path_when_a_raw_note_mutates(self):
        stale1, stale2, stale3 = self._seed_three_stale()
        candidates = bc.stale_tail([self.coderoot], index_path=self.index_db,
                                   state_db=self.state_db)
        proposals = bc.draft_summary(candidates)
        proposal_path = bc.write_proposal(proposals[0], self.proposals_dir)

        # Mutate one raw stale note's bytes after propose, before approve. Still cites the same
        # (still-missing) anchor, so it stays classified stale and reaches the hash check rather
        # than being refused for eligibility first.
        with open(stale2, "a", encoding="utf-8") as fh:
            fh.write("mutated after propose\n")

        with self.assertRaises(ValueError) as ctx:
            bc.approve(proposal_path, "wbs-test", [self.coderoot], out_dir=self.approved_dir,
                      index_path=self.index_db, state_db=self.state_db)
        msg = str(ctx.exception)
        self.assertIn("byte-identical", msg)
        self.assertIn(stale2, msg)
        self.assertFalse(os.path.exists(self.approved_dir),
                         "approve() must not write a summary when immutability fails")

    def test_approve_requires_a_non_empty_approved_by(self):
        self._seed_three_stale()
        candidates = bc.stale_tail([self.coderoot], index_path=self.index_db,
                                   state_db=self.state_db)
        proposals = bc.draft_summary(candidates)
        proposal_path = bc.write_proposal(proposals[0], self.proposals_dir)
        with self.assertRaises(ValueError):
            bc.approve(proposal_path, "   ", [self.coderoot], out_dir=self.approved_dir,
                      index_path=self.index_db, state_db=self.state_db)

    def test_cli_candidates_exits_zero(self):
        self._seed_three_stale()
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = bc.main(["candidates", "--root", self.coderoot, "--index", self.index_db,
                         "--state", self.state_db])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out.getvalue())), 3)

    def test_cli_propose_then_approve_exit_codes(self):
        self._seed_three_stale()
        rc1 = bc.main(["propose", "--root", self.coderoot, "--index", self.index_db,
                      "--state", self.state_db, "--out", self.proposals_dir])
        self.assertEqual(rc1, 0)
        written = sorted(os.listdir(self.proposals_dir))
        self.assertEqual(len(written), 1)
        proposal_path = os.path.join(self.proposals_dir, written[0])
        rc2 = bc.main(["approve", "--proposal", proposal_path, "--approved-by", "wbs-test",
                      "--root", self.coderoot, "--index", self.index_db,
                      "--state", self.state_db, "--out-dir", self.approved_dir])
        self.assertEqual(rc2, 0)
        self.assertEqual(len(os.listdir(self.approved_dir)), 1)


if __name__ == "__main__":
    unittest.main()
