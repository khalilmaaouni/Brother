"""Footprint prefilter in parse_clock_evidence: lines that cannot decode to a
tool_result record are skipped before json.loads, and the result set is the
same as a full parse (a \\u escaped spelling is still parsed)."""
import json
import os
import sys
import unittest
from unittest import mock

# The folder's own import form (as test_bm_repair_d16 beside this file): tools/test_all.py runs each suite as a
# script from this folder, where the repository root package does not exist. The hub rows still import the same file.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bm_clock_guard  # noqa: E402
from bm_clock_guard import parse_clock_evidence, to_minutes  # noqa: E402


def _tool_result(command, **fields):
    record = {"type": "tool_result", "tool_use_id": "toolu_1", "command": command}
    record.update(fields)
    return json.dumps(record)


class TestPrefilter(unittest.TestCase):
    def test_plain_and_escaped_spellings_both_parse(self):
        plain = _tool_result("date", stdout="09:41")
        escaped = plain.replace("tool_result", "tool\\u005fresult")
        self.assertNotIn("tool_result", escaped)
        self.assertEqual(parse_clock_evidence(plain), [to_minutes(9, 41)])
        self.assertEqual(parse_clock_evidence(escaped), [to_minutes(9, 41)])

    def test_non_candidate_lines_never_reach_json_loads(self):
        text = "\n".join([
            json.dumps({"type": "user", "message": "meet at 14:22"}),
            json.dumps({"type": "assistant", "text": "date printed 09:41"}),
            _tool_result("date", stdout="07:05"),
            "not json at all 12:00",
        ])
        real_loads = json.loads
        with mock.patch.object(bm_clock_guard.json, "loads", side_effect=real_loads) as loads:
            minutes = parse_clock_evidence(text)
        self.assertEqual(minutes, [to_minutes(7, 5)])
        self.assertEqual(loads.call_count, 1, "only the one candidate line may be parsed")


if __name__ == "__main__":
    unittest.main()
