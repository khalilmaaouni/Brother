#!/usr/bin/env python3
"""A landing's evidence write never erases, and never crashes on, evidence another writer added meanwhile.

WHY. land_batch read the plan minutes before it wrote the landing's evidence line, then handed plan_store its stale
copy of the unit. Since plan_store refuses a record whose evidence does not extend the evidence on disk (lane G,
2026-09-27), a closer or a second landing writing in that window turned the landing into a ValueError crash with the
build's edits applied and uncommitted. land_batch.landing_records gives plan_store a callable that appends this
landing's lines to the unit RE-READ UNDER THE LOCK, so both writers' evidence survives.

Run: python3 -B scripts/test_land_batch_evidence_race.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import land_batch as LB  # noqa: E402
import plan_store  # noqa: E402


class LandingEvidenceUnderARace(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="lb-evidence-race-")
        self.addCleanup(t.cleanup)
        self.plan = os.path.join(t.name, "plan.json")
        self.lock = os.path.join(t.name, "plan.lock")
        with open(self.plan, "w") as fh:
            json.dump({"units": [{"id": "A", "sub_units": ["A.1", "A.2"], "evidence": "start."}]}, fh)

    def concurrent_writer(self, text):
        plan_store.update_units(self.plan, {"A": {"id": "A", "evidence": "start." + text}},
                                lock_path=self.lock, fields=("evidence",))

    def evidence(self):
        with open(self.plan) as fh:
            return json.load(fh)["units"][0]["evidence"]

    def test_a_concurrent_writers_evidence_survives_the_landing(self):
        self.concurrent_writer(" A.2 closer receipt.")
        plan_store.update_units(self.plan, LB.landing_records({"A": [" A.1 landed today."]}),
                                lock_path=self.lock, fields=("evidence",))
        self.assertEqual(self.evidence(), "start. A.2 closer receipt. A.1 landed today.")

    def test_two_lines_for_one_unit_append_in_order(self):
        plan_store.update_units(self.plan, LB.landing_records({"A": [" A.1 landed.", " A.2 landed."]}),
                                lock_path=self.lock, fields=("evidence",))
        self.assertEqual(self.evidence(), "start. A.1 landed. A.2 landed.")

    def test_the_stale_copy_it_replaces_would_have_crashed(self):
        # The shape land_batch used before: its own copy of the unit, read before the concurrent write.
        stale = {"id": "A", "sub_units": ["A.1", "A.2"], "evidence": "start. A.1 landed today."}
        self.concurrent_writer(" A.2 closer receipt.")
        with self.assertRaises(ValueError):
            plan_store.update_units(self.plan, {"A": stale}, lock_path=self.lock, fields=("evidence",))


class RefusedLandingTakesBackOnlyItsOwnLines(unittest.TestCase):
    """X1 finding 3 (2026-09-27): a refused landing restored the whole plan with an unlocked git checkout, erasing
    evidence another writer appended under the plan lock meanwhile. plan_store.undo_appended removes, under the same
    lock, exactly what this landing appended (landing_records records what it re-read and what it wrote) and nothing
    else; a unit whose evidence no longer carries this landing's write where it made it is named and left alone."""

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="lb-evidence-undo-")
        self.addCleanup(t.cleanup)
        self.plan = os.path.join(t.name, "plan.json")
        self.lock = os.path.join(t.name, "plan.lock")
        plan = {"units": [{"id": "A", "state": "OPEN", "evidence": "start. "}, {"id": "B", "state": "OPEN", "evidence": "b."}]}
        with open(self.plan, "w", encoding="utf-8") as fh:   # the plan_store format, so a byte comparison is fair
            fh.write(json.dumps(plan, indent=1) + "\n")

    def land(self, lines):
        seen = {}
        plan_store.update_units(self.plan, LB.landing_records(lines, seen), lock_path=self.lock, fields=("evidence",))
        return seen

    def other_writer(self, uid, **change):
        plan_store.update_units(self.plan, {uid: lambda u: dict(u, **{k: (u.get(k) or "") + v if k == "evidence" else v
                                                                      for k, v in change.items()})},
                                lock_path=self.lock)

    def unit(self, uid):
        with open(self.plan, encoding="utf-8") as fh:
            return next(u for u in json.load(fh)["units"] if u["id"] == uid)

    def raw(self):
        with open(self.plan, "rb") as fh:
            return fh.read()

    def test_with_no_other_writer_the_plan_is_back_byte_for_byte(self):
        before = self.raw()
        seen = self.land({"A": [" A.1 landed.", " A.2 landed."]})
        self.assertEqual(plan_store.undo_appended(self.plan, seen, lock_path=self.lock), [])
        self.assertEqual(self.raw(), before)

    def test_a_later_writers_evidence_stays(self):
        seen = self.land({"A": [" A.1 landed."]})
        self.other_writer("A", evidence=" A.2 closer receipt.")
        self.assertEqual(plan_store.undo_appended(self.plan, seen, lock_path=self.lock), [])
        self.assertEqual(self.unit("A")["evidence"], "start.  A.2 closer receipt.")

    def test_another_unit_and_other_fields_are_never_touched(self):
        seen = self.land({"A": [" A.1 landed."]})
        self.other_writer("A", state="DONE")
        self.other_writer("B", evidence=" B.1 landed.")
        plan_store.undo_appended(self.plan, seen, lock_path=self.lock)
        self.assertEqual((self.unit("A"), self.unit("B")),
                         ({"id": "A", "state": "DONE", "evidence": "start. "}, {"id": "B", "state": "OPEN", "evidence": "b. B.1 landed."}))

    def test_a_write_no_longer_where_it_was_made_is_named_and_nothing_is_written(self):
        seen = self.land({"A": [" A.1 landed."]})
        with open(self.plan, "w", encoding="utf-8") as fh:   # the unit rewritten by hand since this landing wrote it
            fh.write(json.dumps({"units": [{"id": "A", "state": "OPEN", "evidence": "rewritten."},
                                           {"id": "B", "state": "OPEN", "evidence": "b."}]}, indent=1) + "\n")
        before = self.raw()
        self.assertEqual(plan_store.undo_appended(self.plan, seen, lock_path=self.lock), ["A"])
        self.assertEqual(self.raw(), before)

    def test_committed_bytes_in_another_format_come_back_byte_for_byte(self):
        committed = json.dumps({"units": [{"id": "A", "state": "OPEN", "evidence": "start. "},
                                          {"id": "B", "state": "OPEN", "evidence": "b."}]}).encode()   # no indent, no newline
        with open(self.plan, "wb") as fh:
            fh.write(committed)
        seen = self.land({"A": [" A.1 landed."]})
        self.assertEqual(plan_store.undo_appended(self.plan, seen, lock_path=self.lock, committed=committed), [])
        self.assertEqual(self.raw(), committed)

    def test_committed_bytes_are_never_written_over_another_writers_evidence(self):
        committed = self.raw()
        seen = self.land({"A": [" A.1 landed."]})
        self.other_writer("B", evidence=" B.1 landed.")
        plan_store.undo_appended(self.plan, seen, lock_path=self.lock, committed=committed)
        self.assertEqual((self.unit("A")["evidence"], self.unit("B")["evidence"]), ("start. ", "b. B.1 landed."))

    def test_committed_bytes_that_do_not_parse_are_never_written(self):
        seen = self.land({"A": [" A.1 landed."]})
        plan_store.undo_appended(self.plan, seen, lock_path=self.lock, committed=b"{not json")
        self.assertEqual(self.unit("A")["evidence"], "start. ")

    def test_a_unit_the_plan_no_longer_holds_is_named(self):
        seen = self.land({"A": [" A.1 landed."]})
        self.assertEqual(plan_store.undo_appended(self.plan, dict(seen, Z=("x", "x y")), lock_path=self.lock), ["Z"])
        self.assertEqual(self.unit("A")["evidence"], "start. ")


if __name__ == "__main__":
    unittest.main()
