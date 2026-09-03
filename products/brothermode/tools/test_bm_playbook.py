"""Calibration for tools/bm_playbook.py, in both directions (F7.4).

Every receipt seeded here goes through bm_recurrence.record_receipt() directly rather than a
hand-built sqlite row, so the fixture stays honest to the real receipts schema (same pattern
tools/test_bm_recurrence.py already uses). Assertions are on return values and exit codes, never
printed verdicts, per this estate's rule (see tools/test_bm_freshness.py's own docstring); file
existence and exact frontmatter content are checked as the artifact itself, not a substitute for
an exit-code assertion.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_recurrence as R  # noqa: E402

_spec = importlib.util.spec_from_file_location("bm_playbook", os.path.join(HERE, "bm_playbook.py"))
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)


class Contract(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name
        os.unlink(self.db)
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = os.path.join(self._tmp.name, "playbooks")

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)
        self._tmp.cleanup()

    def _seed(self, unit_id, lesson_id, before_first_write=True):
        R.record_receipt(unit_id, [lesson_id], [lesson_id], [], "", before_first_write, self.db)

    # -- candidates_for_promotion ------------------------------------------------

    def test_candidates_empty_on_a_fresh_db(self):
        self.assertEqual(bp.candidates_for_promotion(self.db), [])

    def test_candidates_below_threshold_are_absent(self):
        self._seed("u1", "L1")
        self._seed("u2", "L1")
        self.assertEqual(bp.candidates_for_promotion(self.db, min_recurrences=3), [])

    def test_candidates_lists_a_lesson_meeting_threshold(self):
        for u in ("u1", "u2", "u3"):
            self._seed(u, "L1")
        out = bp.candidates_for_promotion(self.db, min_recurrences=3)
        self.assertEqual(out, [{"lesson_id": "L1", "unit_ids": ["u1", "u2", "u3"],
                               "recurrences": 3}])

    def test_candidates_ignores_receipts_not_before_first_write(self):
        for u in ("u1", "u2", "u3"):
            self._seed(u, "L1", before_first_write=False)
        self.assertEqual(bp.candidates_for_promotion(self.db, min_recurrences=3), [])

    # -- promote(): below threshold refuses ---------------------------------------

    def test_below_threshold_refuses(self):
        self._seed("u1", "L1")
        self._seed("u2", "L1")
        with self.assertRaises(ValueError):
            bp.promote("L1", self.db, out_dir=self.out_dir, min_recurrences=3)
        self.assertFalse(os.path.exists(self.out_dir),
                         "promote() must not create the out_dir when it refuses")

    def test_below_threshold_cli_exits_nonzero_and_writes_nothing(self):
        self._seed("u1", "L1")
        out = io.StringIO()
        old = sys.stderr
        sys.stderr = out
        try:
            rc = bp.main(["promote", "--db", self.db, "--lesson", "L1",
                        "--out-dir", self.out_dir, "--min-recurrences", "3"])
        finally:
            sys.stderr = old
        self.assertEqual(rc, 1)
        self.assertIn("below threshold", out.getvalue())
        self.assertFalse(os.path.exists(self.out_dir))

    def test_missing_lesson_id_refuses_with_below_threshold_message(self):
        with self.assertRaises(ValueError) as ctx:
            bp.promote("no-such-lesson", self.db, out_dir=self.out_dir, min_recurrences=3)
        self.assertIn("below threshold", str(ctx.exception))

    # -- promote(): above threshold succeeds ---------------------------------------

    def test_above_threshold_promotes_with_exact_unit_ids(self):
        for u in ("u3", "u1", "u2"):
            self._seed(u, "L1")
        path = bp.promote("L1", self.db, out_dir=self.out_dir, min_recurrences=3)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("lesson_id: L1", text)
        self.assertIn("recurrences_prevented: 3", text)
        for line in text.splitlines():
            if line.startswith("unit_ids:"):
                got = json.loads(line[len("unit_ids:"):].strip())
                break
        else:
            self.fail("no unit_ids: line in %r" % text)
        self.assertEqual(got, ["u1", "u2", "u3"])

    def test_promotion_record_matches_candidates_for_promotion(self):
        """The moat clause: promote() must never disagree with candidates_for_promotion about
        which units back a lesson."""
        for u in ("u1", "u2", "u3", "u4"):
            self._seed(u, "L1")
        candidates = bp.candidates_for_promotion(self.db, min_recurrences=3)
        record = bp.promotion_record("L1", self.db)
        self.assertEqual(candidates[0]["unit_ids"], record)

    def test_above_threshold_cli_exits_zero(self):
        for u in ("u1", "u2", "u3"):
            self._seed(u, "L1")
        rc = bp.main(["promote", "--db", self.db, "--lesson", "L1",
                     "--out-dir", self.out_dir, "--min-recurrences", "3"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out_dir, "L1.md")))

    def test_candidates_cli_exits_zero_with_json(self):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = bp.main(["candidates", "--db", self.db])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), [])


if __name__ == "__main__":
    unittest.main()
