#!/usr/bin/env python3
"""Tests for the L0.1 decision and invariant gate (scripts/l0_gate.py).

Every fixture is built inside a TemporaryDirectory, so this suite never
reads the live repository, docs/plan, or the network, and it runs the
same in an export copy with an empty HOME. The tests assert the behaviour
this unit adds: without scripts/l0_gate.py the whole module fails to
import and every test here is an error.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import l0_gate  # noqa: E402  (sys.path set above)


GOOD_INVARIANT = "def main(argv=None):\n    return 0\n"


def _nodata_invariant(link):
    return ("def main(argv=None):\n"
            "    print('NO-DATA: %s (not a pass, and not a contradiction)')\n"
            "    return 0\n" % link)


class TestGate(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.status_path = self.root / "status.json"
        self.decisions_path = self.root / "decisions.json"
        self.waivers_path = self.root / "waivers.json"
        self.worktree = self.root / "worktree"
        (self.worktree / "scripts").mkdir(parents=True)
        self.invariant_path = self.worktree / "scripts" / "release_invariant.py"

    # ---------------------------------------------------------------- helpers

    def _write_json(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def _write_invariant(self, source):
        self.invariant_path.write_text(source, encoding="utf-8")

    def _status(self, units=None):
        if units is None:
            units = {uid: {"state": "BLOCKED"} for uid in l0_gate.EXPECTED_UNIT_IDS}
        return {
            "run_id": l0_gate.RUN_ID,
            "stamp_note": "test note",
            "generated_at": "2026-09-20T00:00:00+00:00",
            "head": "f752d6541c36b7a996d4a9a6a19bbe84b17d61d9",
            "units": units,
            "decisions_waiting": [],
            "decisions_recorded": [],
            "risks": [],
        }

    def _decision(self, decision_id, answer, waiver_id=None):
        return {
            "id": decision_id,
            "question": "question for %s" % decision_id,
            "answer": answer,
            "answered_by": None if answer is None else "founder",
            "answered_at": None if answer is None else "2026-09-20T00:00:00+00:00",
            "evidence_ref": None,
            "scope": l0_gate.DECISION_SCOPE,
            "waiver_id": waiver_id,
        }

    def _decisions(self, ans1="APPROVED", ans2="APPROVED", waiver1=None,
                   waiver2=None, unresolved=None):
        first = self._decision("release_invariant_failure", ans1, waiver1)
        second = self._decision("handover_pack_client_term", ans2, waiver2)
        nulls = sum(1 for a in (ans1, ans2) if a is None)
        return {
            "schema_version": "l0.founder-decisions.v1",
            "run_id": l0_gate.RUN_ID,
            "created_at": "2026-09-20T00:00:00+00:00",
            "decisions": [first, second],
            "unresolved_count": nulls if unresolved is None else unresolved,
        }

    def _waiver(self, waiver_id, scope, evidence_ref):
        return {
            "waiver_id": waiver_id,
            "scope": scope,
            "reason": "test waiver",
            "waived_by": "founder",
            "waived_at": "2026-09-20T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "evidence_ref": evidence_ref,
        }

    def _waivers(self, entries=None):
        return {
            "schema_version": "l0.waivers.v1",
            "run_id": l0_gate.RUN_ID,
            "created_at": "2026-09-20T00:00:00+00:00",
            "waivers": entries or [],
        }

    def _arm(self, decisions=None, waivers=None, status=None, invariant=None):
        self._write_json(self.status_path,
                         self._status() if status is None else status)
        self._write_json(self.decisions_path,
                         self._decisions() if decisions is None else decisions)
        self._write_json(self.waivers_path,
                         self._waivers() if waivers is None else waivers)
        self._write_invariant(GOOD_INVARIANT if invariant is None else invariant)

    def _run(self):
        return l0_gate.check_blocked(self.status_path, self.decisions_path,
                                     self.waivers_path, self.worktree)

    # ------------------------------------------------------------- pass paths

    def test_clean_inputs_pass(self):
        self._arm()
        self.assertEqual(self._run(), 0)

    def test_invariant_nodata_with_waiver_passes(self):
        waiver = self._waiver("w-nodata", "invariant_nodata", "link-one")
        self._arm(waivers=self._waivers([waiver]),
                  invariant=_nodata_invariant("link-one"))
        self.assertEqual(self._run(), 0)

    def test_deferred_with_waiver_passes(self):
        waiver = self._waiver("w-defer", "deferred_decision",
                              "release_invariant_failure")
        self._arm(decisions=self._decisions(ans1="DEFERRED", waiver1="w-defer"),
                  waivers=self._waivers([waiver]))
        self.assertEqual(self._run(), 0)

    # ------------------------------------------------- one test per finding

    def test_null_answer_blocks(self):
        self._arm(decisions=self._decisions(ans1=None))
        self.assertEqual(self._run(), 2)

    def test_deferred_without_waiver_blocks(self):
        self._arm(decisions=self._decisions(ans1="DEFERRED"))
        self.assertEqual(self._run(), 2)

    def test_invariant_exit_1_blocks(self):
        self._arm(invariant="def main(argv=None):\n    return 1\n")
        self.assertEqual(self._run(), 2)

    def test_invariant_exit_2_blocks(self):
        self._arm(invariant="def main(argv=None):\n    return 2\n")
        self.assertEqual(self._run(), 2)

    def test_invariant_nodata_blocks(self):
        self._arm(invariant=_nodata_invariant("link-one"))
        self.assertEqual(self._run(), 2)

    def test_corrupt_status_blocks(self):
        self.status_path.write_text("not json at all", encoding="utf-8")
        self._write_json(self.decisions_path, self._decisions())
        self._write_json(self.waivers_path, self._waivers())
        self._write_invariant(GOOD_INVARIANT)
        self.assertEqual(self._run(), 3)

    def test_run_release_invariant_exit_1_is_returned(self):
        self._write_invariant("def main(argv=None):\n    return 1\n")
        result = l0_gate.run_release_invariant(self.worktree)
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["stdout"], "")

    def test_run_release_invariant_missing_script_is_nodata(self):
        empty = self.root / "empty-worktree"
        empty.mkdir()
        result = l0_gate.run_release_invariant(empty)
        self.assertEqual(result["exit_code"], 2)

    # --------------------------------------------------------------- schema

    def test_missing_expected_unit_is_nodata(self):
        status = self._status()
        del status["units"]["OR-5"]
        self._arm(status=status)
        self.assertEqual(self._run(), 3)

    def test_extra_unit_is_nodata(self):
        status = self._status()
        status["units"]["EXTRA"] = {"state": "BLOCKED"}
        self._arm(status=status)
        self.assertEqual(self._run(), 3)

    def test_missing_top_key_is_nodata(self):
        status = self._status()
        del status["risks"]
        self._arm(status=status)
        self.assertEqual(self._run(), 3)

    def test_wrong_type_top_key_is_nodata(self):
        status = self._status()
        status["risks"] = "not a list"
        self._arm(status=status)
        self.assertEqual(self._run(), 3)

    def test_unresolved_count_mismatch_blocks(self):
        self._arm(decisions=self._decisions(unresolved=1))
        self.assertEqual(self._run(), 2)

    def test_deferred_with_expired_waiver_blocks(self):
        waiver = self._waiver("w-defer", "deferred_decision",
                              "release_invariant_failure")
        waiver["expires_at"] = "2000-01-01T00:00:00+00:00"
        self._arm(decisions=self._decisions(ans1="DEFERRED", waiver1="w-defer"),
                  waivers=self._waivers([waiver]))
        self.assertEqual(self._run(), 2)

    def test_missing_decisions_file_blocks(self):
        self._arm()
        self.decisions_path.unlink()
        self.assertEqual(self._run(), 2)

    def test_missing_waivers_file_blocks(self):
        self._arm()
        self.waivers_path.unlink()
        self.assertEqual(self._run(), 2)

    def test_corrupt_decisions_blocks(self):
        self._arm()
        self.decisions_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self._run(), 2)

    # -------------------------------------------------------------- capture

    def test_run_release_invariant_captures_stdout(self):
        self._write_invariant("def main(argv=None):\n"
                              "    print('hello from invariant')\n"
                              "    return 0\n")
        result = l0_gate.run_release_invariant(self.worktree)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello from invariant", result["stdout"])

    # --------------------------------------------------------- hostile input

    def test_hostile_path_arguments_are_refused(self):
        bad_values = (None, 123, 1.5, "a-string", b"bytes", ["list"],
                      {"dict": 1}, object())
        for bad in bad_values:
            with self.assertRaises(ValueError):
                l0_gate.load_status(bad)
            with self.assertRaises(ValueError):
                l0_gate.load_decisions(bad)
            with self.assertRaises(ValueError):
                l0_gate.load_waivers(bad)
            with self.assertRaises(ValueError):
                l0_gate.run_release_invariant(bad)
            with self.assertRaises(ValueError):
                l0_gate.check_blocked(bad, bad, bad, bad)

    def test_hostile_json_documents_are_refused(self):
        self.status_path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ValueError):
            l0_gate.load_status(self.status_path)

        self.status_path.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(ValueError):
            l0_gate.load_status(self.status_path)

        self.status_path.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            l0_gate.load_status(self.status_path)

        with self.assertRaises(ValueError):
            l0_gate.load_status(self.root)

        self.decisions_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            l0_gate.load_decisions(self.decisions_path)

        self.waivers_path.write_text('["waiver"]', encoding="utf-8")
        with self.assertRaises(ValueError):
            l0_gate.load_waivers(self.waivers_path)

    def test_nan_and_unhashable_answers_are_refused(self):
        self._arm()
        data = self._decisions()
        data["decisions"][0]["answer"] = float("nan")
        self._write_json(self.decisions_path, data)
        with self.assertRaises(ValueError):
            l0_gate.load_decisions(self.decisions_path)

        data = self._decisions()
        data["decisions"][0]["answer"] = ["APPROVED"]
        self._write_json(self.decisions_path, data)
        with self.assertRaises(ValueError):
            l0_gate.load_decisions(self.decisions_path)

    def test_bool_for_unresolved_count_is_refused(self):
        self._arm()
        data = self._decisions()
        data["unresolved_count"] = True
        self._write_json(self.decisions_path, data)
        with self.assertRaises(ValueError):
            l0_gate.load_decisions(self.decisions_path)

    def test_require_all_answered_refuses_hostile_types(self):
        with self.assertRaises(ValueError):
            l0_gate.require_all_answered(None, None)
        with self.assertRaises(ValueError):
            l0_gate.require_all_answered({}, None)
        with self.assertRaises(ValueError):
            l0_gate.require_all_answered({}, {})


if __name__ == "__main__":
    unittest.main()
