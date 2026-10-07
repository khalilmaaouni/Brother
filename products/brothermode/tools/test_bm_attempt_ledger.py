"""What products/brothermode/tools/bm_attempt_ledger.py must keep true.

J1.c: write down what compaction loses, delete nothing. These tests assert
the BEHAVIOUR the module adds, never that a name merely exists. Hostile
input is refused with ValueError, never a raw interpreter exception, never
a silent accept. When the module under test is absent the import fails and
the suite is RED, which is the required red-without-code property.
"""
import os
import sys
import unittest

# The folder's own import form (as test_bm_repair_d16 beside this file): tools/test_all.py runs each suite as a
# script from this folder, where the repository root package does not exist. The hub rows still import the same file.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bm_attempt_ledger as L  # noqa: E402


def _tool_use(index, cid, name, inp):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": cid, "name": name, "input": inp}
            ]
        },
    }


def _tool_result(cid, content, is_error=False):
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": cid,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }


class PairCallsTests(unittest.TestCase):
    def test_call_joined_to_result_by_id(self):
        lines = [
            _tool_use(0, "a", "Bash", {"command": "ls"}),
            _tool_result("a", "file1\nfile2", False),
        ]
        calls = L.pair_calls(lines)
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertEqual(c["id"], "a")
        self.assertEqual(c["name"], "Bash")
        self.assertEqual(c["result_text"], "file1\nfile2")
        self.assertFalse(c["is_error"])
        self.assertEqual(c["index"], 0)

    def test_call_with_no_result_keeps_none(self):
        calls = L.pair_calls([_tool_use(0, "a", "Read", {"file_path": "/x"})])
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["result_text"])

    def test_result_with_no_call_is_ignored(self):
        self.assertEqual(L.pair_calls([_tool_result("z", "orphan")]), [])

    def test_malformed_lines_are_skipped(self):
        lines = [None, "nope", 42, _tool_use(3, "a", "Bash", {})]
        self.assertEqual(len(L.pair_calls(lines)), 1)

    def test_hostile_input_refused(self):
        for bad in (None, "not a list", 123, {"a": 1}, b"\xff\xfe"):
            with self.assertRaises(ValueError):
                L.pair_calls(bad)


class IsFailedTests(unittest.TestCase):
    def test_error_flag_is_failure(self):
        self.assertTrue(L.is_failed({"is_error": True, "result_text": "ok"}))

    def test_failure_marker_in_text_is_failure(self):
        for marker in ("Traceback", "REFUSED", "FAILED", "fatal:",
                       "denied", "exit code 1", "Exit code"):
            self.assertTrue(
                L.is_failed({"is_error": False, "result_text": marker}),
                "marker %r should count" % marker,
            )

    def test_none_result_is_not_failure(self):
        self.assertFalse(L.is_failed({"is_error": False, "result_text": None}))

    def test_clean_result_is_not_failure(self):
        self.assertFalse(L.is_failed({"is_error": False, "result_text": "ok"}))

    def test_hostile_input_refused(self):
        for bad in (None, "x", 3, []):
            with self.assertRaises(ValueError):
                L.is_failed(bad)


class FingerprintTests(unittest.TestCase):
    def test_whitespace_folded(self):
        a = {"name": "Bash", "input": {"command": "echo  1"}}
        b = {"name": "Bash", "input": {"command": "echo 1"}}
        self.assertEqual(L.fingerprint(a), L.fingerprint(b))

    def test_temp_paths_and_numbers_masked(self):
        a = {"name": "Bash",
             "input": {"command": "echo /tmp/abc 12345 1600000000"}}
        b = {"name": "Bash",
             "input": {"command": "echo /tmp/xyz 67890 1600000001"}}
        self.assertEqual(L.fingerprint(a), L.fingerprint(b))

    def test_different_commands_differ(self):
        a = {"name": "Bash", "input": {"command": "ls"}}
        b = {"name": "Bash", "input": {"command": "pwd"}}
        self.assertNotEqual(L.fingerprint(a), L.fingerprint(b))

    def test_hostile_input_refused(self):
        for bad in (None, "x", 3, []):
            with self.assertRaises(ValueError):
                L.fingerprint(bad)

    def test_non_utf8_bytes_still_returns_a_string(self):
        got = L.fingerprint({"name": "X", "input": {"k": b"\xff\xfe"}})
        self.assertIsInstance(got, str)
        self.assertEqual(len(got), 64)

    def test_nan_input_still_returns_a_string(self):
        got = L.fingerprint({"name": "X", "input": {"k": float("nan")}})
        self.assertIsInstance(got, str)
        self.assertEqual(len(got), 64)


