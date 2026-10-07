#!/usr/bin/env python3
"""bind_ledger must bind against the ledger this estate ACTUALLY writes, not against a fixture.

MEASURED 2026-09-21 on the live ledger at ~/.claude/brother-or-dispatch-state/openrouter-ledger.jsonl:
616 RESERVE rows, ALL carrying holder_id; 567 RECONCILE rows, NONE carrying holder_id. A real RECONCILE row is
  {"type": "RECONCILE", "reservation_id": "...", "actual_cost": 84.21, "at": 1789959349.635}

bind_ledger filtered BOTH row types by holder_id, so the reconcile half could never match and the function
returned "" for every real pair. It passed its own suite because that suite builds a fixture carrying holder_id
on both rows. Same green-but-hollow class the L6b done check was written to catch, found by a worker rather than
by a check, which is why this file exists.

Three independent reasons it could never bind a real pair, all three fixed together:
  1. holder_id is absent from every RECONCILE row
  2. the cost field on a RECONCILE is actual_cost, never estimated_cost
  3. the id regex is end anchored and the live dispatcher appends a uuid4 tail
     (added 2026-09-20 after two reservations collided in one millisecond)
Fixing any one alone leaves the function still returning "" on real input."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jev_catalogue_l6b3 as m  # noqa: E402

HOLDER = "jev-catalogue-11-probe"
REAL_ID = HOLDER + "-1789965154330-21936-e4a00bd1939e"      # the live shape, with its uuid4 tail
OLD_ID = "jev-catalogue-02-q02-1789856477984-4076"          # the shape before the collision fix


def _ledger(reserve, reconcile):
    return "\n".join(json.dumps(r) for r in (reserve, reconcile)) + "\n"


class BindsTheLedgerThisEstateWrites(unittest.TestCase):
    def test_a_real_pair_binds(self):
        """THE CASE THAT FAILED. Reconcile carries no holder_id and uses actual_cost, exactly as on disk."""
        text = _ledger(
            {"type": "RESERVE", "reservation_id": REAL_ID, "holder_id": HOLDER,
             "estimated_cost": 0.001, "at": 1.0},
            {"type": "RECONCILE", "reservation_id": REAL_ID, "actual_cost": 0.001, "at": 2.0})
        self.assertEqual(m.bind_ledger(HOLDER, text), REAL_ID)

    def test_the_older_id_shape_still_binds(self):
        """The uuid4 tail was added mid life; ids written before it must not stop binding."""
        text = _ledger(
            {"type": "RESERVE", "reservation_id": OLD_ID, "holder_id": "jev-catalogue-02-q02",
             "estimated_cost": 0.001, "at": 1.0},
            {"type": "RECONCILE", "reservation_id": OLD_ID, "actual_cost": 0.001, "at": 2.0})
        self.assertEqual(m.bind_ledger("jev-catalogue-02-q02", text), OLD_ID)

    def test_a_reserve_with_no_reconcile_does_not_bind(self):
        """An unreconciled reservation is a call still in flight or one that crashed. Binding it would count
        money that was never spent as evidence that it was."""
        text = json.dumps({"type": "RESERVE", "reservation_id": REAL_ID, "holder_id": HOLDER,
                           "estimated_cost": 0.001, "at": 1.0}) + "\n"
        self.assertEqual(m.bind_ledger(HOLDER, text), "")

    def test_another_holders_pair_does_not_bind(self):
        text = _ledger(
            {"type": "RESERVE", "reservation_id": REAL_ID, "holder_id": "somebody-else",
             "estimated_cost": 0.001, "at": 1.0},
            {"type": "RECONCILE", "reservation_id": REAL_ID, "actual_cost": 0.001, "at": 2.0})
        self.assertEqual(m.bind_ledger(HOLDER, text), "")

    def test_a_reconcile_for_a_different_reservation_does_not_bind(self):
        """The reconcile must name THIS reservation, not merely be present in the file."""
        text = _ledger(
            {"type": "RESERVE", "reservation_id": REAL_ID, "holder_id": HOLDER,
             "estimated_cost": 0.001, "at": 1.0},
            {"type": "RECONCILE", "reservation_id": "some-other-id-1-2", "actual_cost": 0.001, "at": 2.0})
        self.assertEqual(m.bind_ledger(HOLDER, text), "")

    def test_a_corrupt_line_is_skipped_not_fatal(self):
        text = ("not json at all\n" + _ledger(
            {"type": "RESERVE", "reservation_id": REAL_ID, "holder_id": HOLDER,
             "estimated_cost": 0.001, "at": 1.0},
            {"type": "RECONCILE", "reservation_id": REAL_ID, "actual_cost": 0.001, "at": 2.0}))
        self.assertEqual(m.bind_ledger(HOLDER, text), REAL_ID)

    def test_hostile_input_is_refused_never_crashes(self):
        for bad in (None, 1, [], {}, True):
            with self.assertRaises(m.CatalogueRefused):
                m.bind_ledger(bad, "")
            with self.assertRaises(m.CatalogueRefused):
                m.bind_ledger(HOLDER, bad)


if __name__ == "__main__":
    unittest.main(verbosity=1)
