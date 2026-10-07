import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jev_catalogue_l6b3 as l6b3


class TestL6b3GatedExecution(unittest.TestCase):

    def _base_entry(self):
        return {
            "nn": 42,
            "qid": "vault_manifest_valid_json",
            "domain": "Vault",
            "state_cmd": "git rev-parse HEAD",
            "state_output_verbatim": "abc123",
            "question": "is manifest valid json",
            "true_criteria": "valid",
            "false_criteria": "invalid",
            "noul_value": 0.98,
            "holder_id": "jev-catalogue-42-vault-manifest-1790000000000-1001",
            "reservation_id": "jev-catalogue-42-vault-manifest-1790000000000-1001",
        }

    def test_refuse_on_missing_blocks(self):
        self.assertTrue(l6b3.refuse_on_missing(""))
        self.assertTrue(l6b3.refuse_on_missing("   "))
        self.assertFalse(l6b3.refuse_on_missing("some output"))

    def test_refuse_on_missing_refuses_bytes(self):
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.refuse_on_missing(b"bytes-not-utf8-\xff")

    def test_bind_requires_pair(self):
        """THE FIXTURE WAS THE BUG, so it is corrected here rather than the code being loosened to accept it.

        This RECONCILE row used to carry holder_id and estimated_cost. The real ledger writes NEITHER on a
        reconcile: measured 2026-09-21 over 567 real RECONCILE rows, none carried holder_id, and the cost field
        is actual_cost. bind_ledger filtered both row types by holder_id and so returned "" for every real pair
        in the estate, while this test stayed green against a shape the world never produces. Making the code
        accept estimated_cost on a reconcile would have kept the fiction alive and the function still broken.
        See scripts/test_jev_catalogue_ledger_binding.py, which pins the real shapes."""
        reserve_only = json.dumps({
            "type": "RESERVE", "holder_id": "h1",
            "reservation_id": "jev-catalogue-01-name-1-1",
            "estimated_cost": 0.001,
        })
        self.assertEqual(l6b3.bind_ledger("h1", reserve_only), "")
        pair = "\n".join([
            reserve_only,
            json.dumps({
                "type": "RECONCILE",
                "reservation_id": "jev-catalogue-01-name-1-1",
                "actual_cost": 0.001,
            }),
        ])
        self.assertEqual(l6b3.bind_ledger("h1", pair), "jev-catalogue-01-name-1-1")

    def test_bind_rejects_wrong_cost(self):
        pair = "\n".join([
            json.dumps({
                "type": "RESERVE", "holder_id": "h1",
                "reservation_id": "jev-catalogue-01-name-1-1",
                "estimated_cost": 0.002,
            }),
            json.dumps({
                "type": "RECONCILE", "holder_id": "h1",
                "reservation_id": "jev-catalogue-01-name-1-1",
                "estimated_cost": 0.002,
            }),
        ])
        self.assertEqual(l6b3.bind_ledger("h1", pair), "")

    def test_timeout_floor_prevents(self):
        good = {"state": "x", "questions": {"q": {"type": "noul"}}}
        holder = "jev-catalogue-01-name-1-1"
        with self.assertRaises(l6b3.GatedCallRefused):
            l6b3.run_gated_call(good, holder, 299)
        out = l6b3.run_gated_call(good, holder, 300)
        self.assertEqual(out["built"]["timeout"], 300)
        self.assertEqual(out["built"]["estimated_cost"], 0.001)
        self.assertEqual(out["built"]["type"], "noul")

    def test_run_gated_refuses_score_type(self):
        bad = {"state": "x", "questions": {"q": {"type": "score"}}}
        with self.assertRaises(l6b3.GatedCallRefused):
            l6b3.run_gated_call(bad, "jev-catalogue-01-name-1-1", 300)

    def test_run_gated_refuses_holder_path(self):
        good = {"state": "x", "questions": {"q": {"type": "noul"}}}
        with self.assertRaises(l6b3.GatedCallRefused):
            l6b3.run_gated_call(good, "../x", 300)

    def test_abstain_band(self):
        self.assertEqual(l6b3.audit_abstain(0.7), "abstain")
        self.assertEqual(l6b3.audit_abstain(0.98), "yes")
        self.assertEqual(l6b3.audit_abstain(0.05), "no")
        self.assertEqual(l6b3.audit_abstain(None), "NO-DATA")

    def test_audit_inf_is_no_data(self):
        self.assertEqual(l6b3.audit_abstain(float("inf")), "NO-DATA")
        self.assertEqual(l6b3.audit_abstain(float("nan")), "NO-DATA")

    def test_validate_rejects_holder_path(self):
        e = self._base_entry()
        e["holder_id"] = "../x"
        self.assertFalse(l6b3.validate_entry(e))

    def test_validate_rejects_noul_range(self):
        for bad in (2, -1, float("inf"), float("nan")):
            e = self._base_entry()
            e["noul_value"] = bad
            self.assertFalse(l6b3.validate_entry(e))

    def test_validate_rejects_qid_path(self):
        e = self._base_entry()
        e["qid"] = "a/b"
        self.assertFalse(l6b3.validate_entry(e))

    def test_validate_rejects_reservation_path(self):
        e = self._base_entry()
        e["reservation_id"] = "../x"
        self.assertFalse(l6b3.validate_entry(e))

    def test_validate_rejects_corrupt_state_output(self):
        e = self._base_entry()
        e["state_output_verbatim"] = "   "
        self.assertFalse(l6b3.validate_entry(e))

    def test_parity_end_to_end_70(self):
        cat = []
        led = []
        for i in range(1, 71):
            rid = "jev-catalogue-{:02d}-test-{}-{}".format(i, 1000 + i, i)
            hid = "jev-catalogue-{:02d}-test".format(i)
            cat.append("Ledger: `{}`".format(rid))
            led.append(json.dumps({
                "type": "RESERVE", "holder_id": hid,
                "reservation_id": rid, "estimated_cost": 0.001,
            }))
            led.append(json.dumps({
                "type": "RECONCILE", "holder_id": hid,
                "reservation_id": rid, "estimated_cost": 0.001,
            }))
        catalogue_text = "\n".join(cat)
        ledger_text = "\n".join(led)
        self.assertEqual(l6b3.count_markers(catalogue_text), 70)
        self.assertEqual(l6b3.recount_unique_ids(catalogue_text), 70)
        self.assertTrue(l6b3.verify_parity(catalogue_text, ledger_text))
        broken = []
        removed = False
        for ln in led:
            o = json.loads(ln)
            if not removed and o["type"] == "RECONCILE":
                removed = True
                continue
            broken.append(ln)
        self.assertFalse(l6b3.verify_parity(catalogue_text, "\n".join(broken)))

    def test_verify_parity_rejects_path_rid(self):
        cat = "Ledger: `../x`"
        led = ""
        self.assertFalse(l6b3.verify_parity(cat, led))

    def test_spend_tolerance(self):
        count = 10
        led = []
        for i in range(count):
            led.append(json.dumps({
                "type": "RESERVE", "holder_id": "h{}".format(i),
                "reservation_id": "r{}".format(i),
                "estimated_cost": 0.001,
            }))
        spend = l6b3.current_spend_check("\n".join(led))
        self.assertAlmostEqual(spend, 0.001 * count, delta=0.0001)

    def test_append_entry_parity_roundtrip(self):
        entry = self._base_entry()
        empty = ""
        after = l6b3.append_entry(empty, entry)
        self.assertEqual(l6b3.count_markers(after), 1)
        self.assertEqual(l6b3.recount_unique_ids(after), 1)
        again = l6b3.append_entry(after, entry)
        self.assertEqual(again, after)

    def test_append_entry_rejects_entry_mutated_after_validate(self):
        e = self._base_entry()
        self.assertTrue(l6b3.validate_entry(e))
        e["noul_value"] = 2
        before = "seed\n"
        after = l6b3.append_entry(before, e)
        self.assertEqual(after, before)

    def test_hostile_types_raise(self):
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.count_markers(None)
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.count_markers(b"bytes")
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.recount_unique_ids(True)
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.verify_parity(None, "x")
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.verify_parity("x", None)
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.bind_ledger(123, "ledger")
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.bind_ledger("h", None)
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.append_entry(None, {})
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.current_spend_check(None)
        with self.assertRaises(l6b3.CatalogueRefused):
            l6b3.audit_abstain("0.5")
        with self.assertRaises(l6b3.GatedCallRefused):
            l6b3.run_gated_call({}, "h", True)
        with self.assertRaises(l6b3.GatedCallRefused):
            l6b3.run_gated_call({}, "h", "300")
        with self.assertRaises(l6b3.GatedCallRefused):
            l6b3.run_gated_call([], "h", 300)


if __name__ == "__main__":
    unittest.main()
