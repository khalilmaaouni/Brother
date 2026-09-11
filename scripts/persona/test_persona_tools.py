"""Unit tests for scripts/persona/*.py, over a temp evidence dir the test
builds itself. Never touches the real evidence dir; asserts nothing is
written there when a test forgets to override it.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import tally  # noqa: E402
import gen_briefs  # noqa: E402
import judge  # noqa: E402


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class TallyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="persona-tally-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def results_path(self):
        return os.path.join(self.tmp, "transcripts", "results.jsonl")

    def test_no_data_on_empty_dir(self):
        code = tally.main(["--evidence-dir", self.tmp])
        self.assertEqual(code, 2)

    def test_exit_0_at_or_above_target(self):
        rows = [{"scenario": "S%d" % i, "round": 1, "verdict": "PASS", "trust_after": 4} for i in range(36)]
        rows += [{"scenario": "S%d" % i, "round": 1, "verdict": "FAIL", "trust_after": 1} for i in range(36, 40)]
        write_jsonl(self.results_path(), rows)
        code = tally.main(["--evidence-dir", self.tmp])
        self.assertEqual(code, 0)

    def test_exit_1_below_target(self):
        rows = [{"scenario": "S1", "round": 1, "verdict": "FAIL", "trust_after": 1}]
        write_jsonl(self.results_path(), rows)
        code = tally.main(["--evidence-dir", self.tmp])
        self.assertEqual(code, 1)

    def test_installed_reports_regression_and_forces_exit_1(self):
        # S1: PASSed round 1, PASSes again round 2 (steady PASS, no regression).
        # S2: PASSed round 1, FAILs round 2 (regression).
        rows = [
            {"scenario": "S1", "round": 1, "verdict": "PASS", "trust_after": 4},
            {"scenario": "S2", "round": 1, "verdict": "PASS", "trust_after": 4},
            {"scenario": "S1", "round": 2, "verdict": "PASS", "trust_after": 4},
            {"scenario": "S2", "round": 2, "verdict": "FAIL", "trust_after": 1},
        ]
        # pad to a base pass rate that would otherwise be exit 0, to prove the
        # regression alone forces the exit code to 1.
        rows += [{"scenario": "S%d" % i, "round": 1, "verdict": "PASS", "trust_after": 4} for i in range(3, 39)]
        write_jsonl(self.results_path(), rows)
        out_path = os.path.join(self.tmp, "out.txt")
        old_stdout = sys.stdout
        with open(out_path, "w") as fh:
            sys.stdout = fh
            try:
                code = tally.main(["--evidence-dir", self.tmp, "--installed"])
            finally:
                sys.stdout = old_stdout
        with open(out_path) as fh:
            output = fh.read()
        self.assertEqual(code, 1)
        self.assertIn("REGRESSION: S2", output)
        self.assertNotIn("REGRESSION: S1", output)
        self.assertIn("tree: NO-DATA (no PINNED-TREE file)", output)

    def test_installed_reads_pinned_tree(self):
        rows = [{"scenario": "S1", "round": 1, "verdict": "PASS", "trust_after": 4}]
        write_jsonl(self.results_path(), rows)
        with open(os.path.join(self.tmp, "PINNED-TREE"), "w") as fh:
            fh.write("/some/tree/path\nabc1234\n")
        out_path = os.path.join(self.tmp, "out.txt")
        old_stdout = sys.stdout
        with open(out_path, "w") as fh:
            sys.stdout = fh
            try:
                tally.main(["--evidence-dir", self.tmp, "--installed"])
            finally:
                sys.stdout = old_stdout
        with open(out_path) as fh:
            output = fh.read()
        self.assertIn("tree: /some/tree/path at abc1234", output)


class GenBriefsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="persona-briefs-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with open(os.path.join(D, "scenarios.json")) as fh:
            self.scenarios = json.load(fh)
        self.persona_ids = [p["id"] for p in self.scenarios["personas"]]

    def scenario_count(self, pid):
        return sum(1 for s in self.scenarios["scenarios"] if s["persona"] == pid)

    def test_default_skips_pass_scenarios(self):
        # Mark every scenario of the first persona as PASS; it should get no brief.
        pid = self.persona_ids[0]
        rows = [{"scenario": s["id"], "verdict": "PASS"} for s in self.scenarios["scenarios"] if s["persona"] == pid]
        write_jsonl(os.path.join(self.tmp, "transcripts", "results.jsonl"), rows)
        gen_briefs.main(["1", "FAKETREE", "--evidence-dir", self.tmp])
        brief_path = os.path.join(self.tmp, "brief-%s-r1.md" % pid)
        self.assertFalse(os.path.exists(brief_path))
        # A persona with no results at all still gets briefed (nothing is PASS).
        other = self.persona_ids[1]
        other_brief = os.path.join(self.tmp, "brief-%s-r1.md" % other)
        self.assertTrue(os.path.exists(other_brief))

    def test_all_flag_briefs_every_persona_every_scenario(self):
        pid = self.persona_ids[0]
        rows = [{"scenario": s["id"], "verdict": "PASS"} for s in self.scenarios["scenarios"] if s["persona"] == pid]
        write_jsonl(os.path.join(self.tmp, "transcripts", "results.jsonl"), rows)
        gen_briefs.main(["1", "FAKETREE", "--all", "--evidence-dir", self.tmp])
        for p in self.persona_ids:
            brief_path = os.path.join(self.tmp, "brief-%s-r1.md" % p)
            self.assertTrue(os.path.exists(brief_path), "missing brief for %s" % p)
            with open(brief_path) as fh:
                text = fh.read()
            scen_ids = [s["id"] for s in self.scenarios["scenarios"] if s["persona"] == p]
            for sid in scen_ids:
                self.assertIn(sid, text)


class EvidenceDirRoutingTests(unittest.TestCase):
    """--evidence-dir and PERSONA_EVIDENCE_DIR both route reads/writes; the
    default evidence dir is never touched by a test that sets either."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="persona-routing-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def default_dir_snapshot(self):
        default_dir = tally.DEFAULT_EVIDENCE_DIR
        if not os.path.isdir(default_dir):
            return None
        return set(os.listdir(default_dir))

    def test_flag_routes_reads_and_writes_away_from_default(self):
        before = self.default_dir_snapshot()
        write_jsonl(os.path.join(self.tmp, "transcripts", "results.jsonl"),
                    [{"scenario": "S1", "round": 1, "verdict": "PASS", "trust_after": 4}])
        code = tally.main(["--evidence-dir", self.tmp])
        self.assertEqual(code, 1)  # one PASS of 40 read from tmp, below the 36 target
        after = self.default_dir_snapshot()
        self.assertEqual(before, after, "the default evidence dir must not change")

    def test_env_var_default_routes_without_the_flag(self):
        old = os.environ.get("PERSONA_EVIDENCE_DIR")
        os.environ["PERSONA_EVIDENCE_DIR"] = self.tmp
        try:
            import importlib
            importlib.reload(tally)
            write_jsonl(os.path.join(self.tmp, "transcripts", "results.jsonl"),
                        [{"scenario": "S1", "round": 1, "verdict": "PASS", "trust_after": 4}])
            code = tally.main([])
            self.assertEqual(code, 1)  # read the tmp dir's one PASS-of-40, below target
        finally:
            if old is None:
                os.environ.pop("PERSONA_EVIDENCE_DIR", None)
            else:
                os.environ["PERSONA_EVIDENCE_DIR"] = old
            importlib.reload(tally)


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="persona-judge-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_no_data_when_no_results_file(self):
        # Out of scope: judge.py's network call to the Muse bridge. This
        # test only proves the NO-DATA short-circuit before any call is made.
        code = judge.main(["--evidence-dir", self.tmp])
        self.assertEqual(code, 2)