class BuildLedgerTests(unittest.TestCase):
    def test_fifty_repeats_collapse_to_one_row(self):
        lines = []
        for i in range(50):
            cid = "c%d" % i
            lines.append(_tool_use(i * 2, cid, "Bash", {"command": "false"}))
            lines.append(_tool_result(cid, "exit code 1", True))
        ledger = L.build_ledger(lines)
        self.assertEqual(len(ledger["failed"]), 1)
        self.assertEqual(ledger["failed"][0]["count"], 50)
        self.assertEqual(ledger["cut"], 0)

    def test_over_budget_cuts_oldest_and_reports_count(self):
        lines = []
        for i in range(45):
            cid = "c%d" % i
            lines.append(_tool_use(i * 2, cid, "Bash",
                                   {"command": "cmd%d" % i}))
            lines.append(_tool_result(cid, "FAILED", True))
        ledger = L.build_ledger(lines, max_attempts=10)
        self.assertEqual(len(ledger["failed"]), 10)
        self.assertEqual(ledger["cut"], 35)
        self.assertEqual(ledger["failed"][0]["last_index"], 88)

    def test_why_is_last_nonblank_line_cut_to_limit(self):
        long_line = "x" * 500
        lines = [
            _tool_use(0, "c", "Bash", {"command": "x"}),
            _tool_result("c", "ok\n" + long_line, True),
        ]
        ledger = L.build_ledger(lines)
        self.assertEqual(len(ledger["failed"]), 1)
        self.assertEqual(len(ledger["failed"][0]["why"]), L.MAX_DETAIL_CHARS)

    def test_call_with_no_result_is_unfinished_not_failed(self):
        lines = [_tool_use(0, "c", "Bash", {"command": "x"})]
        ledger = L.build_ledger(lines)
        self.assertEqual(ledger["failed"], [])
        self.assertEqual(len(ledger["unfinished"]), 1)

    def test_failure_then_success_marks_later_succeeded(self):
        lines = [
            _tool_use(0, "c1", "Bash", {"command": "x"}),
            _tool_result("c1", "FAILED", True),
            _tool_use(2, "c2", "Bash", {"command": "x"}),
            _tool_result("c2", "ok", False),
        ]
        ledger = L.build_ledger(lines)
        self.assertEqual(len(ledger["failed"]), 1)
        self.assertTrue(ledger["failed"][0]["later_succeeded"])

    def test_empty_transcript_gives_empty_ledger(self):
        ledger = L.build_ledger([])
        self.assertEqual(ledger["failed"], [])
        self.assertEqual(ledger["unfinished"], [])
        self.assertEqual(ledger["cut"], 0)

    def test_hostile_input_refused(self):
        for bad in (None, "x", 12, {"a": 1}, b"ab"):
            with self.assertRaises(ValueError):
                L.build_ledger(bad)
        with self.assertRaises(ValueError):
            L.build_ledger([], max_attempts=True)
        with self.assertRaises(ValueError):
            L.build_ledger([], max_attempts=-1)
        with self.assertRaises(ValueError):
            L.build_ledger([], max_attempts="10")


class RenderLedgerTests(unittest.TestCase):
    def test_empty_ledger_never_absent(self):
        out = L.render_ledger({"failed": [], "unfinished": [], "cut": 0})
        self.assertIn("Already tried and failed", out)
        self.assertIn("none were recorded", out)

    def test_row_shows_what_why_count_and_later(self):
        ledger = {
            "failed": [
                {
                    "fingerprint": "x",
                    "what": "Bash: false",
                    "why": "exit code 1",
                    "count": 3,
                    "last_index": 5,
                    "later_succeeded": True,
                }
            ],
            "unfinished": [],
            "cut": 0,
        }
        out = L.render_ledger(ledger)
        self.assertIn("Bash: false", out)
        self.assertIn("failed 3", out)
        self.assertIn("exit code 1", out)
        self.assertIn("later succeeded", out)

    def test_hostile_input_refused(self):
        for bad in (None, "x", [], 3, b"ab"):
            with self.assertRaises(ValueError):
                L.render_ledger(bad)


class RepeatRateTests(unittest.TestCase):
    def test_repeat_rate_counts_repeats(self):
        fp = L.fingerprint({"name": "Bash", "input": {"command": "x"}})
        ledger = {"failed": [{"fingerprint": fp}]}
        later = [
            _tool_use(0, "c", "Bash", {"command": "x"}),
            _tool_result("c", "ok", False),
        ]
        res = L.repeat_rate(ledger, later)
        self.assertEqual(res["failed_before"], 1)
        self.assertEqual(res["repeated_after"], 1)
        self.assertEqual(res["rate"], 1.0)

    def test_no_failed_rows_gives_zero_rate(self):
        res = L.repeat_rate({"failed": []}, [])
        self.assertEqual(res, {"failed_before": 0, "repeated_after": 0,
                                "rate": 0.0})

    def test_no_repeat_gives_zero_rate(self):
        fp = L.fingerprint({"name": "Bash", "input": {"command": "x"}})
        ledger = {"failed": [{"fingerprint": fp}]}
        later = [
            _tool_use(0, "c", "Bash", {"command": "y"}),
            _tool_result("c", "ok", False),
        ]
        res = L.repeat_rate(ledger, later)
        self.assertEqual(res["failed_before"], 1)
        self.assertEqual(res["repeated_after"], 0)
        self.assertEqual(res["rate"], 0.0)

    def test_hostile_input_refused(self):
        with self.assertRaises(ValueError):
            L.repeat_rate(None, [])
        with self.assertRaises(ValueError):
            L.repeat_rate({}, None)
        with self.assertRaises(ValueError):
            L.repeat_rate("x", [])
        with self.assertRaises(ValueError):
            L.repeat_rate({}, b"ab")


if __name__ == "__main__":
    unittest.main()
