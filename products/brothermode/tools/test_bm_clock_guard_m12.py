"""M1.2 guard decision on evidence: parse_clock_evidence, decide, format_block."""
import json
import os
import sys
import unittest

# The folder's own import form (as test_bm_repair_d16 beside this file): tools/test_all.py runs each suite as a
# script from this folder, where the repository root package does not exist. The hub rows still import the same file.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from bm_clock_guard import (  # noqa: E402
    CLOCK_COMMAND_ALLOWLIST,
    CLOCK_RESULT_FIELDS,
    decide,
    format_block,
    parse_clock_evidence,
    to_minutes,
)


def _tool_result(command, **fields):
    record = {"type": "tool_result", "tool_use_id": "toolu_1", "command": command}
    record.update(fields)
    return json.dumps(record)


class TestGuardEvidence(unittest.TestCase):
    def test_parse_clock_evidence_direct(self):
        text = "\n".join([
            _tool_result("date", stdout="Mon 2026-09-20 14:22:11 UTC"),
            _tool_result("date +%H:%M", stdout="09:41"),
            _tool_result("clock", text="23:59"),
            "this is not json",
            _tool_result("grep -r hm .", stdout="14:22"),
        ])
        minutes = parse_clock_evidence(text)
        self.assertIn(to_minutes(14, 22), minutes)
        self.assertIn(to_minutes(9, 41), minutes)
        self.assertIn(to_minutes(23, 59), minutes)
        self.assertEqual(minutes.count(to_minutes(14, 22)), 1)
        self.assertNotIn(to_minutes(20, 26), minutes)
        self.assertNotIn(to_minutes(9, 20), minutes)

    def test_parse_clock_evidence_rejects_unallowlisted(self):
        self.assertEqual(
            parse_clock_evidence(_tool_result("cat notes.txt", stdout="meet at 14:22")), []
        )
        self.assertEqual(parse_clock_evidence(_tool_result("date")), [])
        self.assertEqual(parse_clock_evidence(_tool_result("date", notes="14:22")), [])
        self.assertEqual(
            parse_clock_evidence(
                json.dumps({"type": "user", "command": "date", "stdout": "14:22"})
            ),
            [],
        )

    def test_result_fields_accepted(self):
        for field in CLOCK_RESULT_FIELDS:
            record = _tool_result("date", **{field: "read at 07:05"})
            self.assertEqual(parse_clock_evidence(record), [to_minutes(7, 5)])

    def test_allowlist_commands_accepted(self):
        for command in CLOCK_COMMAND_ALLOWLIST:
            record = _tool_result(command, stdout="read at 07:05")
            self.assertEqual(parse_clock_evidence(record), [to_minutes(7, 5)])

    def test_no_time_allows(self):
        self.assertEqual(decide("no times here", []), ("ALLOW", "NO-TIME"))
        self.assertEqual(decide("", [to_minutes(9, 0)]), ("ALLOW", "NO-TIME"))

    def test_estimate_only_allows(self):
        self.assertEqual(decide("meet at 14:22 estimate", []), ("ALLOW", "ESTIMATE-ONLY"))

    def test_block_without_evidence(self):
        self.assertEqual(decide("deploy at 14:22", []), ("BLOCK", "NO-DATA"))

    def test_allow_within_two_minutes(self):
        base = to_minutes(14, 22)
        self.assertEqual(decide("deploy at 14:22", [base]), ("ALLOW", "OK"))
        self.assertEqual(decide("deploy at 14:22", [base + 1]), ("ALLOW", "OK"))
        self.assertEqual(decide("deploy at 14:22", [base + 2]), ("ALLOW", "OK"))
        self.assertEqual(decide("deploy at 14:22", [base - 2]), ("ALLOW", "OK"))
        self.assertEqual(decide("deploy at 14:22", [base + 3]), ("BLOCK", "MISMATCH"))
        self.assertEqual(decide("deploy at 14:22", [base + 30]), ("BLOCK", "MISMATCH"))

    def test_midnight_wrap(self):
        self.assertEqual(decide("deploy at 00:00", [to_minutes(23, 59)]), ("ALLOW", "OK"))
        self.assertEqual(decide("deploy at 23:59", [to_minutes(0, 0)]), ("ALLOW", "OK"))

    def test_mismatch_lists_times(self):
        text = "start 09:41 then 14:22"
        self.assertEqual(decide(text, [to_minutes(9, 41)]), ("BLOCK", "MISMATCH"))
        self.assertEqual(
            decide(text, [to_minutes(9, 41), to_minutes(14, 22)]), ("ALLOW", "OK")
        )
        payload = json.loads(format_block("MISMATCH", ["14:22"]))
        self.assertEqual(payload["decision"], "BLOCK")
        self.assertEqual(payload["reason"], "MISMATCH")
        self.assertEqual(payload["times"], ["14:22"])
        self.assertNotIn("09:41", payload["times"])

    def test_hostile_input_refused(self):
        for bad in (None, 4, [], True, b"14:22"):
            with self.assertRaises(ValueError):
                parse_clock_evidence(bad)
        for bad in (None, 4, [], True, b"14:22"):
            with self.assertRaises(ValueError):
                decide(bad, [])
        for bad in (None, "1", {}, True, 3.5):
            with self.assertRaises(ValueError):
                decide("14:22", bad)
        for bad in ([None], ["9:41"], [1.5], [True], [float("nan")]):
            with self.assertRaises(ValueError):
                decide("14:22", bad)
        with self.assertRaises(ValueError):
            format_block(None, [])
        with self.assertRaises(ValueError):
            format_block("MISMATCH", None)
        with self.assertRaises(ValueError):
            format_block("MISMATCH", [1])


if __name__ == "__main__":
    unittest.main()
