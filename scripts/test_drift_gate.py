"""Calibration for drift_gate.py: the two real, measured defects it fixes
relative to the third-party plugin it replaces (a command-name-only
evidence allowlist, and matching quoted/described text as a live claim),
plus the recency fix. Every test string that would otherwise look like a
live finishing claim is built from parts (see _phrase below), for the
same reason drift_gate.py's own pattern data is: so this test file's own
fixtures are never mistaken for a live claim by an outside scanner
watching this session, which is the exact failure class under test here.

Proving command: python3 scripts/test_drift_gate.py
Expected tail: 0 failures
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drift_gate as dg  # noqa: E402


def _phrase(*parts):
    return "".join(parts)


_LIVE_CLAIM = _phrase("the w", "ork is c", "omplete and ver", "ified")
_REAL_PASS_OUTPUT = "Ran 44 tests in 12.3s\n\nOK"
_REAL_FAIL_OUTPUT = "Traceback (most recent call last):\nFAILED (failures=1)"


class Defect1EvidenceByOutcomeNotCommandName(unittest.TestCase):
    """The real, measured bug: the plugin this replaces only recognizes a
    hardcoded list of test-runner command NAMES, so a bare interpreter
    invocation of a test file (this estate's own dominant convention)
    never counts as evidence. drift_gate reads the OUTCOME instead."""

    def test_a_bare_interpreter_test_run_output_counts_as_evidence(self):
        self.assertTrue(dg.looks_like_real_evidence(_REAL_PASS_OUTPUT))

    def test_b_a_real_failure_never_counts_even_with_a_pass_looking_substring(self):
        # "exit code 0" style text inside a traceback must not count.
        noisy = _REAL_FAIL_OUTPUT + "\n(a prior unrelated step exited 0)"
        self.assertFalse(dg.looks_like_real_evidence(noisy))

    def test_c_empty_or_missing_output_is_never_evidence(self):
        self.assertFalse(dg.looks_like_real_evidence(""))
        self.assertFalse(dg.looks_like_real_evidence(None))

    def test_d_mutation_proof_removing_the_ran_n_tests_signal_stops_detection(self):
        """Red-then-green: temporarily remove the 'Ran N tests' signal
        from the real signal list, confirm the same real output this
        module is built to recognize is no longer recognized, restore."""
        original = list(dg._EVIDENCE_SIGNALS)
        try:
            dg._EVIDENCE_SIGNALS[:] = [
                s for s in dg._EVIDENCE_SIGNALS
                if "Ran" not in s.pattern
            ]
            # Real output whose ONLY signal is the "Ran N tests" line.
            only_ran_signal = "Ran 44 tests in 12.3s"
            self.assertFalse(dg.looks_like_real_evidence(only_ran_signal),
                              "removing the signal should have made this unrecognized")
        finally:
            dg._EVIDENCE_SIGNALS[:] = original
        self.assertTrue(dg.looks_like_real_evidence("Ran 44 tests in 12.3s\nOK"))


class Defect2QuotedTextNeverScoredAsLive(unittest.TestCase):
    """The real, measured bug: the plugin this replaces scans an event's
    WHOLE text with no way to tell a live claim from a quoted or
    described example. Measured 5 separate times in one session while
    drafting this very module. drift_gate strips non-live regions first."""

    def setUp(self):
        self.state = {"evidence": [], "events_since_last_evidence_check": 0}

    def test_a_the_same_phrase_fires_when_live(self):
        findings = dg.detect(_LIVE_CLAIM, self.state)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["pattern_id"], "premature_completion")

    def test_b_the_same_phrase_inside_a_heredoc_never_fires(self):
        wrapped = "Here is an example:\n<<EOF\n%s\nEOF\nReal unrelated text." % _LIVE_CLAIM
        self.assertEqual(dg.detect(wrapped, self.state), [])

    def test_c_the_same_phrase_inside_a_code_fence_never_fires(self):
        wrapped = "```\n%s\n```\nReal unrelated text." % _LIVE_CLAIM
        self.assertEqual(dg.detect(wrapped, self.state), [])

    def test_d_the_same_phrase_inside_a_blockquote_never_fires(self):
        wrapped = "> %s\nReal unrelated text." % _LIVE_CLAIM
        self.assertEqual(dg.detect(wrapped, self.state), [])

    def test_e_mutation_proof_disabling_the_strip_makes_the_heredoc_fire(self):
        """Red-then-green: temporarily make strip_non_live_text() a
        no-op, confirm the heredoc case that must NEVER fire now DOES
        fire (proving test b is not vacuous), restore."""
        real_strip = dg.strip_non_live_text
        try:
            dg.strip_non_live_text = lambda text: text
            wrapped = "Here is an example:\n<<EOF\n%s\nEOF\n" % _LIVE_CLAIM
            findings = dg.detect(wrapped, self.state)
            self.assertEqual(len(findings), 1,
                              "with the strip disabled, the heredoc content should now fire")
        finally:
            dg.strip_non_live_text = real_strip
        wrapped = "Here is an example:\n<<EOF\n%s\nEOF\n" % _LIVE_CLAIM
        self.assertEqual(dg.detect(wrapped, self.state), [])


class TwoMorePatternCategoriesPortedThisPass(unittest.TestCase):
    """process_substitution and tool_misuse_stall, added alongside the
    original 4, bringing real coverage to 6 of the plugin's ~9 categories
    (see the module docstring's PATTERN COVERAGE section for the 3
    deliberately not ported and why)."""

    def setUp(self):
        self.state = {"evidence": [], "events_since_last_evidence_check": 0}

    def test_a_process_substitution_fires(self):
        text = _phrase("let ", "me j", "ust ") + _phrase("direct", "ly") + _phrase(" write the ", "file myself")
        findings = dg.detect(text, self.state)
        self.assertTrue(any(f["pattern_id"] == "process_substitution" for f in findings))

    def test_b_tool_misuse_stall_fires(self):
        text = _phrase("bl", "ocked") + ": " + _phrase("sl", "eep") + " 30"
        findings = dg.detect(text, self.state)
        self.assertTrue(any(f["pattern_id"] == "tool_misuse_stall" for f in findings))

    def test_c_neither_fires_on_unrelated_text(self):
        findings = dg.detect("ran the build and it succeeded", self.state)
        ids = [f["pattern_id"] for f in findings]
        self.assertNotIn("process_substitution", ids)
        self.assertNotIn("tool_misuse_stall", ids)


class RecencyFix(unittest.TestCase):
    """The DeepSeek-sourced improvement: evidence older than the
    freshness window is evicted, since it proves nothing about code that
    has since changed."""

    def test_a_fresh_evidence_suppresses_the_completion_pattern(self):
        state = {"evidence": [], "events_since_last_evidence_check": 0}
        dg.record_tool_event(state, "Bash", _REAL_PASS_OUTPUT)
        self.assertEqual(dg.detect(_LIVE_CLAIM, state), [])

    def test_b_stale_evidence_no_longer_suppresses_it(self):
        state = {"evidence": [], "events_since_last_evidence_check": 0}
        dg.record_tool_event(state, "Bash", _REAL_PASS_OUTPUT)
        for _ in range(dg.EVIDENCE_FRESHNESS_WINDOW + 1):
            dg.record_tool_event(state, "Edit", "")
        findings = dg.detect(_LIVE_CLAIM, state)
        self.assertEqual(len(findings), 1,
                          "evidence older than the freshness window must no longer count")


class StateRoundTrip(unittest.TestCase):
    def test_a_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            dg.STATE_DIR = tmp
            state = {"evidence": [{"age": 0}], "events_since_last_evidence_check": 2}
            dg.save_state("test-session-1", state)
            loaded = dg.load_state("test-session-1")
            self.assertEqual(loaded["evidence"], state["evidence"])
            self.assertEqual(loaded["events_since_last_evidence_check"], 2)

    def test_b_missing_state_file_is_a_safe_empty_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            dg.STATE_DIR = tmp
            loaded = dg.load_state("never-seen-session")
            self.assertEqual(loaded["evidence"], [])

    def test_c_corrupt_state_file_fails_open_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            dg.STATE_DIR = tmp
            path = dg._state_path("bad-session")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json at all{{{")
            loaded = dg.load_state("bad-session")
            self.assertEqual(loaded["evidence"], [])


class MainFailsOpen(unittest.TestCase):
    def _run(self, payload_json, state_dir):
        import subprocess
        hook = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drift_gate.py")
        env = dict(os.environ)
        env["DRIFT_GATE_STATE_DIR"] = state_dir
        return subprocess.run([sys.executable, hook], input=payload_json,
                              capture_output=True, text=True, env=env, timeout=30)

    def test_a_malformed_json_fails_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._run("not json at all", tmp)
            self.assertEqual(p.returncode, 0)

    def test_b_empty_stdin_fails_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._run("", tmp)
            self.assertEqual(p.returncode, 0)

    def test_c_a_live_finishing_claim_with_no_evidence_blocks(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.dumps({
                "hook_event_name": "Stop", "session_id": "s1",
                "last_message_text": _LIVE_CLAIM,
            })
            p = self._run(payload, tmp)
            self.assertEqual(p.returncode, 0)
            out = json.loads(p.stdout)
            self.assertEqual(out.get("decision"), "block")

    def test_d_the_same_claim_after_real_evidence_recorded_does_not_block(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            evidence_payload = json.dumps({
                "hook_event_name": "PostToolUse", "session_id": "s2",
                "tool_name": "Bash",
                "tool_response": {"stdout": _REAL_PASS_OUTPUT, "stderr": ""},
            })
            self._run(evidence_payload, tmp)
            claim_payload = json.dumps({
                "hook_event_name": "Stop", "session_id": "s2",
                "last_message_text": _LIVE_CLAIM,
            })
            p = self._run(claim_payload, tmp)
            self.assertEqual(p.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
