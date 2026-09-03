import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
FIXTURES = os.path.join(ROOT, "tools", "fixtures", "coldstart")

import coldstart_parse


class ParseTranscript(unittest.TestCase):
    def test_completed_run_reports_all_five_metrics(self):
        m = coldstart_parse.parse_transcript(os.path.join(FIXTURES, "completed.jsonl"))
        self.assertEqual(m["outcome"], "ok")
        self.assertTrue(m["completed"])
        self.assertEqual(m["commands_typed"], 2)
        self.assertEqual(m["internal_docs_opened"], 1)
        self.assertEqual(m["turns_to_first_artifact"], 3)
        self.assertGreater(m["context_bytes_at_start"], 0)

    def test_abandoned_run_is_not_completed_and_names_why(self):
        m = coldstart_parse.parse_transcript(os.path.join(FIXTURES, "abandoned.jsonl"))
        self.assertFalse(m["completed"])
        self.assertIn("max_turns", m["outcome"])

    def test_unparseable_transcript_raises_rather_than_reporting_zeroes(self):
        bad = os.path.join(FIXTURES, "not-a-file.jsonl")
        with self.assertRaises(OSError):
            coldstart_parse.parse_transcript(bad)




class HookOutputFieldNames(unittest.TestCase):
    """The field names are pinned against a live probe of the CLI's
    stream-json output taken 2026-08-11, because the first version of this
    parser summed `hook_output`, a key that probe contains ZERO times. The
    metric therefore read 0 on every real run: a perfect score on the exact
    weakness it exists to expose. The fixtures encoded the same wrong key, so
    the suite passed while production measured nothing, which is why these
    cases assert the SHAPE rather than trusting a fixture somebody wrote from
    the same belief as the code."""

    def _one(self, event):
        import tempfile, json as _json
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(_json.dumps(event) + "\n")
            fh.write(_json.dumps({"type": "result", "is_error": False}) + "\n")
            path = fh.name
        try:
            return coldstart_parse.parse_transcript(path)["context_bytes_at_start"]
        finally:
            os.unlink(path)

    def test_output_is_the_field_that_counts(self):
        self.assertEqual(self._one({"type": "system", "output": "x" * 40}), 40)

    def test_stdout_and_stderr_are_the_fallback_when_output_is_absent(self):
        self.assertEqual(
            self._one({"type": "system", "stdout": "x" * 30, "stderr": "y" * 5}), 35)

    def test_the_split_is_not_added_to_output(self):
        """The probe carries the same content in both spellings, so summing
        all three would report roughly double the real context tax."""
        self.assertEqual(
            self._one({"type": "system", "output": "x" * 40,
                       "stdout": "x" * 40, "stderr": ""}), 40)

    def test_the_key_that_shipped_the_defect_reads_zero(self):
        """Calibration: the exact shape that produced the perfect score. If
        someone reintroduces `hook_output` as the source, this stays 0 and
        the case above goes red instead of the metric quietly lying."""
        self.assertEqual(self._one({"type": "system", "hook_output": "x" * 99}), 0)


if __name__ == "__main__":
    unittest.main()