class JudgeCLITests(unittest.TestCase):
    """judge.py calls the headless Claude CLI via subprocess.run(judge.JUDGE_ARGV);
    every test here patches judge.subprocess.run so no real 'claude' process is
    ever launched, and no network call is made."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="persona-judgecli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.old_env = os.environ.get("PERSONA_PRIVATE_TERMS")
        self.addCleanup(self._restore_env)
        terms_path = os.path.join(self.tmp, "terms.txt")
        with open(terms_path, "w") as fh:
            fh.write("Longmadeupname\n")
        os.environ["PERSONA_PRIVATE_TERMS"] = terms_path

    def _restore_env(self):
        if self.old_env is None:
            os.environ.pop("PERSONA_PRIVATE_TERMS", None)
        else:
            os.environ["PERSONA_PRIVATE_TERMS"] = self.old_env

    def write_result(self, rows):
        write_jsonl(os.path.join(self.tmp, "transcripts", "results.jsonl"), rows)

    def envelope(self, verdict_dict, is_error=False, returncode=0):
        result_text = "" if verdict_dict is None else json.dumps(verdict_dict)
        fake = type("FakeCompletedProcess", (), {})()
        fake.stdout = json.dumps({"is_error": is_error, "result": result_text})
        fake.stderr = ""
        fake.returncode = returncode
        return fake

    def judgments(self):
        with open(os.path.join(self.tmp, "judgments.jsonl")) as fh:
            return [json.loads(line) for line in fh]

    def run_judge(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = judge.main(["--evidence-dir", self.tmp])
        return code

    def test_timeout_yields_no_data_and_next_scenario_still_judged(self):
        self.write_result([
            {"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"},
            {"scenario": "S2", "persona": "P1", "round": 1, "verdict": "PASS"},
        ])
        good = self.envelope({"verdict": "PASS", "trust_after": 5})
        with patch("judge.subprocess.run", side_effect=[subprocess.TimeoutExpired(cmd="claude", timeout=300), good]):
            code = self.run_judge()
        rows = self.judgments()
        self.assertEqual(rows[0]["scenario"], "S1")
        self.assertEqual(rows[0]["verdict"], "NO-DATA")
        self.assertIn("timed out", rows[0]["judge_error"])
        self.assertEqual(rows[1]["scenario"], "S2")
        self.assertEqual(rows[1]["verdict"], "PASS")
        self.assertEqual(code, 1)  # one of two scenarios failed to judge

    def test_claude_missing_yields_no_data(self):
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        with patch("judge.subprocess.run", side_effect=OSError("claude: command not found")):
            self.run_judge()
        rows = self.judgments()
        self.assertEqual(rows[0]["verdict"], "NO-DATA")
        self.assertIn("command not found", rows[0]["judge_error"])

    def test_is_error_true_yields_no_data(self):
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        bad = self.envelope({"whatever": 1}, is_error=True)
        with patch("judge.subprocess.run", return_value=bad):
            self.run_judge()
        rows = self.judgments()
        self.assertEqual(rows[0]["verdict"], "NO-DATA")
        self.assertIn("is_error", rows[0]["judge_error"])

    def test_a_nonzero_exit_is_no_data_even_with_a_verdict_on_stdout(self):
        """Review m2: the exit code is read, not ignored. A limit exits 1 with
        its reason on stderr or in the envelope; either way no judgment."""
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        limited = self.envelope(None, returncode=1)
        limited.stdout = ""
        limited.stderr = "You've hit your limit · resets 3pm"
        passed_but_failed = self.envelope({"verdict": "PASS", "trust_after": 5}, returncode=1)
        for fake, needle in ((limited, "hit your limit"), (passed_but_failed, "exited 1")):
            with open(os.path.join(self.tmp, "judgments.jsonl"), "w"):
                pass
            with patch("judge.subprocess.run", return_value=fake):
                code = self.run_judge()
            rows = self.judgments()
            self.assertEqual(rows[0]["verdict"], "NO-DATA", needle)
            self.assertIn("exited 1", rows[0]["judge_error"])
            self.assertIn(needle, rows[0]["judge_error"])
            self.assertEqual(code, 1)

    def test_empty_result_yields_no_data(self):
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        empty = self.envelope(None, is_error=False)
        with patch("judge.subprocess.run", return_value=empty):
            self.run_judge()
        rows = self.judgments()
        self.assertEqual(rows[0]["verdict"], "NO-DATA")
        self.assertIn("empty result", rows[0]["judge_error"])

    def test_an_answer_with_no_valid_verdict_is_no_data_not_a_judgment(self):
        """Measured live on 2026-09-11: the headless child answered one scenario
        with another harness's shape, {"ok": true, "reason": ...}, and the
        judge wrote it down as a judgment with no verdict and no error."""
        for answer in ({"ok": True, "reason": "a hook's answer, not a verdict"},
                       {"verdict": "MAYBE", "trust_after": 3}):
            self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
            if os.path.exists(os.path.join(self.tmp, "judgments.jsonl")):
                os.remove(os.path.join(self.tmp, "judgments.jsonl"))
            with patch("judge.subprocess.run", return_value=self.envelope(answer)):
                code = self.run_judge()
            rows = self.judgments()
            self.assertEqual(rows[0]["verdict"], "NO-DATA", answer)
            self.assertIn("verdict", rows[0]["judge_error"])
            self.assertEqual(code, 1, "a malformed answer is a failed judge call")

    def test_good_json_answer_parsed_into_verdict_record(self):
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        good = self.envelope({"verdict": "PASS", "trust_after": 5, "would_use_tomorrow": True})
        with patch("judge.subprocess.run", return_value=good):
            code = self.run_judge()
        rows = self.judgments()
        self.assertEqual(rows[0]["scenario"], "S1")
        self.assertEqual(rows[0]["persona"], "P1")
        self.assertEqual(rows[0]["verdict"], "PASS")
        self.assertEqual(rows[0]["trust_after"], 5)
        self.assertEqual(code, 0)

    def test_argv_carries_output_format_model_and_fallback_model(self):
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        good = self.envelope({"verdict": "PASS", "trust_after": 5})
        with patch("judge.subprocess.run", return_value=good) as m:
            self.run_judge()
        argv = m.call_args[0][0]
        self.assertIn("--output-format", argv)
        self.assertIn("json", argv)
        self.assertIn("--model", argv)
        self.assertIn("sonnet", argv)
        self.assertIn("--fallback-model", argv)
        self.assertIn("haiku", argv)

    def test_brief_sent_to_runner_is_the_scrubbed_transcript(self):
        T = os.path.join(self.tmp, "transcripts")
        os.makedirs(T, exist_ok=True)
        with open(os.path.join(T, "P1-S1.md"), "w") as fh:
            fh.write("session log mentioning Longmadeupname right here\n")
        self.write_result([{"scenario": "S1", "persona": "P1", "round": 1, "verdict": "PASS"}])
        good = self.envelope({"verdict": "PASS", "trust_after": 5})
        with patch("judge.subprocess.run", return_value=good) as m:
            self.run_judge()
        sent = m.call_args.kwargs["input"]
        self.assertNotIn("Longmadeupname", sent)
        self.assertIn("[client]", sent)


class PrivateTermsTests(unittest.TestCase):
    """The scrub list lives outside the repository (PERSONA_PRIVATE_TERMS,
    else ~/.brothersbe-private-names); judge.py never carries a literal
    client term. Made-up archetype terms only, never a real one."""

    SHORT_TERM = "ZQXV"           # <=5 chars: matched exactly, case sensitive
    LONG_TERM = "Longmadeupname"  # >5 chars: matched case insensitively

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="persona-privterms-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.old_env = os.environ.get("PERSONA_PRIVATE_TERMS")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.old_env is None:
            os.environ.pop("PERSONA_PRIVATE_TERMS", None)
        else:
            os.environ["PERSONA_PRIVATE_TERMS"] = self.old_env

    def write_terms_file(self):
        terms_path = os.path.join(self.tmp, "terms.txt")
        with open(terms_path, "w") as fh:
            fh.write("# a comment line, ignored\n\n%s\n%s\n" % (self.SHORT_TERM, self.LONG_TERM))
        os.environ["PERSONA_PRIVATE_TERMS"] = terms_path
        return terms_path

    def test_scrub_replaces_short_exact_and_long_case_insensitive(self):
        self.write_terms_file()
        terms, terms_path = judge.private_terms()
        self.assertEqual(terms, [self.SHORT_TERM, self.LONG_TERM])
        transcript = "seen %s here and LONGMADEUPNAME there, also lower %s" % (
            self.SHORT_TERM, self.LONG_TERM.lower())
        scrubbed = judge.scrub(transcript, terms)
        self.assertNotIn(self.SHORT_TERM, scrubbed)
        self.assertNotIn(self.LONG_TERM.lower(), scrubbed)
        self.assertNotIn("LONGMADEUPNAME", scrubbed)
        self.assertEqual(scrubbed.count("[client]"), 3)

    def test_scrub_short_term_is_case_sensitive(self):
        # A lowercase variant of the short (<=5 char) term must survive: only
        # exact-case matching applies below the five-character threshold.
        self.write_terms_file()
        terms, _ = judge.private_terms()
        scrubbed = judge.scrub(self.SHORT_TERM.lower(), terms)
        self.assertEqual(scrubbed, self.SHORT_TERM.lower())

    def test_main_exits_2_and_never_calls_bridge_when_terms_file_missing(self):
        os.environ["PERSONA_PRIVATE_TERMS"] = os.path.join(self.tmp, "does-not-exist.txt")
        write_jsonl(os.path.join(self.tmp, "transcripts", "results.jsonl"),
                    [{"scenario": "S1", "round": 1, "verdict": "PASS", "trust_after": 4}])
        with patch("judge.subprocess.run", side_effect=AssertionError("bridge must not be called")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = judge.main(["--evidence-dir", self.tmp])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA: private terms file not readable", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
