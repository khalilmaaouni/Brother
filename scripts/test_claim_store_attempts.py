"""D1.1: the append-only attempt history in scripts/claim_store.py.

Everything a unit has ever run is recorded once and never lost; a write that
would erase an attempt or rewrite a terminal field is refused at the source,
in _write, before os.replace. The lease-expiry takeover contract
scripts/test_crash_resume.py already depends on is repeated here with the
attempt history switched on, so this sub unit can never regress it.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import claim_store as C  # noqa: E402


def _store():
    return os.path.join(tempfile.mkdtemp(prefix="attempts-"), "claims.json")


class TheAttemptHistoryIsAppendOnly(unittest.TestCase):

    def test_two_takes_in_a_row_are_two_attempts_and_the_second_names_the_first(self):
        path = _store()
        a, problem = C.acquire(path, "U1", "owner-a", parent_attempt_id=None,
                               ready_set_fingerprint="f" * 64)
        self.assertEqual(problem, "")
        self.assertTrue(a and a["attempt_id"])
        b, problem = C.acquire(path, "U1", "owner-a",
                               parent_attempt_id=a["attempt_id"],
                               ready_set_fingerprint="f" * 64)
        self.assertEqual(problem, "")
        self.assertNotEqual(a["attempt_id"], b["attempt_id"])
        records, problem = C.history(path, "U1")
        self.assertEqual(problem, "")
        self.assertEqual([r["attempt"] for r in records], [1, 2])
        self.assertEqual(records[1]["parent_attempt_id"], a["attempt_id"])
        self.assertEqual(C.attempt_ids(path, "U1"),
                         (a["attempt_id"], b["attempt_id"]))
        tip, problem = C.head(path, "U1")
        self.assertEqual(problem, "")
        self.assertEqual(tip["attempt_id"], b["attempt_id"])
        self.assertTrue(tip["live"])

    def test_a_release_terminates_the_attempt_record_too(self):
        path = _store()
        claim, problem = C.acquire(path, "U1", "owner-a")
        self.assertEqual(problem, "")
        held, problem = C.release(path, "U1", "owner-a", state="done",
                                  evidence={"exit_code": 0},
                                  executed_lane="lane-x")
        self.assertEqual(problem, "")
        self.assertEqual(held["state"], "done")
        records, problem = C.history(path, "U1")
        self.assertEqual(problem, "")
        top = records[-1]
        self.assertEqual(top["attempt_id"], claim["attempt_id"])
        self.assertEqual(top["state"], "done")
        self.assertEqual(top["evidence"], {"exit_code": 0})
        self.assertEqual(top["executed_lane"], "lane-x")
        self.assertIn("released_at", top)

    def test_a_write_that_would_drop_an_attempt_is_refused_before_os_replace(self):
        path = _store()
        C.acquire(path, "U1", "owner-a")
        C.acquire(path, "U1", "owner-a")
        with open(path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        tampered = json.loads(json.dumps(on_disk))
        tampered["attempts"]["U1"] = tampered["attempts"]["U1"][:1]
        with self.assertRaises(C.HistoryLoss):
            C._write(path, tampered)
        with open(path, encoding="utf-8") as fh:
            still = json.load(fh)
        self.assertEqual(len(still["attempts"]["U1"]), 2)

    def test_a_write_that_would_rewrite_a_terminal_field_is_refused(self):
        path = _store()
        C.acquire(path, "U1", "owner-a")
        C.release(path, "U1", "owner-a", state="done")
        with open(path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        tampered = json.loads(json.dumps(on_disk))
        tampered["attempts"]["U1"][0]["state"] = "claimed"
        with self.assertRaises(C.HistoryLoss):
            C._assert_append_only(on_disk, tampered)

    def test_a_unit_with_no_recorded_attempt_reads_no_data_not_an_empty_record(self):
        path = _store()
        records, problem = C.history(path, "U1")
        self.assertEqual(records, ())
        self.assertEqual(problem, "")
        tip, problem = C.head(path, "U1")
        self.assertIsNone(tip)
        self.assertEqual(problem, C.NODATA)
        self.assertEqual(C.attempt_ids(path, "U1"), ())

    def test_an_unreadable_store_refuses_rather_than_reads_as_empty(self):
        path = _store()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        records, problem = C.history(path, "U1")
        self.assertEqual(records, ())
        self.assertNotEqual(problem, "")
        tip, problem = C.head(path, "U1")
        self.assertIsNone(tip)
        self.assertNotEqual(problem, "")
        self.assertEqual(C.attempt_ids(path, "U1"), ())
        claim, problem = C.acquire(path, "U1", "owner-a")
        self.assertIsNone(claim)
        self.assertNotEqual(problem, "")

    def test_hostile_inputs_are_refused_and_never_raise_type_error(self):
        path = _store()
        for bad in (None, 17, ["U1"], {"id": "U1"}, True, b"U1"):
            claim, problem = C.acquire(path, bad, "owner-a")
            self.assertIsNone(claim)
            self.assertNotEqual(problem, "")
            records, problem = C.history(path, bad)
            self.assertEqual(records, ())
            self.assertNotEqual(problem, "")
            self.assertEqual(C.attempt_ids(path, bad), ())
            self.assertIsNone(C.head(path, bad)[0])
        for bad_owner in (None, 5, ["owner"]):
            claim, problem = C.acquire(path, "U1", bad_owner)
            self.assertIsNone(claim)
            self.assertNotEqual(problem, "")
        claim, problem = C.acquire(path, "U1", "owner-a", attempt=True)
        self.assertIsNone(claim)
        self.assertNotEqual(problem, "")
        claim, problem = C.acquire(path, "U1", "owner-a", parent_attempt_id=7)
        self.assertIsNone(claim)
        self.assertNotEqual(problem, "")
        for reserved in ("attempts", "unit_attempts", "schema"):
            claim, problem = C.acquire(path, reserved, "owner-a")
            self.assertIsNone(claim)
            self.assertNotEqual(problem, "")
        self.assertEqual(C.history(path, "U1"), ((), ""))
        self.assertFalse(os.path.exists(path))


class TheExpiryTakeoverSurvivesTheHistory(unittest.TestCase):
    """The contract scripts/test_crash_resume.py's
    test_after_expiry_another_owner_may_take_over_and_says_so already
    proves, repeated with D1.1's attempt history in place. An expired lease
    MUST hand over to another owner, and the takeover MUST name whose work
    it inherits."""

    def test_after_expiry_another_owner_takes_over_with_history_in_place(self):
        path = _store()
        first, problem = C.acquire(path, "CR1", "crash-A")
        self.assertEqual(problem, "")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for value in data.values():
            if isinstance(value, dict) and "state" in value:
                value["expires_at"] = 0
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        found, problem = C.reconcile(path)
        self.assertEqual(problem, "")
        self.assertEqual([f["status"] for f in found], ["abandoned"])
        taken, problem = C.acquire(path, "CR1", "successor")
        self.assertIsNotNone(taken, problem)
        self.assertEqual(problem, "")
        self.assertEqual(taken["reclaimed_from"], "crash-A")
        records, problem = C.history(path, "CR1")
        self.assertEqual(problem, "")
        self.assertEqual([r["attempt"] for r in records], [1, 2])
        self.assertEqual(records[0]["attempt_id"], first["attempt_id"])
        self.assertEqual(records[1]["owner"], "successor")


if __name__ == "__main__":
    unittest.main()
