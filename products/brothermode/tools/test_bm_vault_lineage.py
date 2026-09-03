#!/usr/bin/env python3
"""Calibration for tools/bm_vault_lineage.py, WBS row VB11-01: one origin
answer read from three existing seams (bm_vault_intake provenance,
bm_vault_events the event stream, bm_vault_cite citations), no new store.

Fixtures are built through the three seams' OWN write paths wherever one
exists (bm_vault_intake.main(["admit", ...]) for the note, bm_vault_cite.
main(["mint", ...]) for the citation record) so this suite can never drift
from what those modules actually write; the event stream has no minting
command of its own, so its fixture is written in bm_vault_events' own
documented JSONL schema directly.

Driven backwards: a fixture touched by all three seams answers with all
three named (a); a hand-created note touched by none answers NO-DATA for
each, exit 0 (b); a corrupted citation hash is reported as
SUPERSEDED-CONTENT, reusing bm_vault_cite's own vocabulary (c); a deleted
event log reports NO-DATA naming the missing file (d); an unresolvable id
exits nonzero naming it (e); --json carries the same verdicts as the prose
run for both fixtures (f).

No em or en dashes anywhere in this file.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_cite as cite        # noqa: E402
import bm_vault_ids as ids          # noqa: E402
import bm_vault_intake as intake    # noqa: E402
import bm_vault_lineage as lineage  # noqa: E402


def run(argv):
    """(exit_code, combined_output) for one CLI call, mirroring the
    run/capture shape this module family already uses (test_bm_vault_cite,
    test_bm_vault_closure)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lineage.main(argv)
    return code, out.getvalue() + err.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-lineage-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _events_path(self):
        path = os.path.join(self.vault, ".vault", "events.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _citations_path(self):
        path = os.path.join(self.vault, "99-System", "citations.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _admit_one(self, text="fixture body text for lineage testing"):
        """Runs bm_vault_intake's OWN admit path so the produced note's
        frontmatter is exactly what that module writes, never an invented
        shape. Returns the minted note id."""
        src = os.path.join(self.tmp, "fixture-note.md")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(text)
        rc = intake.main(["admit", "--vault", self.vault,
                           "--source", "test-system", "--by", "tester", src])
        self.assertEqual(rc, 0, "fixture admit must land clean")
        by_id, missing, _ = ids.index(self.vault)
        self.assertEqual(len(missing), 0)
        self.assertEqual(len(by_id), 1)
        return next(iter(by_id))

    def _write_event(self, note_id):
        rec = {"event_key": "ek-" + note_id, "kind": "upsert", "ref": note_id,
               "occurred_at": "2026-01-01T00:00:00Z",
               "recorded_at": "2026-01-01T00:00:01Z"}
        with open(self._events_path(), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _mint_citation(self, note_id):
        """Runs bm_vault_cite's OWN mint path so the citation record's hash
        and lifecycle are exactly what that module computes, never
        recomputed here a second way."""
        rc = cite.main(["mint", "--vault", self.vault, "--note", note_id,
                         "--by", "VB11-01", "--out", self._citations_path()])
        self.assertEqual(rc, 0)

    def _full_fixture(self):
        note_id = self._admit_one()
        self._write_event(note_id)
        self._mint_citation(note_id)
        return note_id

    def _hand_created_note(self):
        """No intake, no events, no citations: a note nobody has touched
        since it was hand-written straight into the vault."""
        note_id = ids.mint()
        path = os.path.join(self.vault, "hand-written.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\nid: %s\ntype: reference\nstatus: open\n---\n\n"
                      "# a hand-written note\n\nnobody touched this.\n" % note_id)
        return note_id


class FullFixtureTests(Fixture):
    def test_show_names_all_three_seams(self):
        note_id = self._full_fixture()
        rc, out = run(["show", "--vault", self.vault, "--id", note_id])
        self.assertEqual(rc, 0)
        self.assertIn("INTAKE", out)
        self.assertIn("provenance_source: test-system", out)
        self.assertIn("EVENTS", out)
        self.assertIn("ek-" + note_id, out)
        self.assertIn("CITATIONS", out)
        self.assertIn("VB11-01", out)
        self.assertNotIn("NO-DATA", out)

    def test_resolve_by_vault_relative_path(self):
        note_id = self._full_fixture()
        rel = ids.index(self.vault)[0][note_id]
        rc, out = run(["show", "--vault", self.vault, "--id", rel])
        self.assertEqual(rc, 0)
        self.assertIn(note_id, out)


class HandCreatedFixtureTests(Fixture):
    def test_each_silent_seam_is_no_data_and_exit_is_clean(self):
        note_id = self._hand_created_note()
        rc, out = run(["show", "--vault", self.vault, "--id", note_id])
        self.assertEqual(rc, 0, "NO-DATA is an honest answer, never a failure exit")
        self.assertIn("NO-DATA: INTAKE", out)
        self.assertIn("NO-DATA: EVENTS", out)
        self.assertIn("NO-DATA: CITATIONS", out)


class BackwardsTests(Fixture):
    def test_corrupted_citation_hash_reports_superseded_content(self):
        note_id = self._full_fixture()
        citations_path = self._citations_path()
        with open(citations_path, encoding="utf-8") as fh:
            rec = json.loads(fh.readline())
        rec["content_sha256"] = "0" * 64
        with open(citations_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

        rc, out = run(["show", "--vault", self.vault, "--id", note_id])
        self.assertEqual(rc, 0)
        self.assertIn("SUPERSEDED-CONTENT", out)

    def test_deleted_event_log_reports_no_data_naming_the_file(self):
        note_id = self._full_fixture()
        events_path = self._events_path()
        os.remove(events_path)

        rc, out = run(["show", "--vault", self.vault, "--id", note_id])
        self.assertEqual(rc, 0)
        self.assertIn("NO-DATA: EVENTS", out)
        self.assertIn(events_path, out)

    def test_unresolvable_id_exits_nonzero_naming_it(self):
        self._admit_one()
        bogus = "n-ffffffffffffffff"
        rc, out = run(["show", "--vault", self.vault, "--id", bogus])
        self.assertNotEqual(rc, 0)
        self.assertIn(bogus, out)


class JsonModeTests(Fixture):
    def test_json_matches_prose_verdicts_full_fixture(self):
        note_id = self._full_fixture()
        rc, out = run(["show", "--vault", self.vault, "--id", note_id, "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["note"]["id"], note_id)
        self.assertTrue(payload["intake"])
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(len(payload["citations"]), 1)
        self.assertEqual(payload["nodata"], [])

    def test_json_matches_prose_verdicts_empty_fixture(self):
        note_id = self._hand_created_note()
        rc, out = run(["show", "--vault", self.vault, "--id", note_id, "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["note"]["id"], note_id)
        self.assertIsNone(payload["intake"])
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["citations"], [])
        self.assertEqual(sorted(payload["nodata"]), ["CITATIONS", "EVENTS", "INTAKE"])


if __name__ == "__main__":
    unittest.main()
